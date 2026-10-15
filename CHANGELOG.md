# Sequoia-X V3 变更记录

> 生成日期: 2026-06-10
> 会话: 代码审查 + 修复 + 功能增强

---

## 一、数据问题诊断与修复

### 发现的问题
- `sync_today_bulk()` 中 `DELETE FROM stock_daily WHERE date = ?` 删除某日 ALL 股票数据
- 然后只重写 volume>0 的 ~2126 只，其余 ~3072 只永久丢失数据
- 导致数据库 5.28~6.9 期间仅 40.8% 股票有数据

### 修复
1. **DELETE 改为精准删除** (`data/engine.py`)
   - 旧: `DELETE FROM stock_daily WHERE date = ?`
   - 新: `DELETE FROM stock_daily WHERE date = ? AND symbol IN (...)`
2. **多进程"用户未登录"重试** (`data/engine.py` - `_bs_fetch_batch`)
   - 检测到"未登录"错误时自动 logout+login 重试
   - 188 只失败降至 5 只
3. **补全缺失的 Pool 导入** (`data/engine.py` - `backfill_parallel`)
4. **_is_trading_day() 改进** (`main.py`)
   - 旧: 仅判断周末
   - 新: baostock query_trade_dates() API，含节假日判断
5. **数据恢复**: 运行 --parallel-backfill，数据覆盖从 40.8% 恢复到 99.8%

---

## 二、代码修复

### backtest/engine.py
- 完整重写 `run()` 方法，清除 `_rewrite_engine.py` 造成的残缺代码
- 预加载全市场数据到内存缓存加速
- 基准曲线改用真实沪深300/中证500数据
- 持仓市值准确计算

### data/engine.py
- 清除末尾游离的 `return count`
- 清除 BOM (U+FEFF) 导致的语法错误

### 废弃文件删除
- `_rewrite_engine.py` (会破坏回测引擎)
- `intersection.py`, `print_results.py`, `show_intersection.py`

---

## 三、新增模块

### signal/ - 交易信号模块
- `models.py`: TradeSignal 数据类
  - 买入区间 (entry_zone_low/entry_zone_high)
  - 止损价 / 目标价1/2
  - 建议仓位 / ATR / 置信度
  - risk_reward_ratio() 盈亏比计算
  - summary() 一行摘要
- `generator.py`: SignalGenerator
  - 基于均线/布林带/ATR 生成买入区间
  - 多策略置信度计算
  - 接入 technical.py 支撑压力分析
  - 自动填充股票名称

### recommender/ - 推荐报告模块
- `reporter.py`: ReportGenerator
  - 终端打印结构化报告
  - 飞书卡片推送 Top 10

### ensemble/ - 多策略融合评分
- `consensus.py`: ConsensusEngine
  - 策略数量加分 (每策略+15)
  - 策略类型差异加分 (趋势+反转双类型确认+15)
  - 策略权重加权 (趋势类1.0, RPS 0.9, 反转类0.7~0.8)
  - 反转类小众策略额外加分

### utils/ - 工具模块
- `name_resolver.py`: NameResolver
  - 一次性拉取 baostock 全量股票名称
  - LRU 缓存，全局单例

---

## 四、main.py 重构

### 日常流程架构

```
python main.py

阶段 A: 全6策略并行
  MaVolume + TurtleTrade + HighTightFlag
  + LimitUpShakeout + UptrendLimitDown + RpsBreakout
  → 各策略独立的飞书推送

阶段 B: 多策略融合评分 (ConsensusEngine)
  → 输出 Top 50 排名
  → 保存 Consensus_YYYYMMDD.csv
  → 推送 Top 30 多策略确认股

阶段 C: 精选 Top 5 详细分析
  → 技术面评分
  → 多因子评分 (PE/PB/ROE)
  → 飞书推送详细分析卡片

阶段 D: 结构化交易信号 (SignalGenerator + ReportGenerator)
  → 终端打印推荐报告
  → 飞书推送 Top 10 推荐
```

### CLI 参数
| 参数 | 功能 | 频率 |
|---|---|---|
| (无参数) | 日常增量更新+选股+推荐 | 每天 |
| --backfill | 串行回填历史数据 | 首次/数据损坏 |
| --parallel-backfill | 并行回填 (8进程) | 首次/数据损坏 |
| --rating | 全市场7策略评级 | 按需 |
| --backtest | 策略历史回测 | 按需 |
| --regime | 市场状态检测 | 按需 |
| --factors TOP20 | 多因子选股 | 按需 |
| --install-schedule | 安装每日21:00定时任务 | 一次性 |

---

## 五、待办 (后续阶段)

- [ ] 阶段二续: 改造各策略 run() 直接返回 TradeSignal
- [~] 阶段三续: 多周期确认过滤假突破 —— **周线过滤已实现**（`signal/generator.py`，
  ±0.10 加减分）；60分钟线未做
- [ ] 阶段四: 申万一级行业轮动评分
- [ ] 阶段四: 实盘模拟记录+命中率追踪
- [ ] 阶段五: 回测驱动的参数自适应优化


## 手机端数据桥接 — 腾讯云COS同步

### 新增功能
1. **COS云端同步** (`sequoia_x/cloud.py`)
   - 新增云端同步模块，通过腾讯云COS XML API实现推荐结果上传
   - 使用HMAC-SHA1签名认证，无需额外SDK依赖
   - 支持自定义存储桶、地域、路径

2. **Settings配置** (`sequoia_x/core/config.py`)
   - 新增COS相关配置字段: cos_bucket, cos_region, cos_secret_id, cos_secret_key, cos_path, cos_enabled
   - 从环境变量或.env文件自动加载

3. **main.py集成**
   - D阶段尾部增加COS上传逻辑
   - 仅在cos_enabled=true且cos_bucket非空时执行
   - 上传Top 20推荐股票的结构化数据（含买卖区间、止损止盈、置信度等）

### 配置方式
在.env中添加以下参数：
- COS_BUCKET=存储桶名称
- COS_REGION=地域（默认ap-guangzhou）
- COS_SECRET_ID=腾讯云API密钥ID
- COS_SECRET_KEY=腾讯云API密钥Key
- COS_PATH=对象路径（默认recommend/today.json）
- COS_ENABLED=true（启用同步）

---

# 2026-07 ~ 2026-09 更新

> 生成日期: 2026-09-28
> 会话: 文档对齐 + 推送 GitHub

## 一、因子方法论落地（2026-07-28）

- 因子预处理：缺失值填充、去极值、截面标准化
- IC/IR 报告：逐策略滚动 IC，导出 `output/ic_history.json`
- 分组排序 + IR 加权 + 健康度评分
- 基本面因子接入，策略简化，因子正交化
- 8 项优化完成 7 项（详见 README「动态权重」章节）

## 二、IC 信号质量监控系统（2026-07-04 ~ 07-07）

- 新增 `sequoia_x/signal/quality.py`：ICTracker
  - 每日记录各策略原始分快照，20 日后回看真实收益计算 IC/IR
  - `apply_ir_weights()`：IR≤0 的策略权重归零，正 IR 策略按比例分配
  - `_apply_ic_decay_weights()`：近期 IC 衰减的策略本轮即降权
- 修正：IC 追踪改喂**原始分**（此前误喂加权总分，导致 IC 失真）
- 手机端阈值校准：S/A/B/C ≥1.5/≥1.0/≥0.5（对齐全市场评分分布）

## 三、评分权重重配（2026-08-18 ~ 08-30）

全量因子验证（2024-01~2026-08，5206只/584交易日）后的修正：

- **权重真正进评分**：此前策略权重硬编码 0.25 未被使用，现由 `config.weight_*` 驱动
- **均值回归主导**：动量/趋势类横截面 IC 全负，反转类唯一正超额
  - 新默认权重：meanReversion 0.20 / fundamental 0.20 / turtle 0.125 / kdj 0.125 / maCross 0.05 / atr 0.05，其余归零
- **REGIME_BIAS 重校准**：熊市 reversal 权重升至 0.68，趋势/动量趋零
- **IR 兜底修复**：正 IR 策略不足 3 个时保留市场状态权重，不再回退等权（避免负 IC 策略被等权恢复）
- **趋势软过滤**：趋势未通过时评级上限降为 B，不再直接丢弃
- **负资产惩罚**、**截面标准化** 加入评分

## 四、回测引擎修复（2026-08-18）

- 现金记账修复（此前权益虚高）
- 涨跌停 + 印花税计入成交约束
- ST 过滤
- 复权跳变防护：151 只异常股票被识别

## 五、市场状态识别改造（2026-07-07，08-21 修复）

- 判定方法改为 **价格 vs MA120 ±5%** + ATR 趋势强度 + 波动率修正
- 策略类别权重统一等权（后续 08-30 由 REGIME_BIAS 取代）
- 修复 000905 基准指数前缀映射 bug（此前市场状态失真）

## 六、数据层健壮性（2026-08-19 ~ 09-01）

- **baostock 断连修复**：`_fetch_financial` 复用单连接被踢线后财务因子（25%权重）静默丢失 → 加重连 + 0.15s 节流；factors 模块同类问题一并修复
- **全量重刷限流修复**：`refresh_history` 5473 只零失败跑完
- **efinance 财务数据接入**（2026-09-01）：fundamental 改 efinance 全市场批量拉取（约 8.6s/5566只 + 磁盘缓存），不可用时回退 baostock 逐股查询
- **SQLite 连接泄漏**：16 处连接改为显式关闭
- **COS 大文件上传**：修复 Clash 代理断流（trust_env）

## 七、手机端完善（2026-08-18 ~ 08-30）

- COS 云备份自选股 + 设置页云端恢复按钮（重装后不丢失）
- 自选卡 PC 评级对齐（`mergePcData`）
- 真 K 线蜡烛图、详情页 8 分区瘦身
- 首页持续上榜天数、返回保持滚动位置、C 级观望
- 决策卡展示盈亏比、置信度/仓位/买入依据
- 魔法数字统一到 `AppTheme` 常量

## 八、推荐可靠性测评（2026-09-04）

新增 `run_rec_eval.py` + `run_rec_realcheck.py` 互为交叉校验：

- **历史重建**（`run_rec_eval.py`）：用当前评分配置回放 2024-07~2026-08 每日 Top15，算什么未来 5/10/20 日收益
  - 结论：5日 +1.0%（超额 +0.35%），20日 +2.2%（超额 -0.48%）
- **真实快照**（`run_rec_realcheck.py`）：对 `output/rating_result_*.json` 重建 Top15，反映"当时实际推荐"
  - 结论：旧版快照出现 -1.4% / -10% 的大亏
- 新增 `sequoia_x/eval/`：无前视的历史回放工具（逐日评分重建 / regime 序列）
- 策略分镜像 Spearman = 1.0，验证重建口径与生产一致

## 九、已知问题

- `main.py` 日常流程中策略评分不接市场状态权重（`ConsensusEngine` 按策略数量与类型打分）；
  实际评分权重以 `--rating` 路径的 `REGIME_BIAS` 为准。原先那段「算完只打日志」的
  `strategy_regime_weights` 死代码已于 2026-10-11 删除。

  > 以下三项已于 2026-10-11 修复，见文末「2026-10 更新」：
  > ~~`REGIME_STRATEGY_BIAS` 等权表与 `REGIME_BIAS` 口径不一致~~、
  > ~~`efinance` 未声明在 `pyproject.toml`~~、
  > ~~`--mootdx-backfill` 静默空转~~。

---

# 2026-10 更新

## 一、修复 `--rating` 报 `WinError 10054`

根因是 efinance 全市场财务拉取在报告期刚切换（尚无公司披露）时抛错并被永久禁用，
导致每只过趋势的股票都回退 baostock 逐股查财务，请求量暴涨触发服务端踢线；
而 baostock 的 `send_msg` 无接收超时、异常被吞掉只打印，调用方拿到 `None`
无法感知，分页读取还会静默截断。

- 新增 `utils/baostock_guard.py`：在 `send_msg` 这一唯一出口加 30s 接收超时 +
  断线 `logout`/`login` 重连并重发同一请求；重试耗尽后仍返回 `None` 并保留原有提示，
  对外契约不变，全项目既有调用点无需改动
- `efinance_financial`：最新报告期拉不到就逐期回退（实测回退到 `2026-06-30`），
  单次失败不再永久禁用；失败时回退本地磁盘缓存而非静默返回空

## 二、日常增量数据源替换（mootdx → 新浪批量快照）

TDX 服务端已拒绝 K 线请求（`TdxFunctionCallError`），mootdx 取不到任何行情，
`sync_today_bulk` 只能降级 baostock，而那段兜底开 8 线程共用一把全局锁（实际完全串行）
且设了 300s 硬截止，每晚只能补约 1850/5700 只，数据缺口滚雪球。

- 新增 `data/sina_quote.py`：`hq.sinajs.cn/list=` 一次带 250 个代码，全市场约 20 个请求；
  成交量单位是「股」、成交额是「元」，与 `stock_daily` 口径一致
- `sync_today_bulk`：新浪快照为主（日增量），多日缺口降级 baostock；
  去掉假并行与硬截止
- `compute_staleness_report` / `compute_data_health_report`：过期率改用**交易日**计数
  （原先按自然日，国庆后误报 67.6%，实际只有 4.9%）
- `get_all_symbols` 改用 baostock（原 mootdx 列表按名称黑名单过滤，会把指数当股票返回）

## 三、基准指数被同号个股覆盖（regime 失真）

`000905` 既是中证500指数、又是深市个股厦门港务，库内按裸 6 位存储导致两者冲突。
日增量同步每日把该股的行情写进指数序列，自 2026-08-24 起指数数据被整段污染
（8000 点 → 9 元），`RegimeDetector` 读到的 `价格vsMA120 = -99.9%`，
**无论真实市场如何都被判为强熊**。

- 基准指数（`sh.000905` / `sh.000300`）改用 baostock 显式代码单独更新，不走个股行情源
- 已删除被污染的 29 行并重新回补指数，现在 `价格vsMA120 = -10.7%`、建议仓位 10% → 15%

## 四、其他修复

- `get_local_symbols`：`_is_a_share` 原规则放行了 `399` 深证指数等，导致 207 个指数
  被当成个股评级并写进 `rating_result`；改为排除 `010/018/019/020/360/395/399`
  与基准指数
- `verify_prices`（`--verify-data`）：取不到数据原先一律计为「通过」，mootdx 死后
  会报出 100% 一致的假结果；改为对比 baostock 并分别统计 比对/通过/异常/跳过
- 移除死路径：`--mootdx-backfill`、`backfill_from_mootdx`、`_mootdx_get_symbols` 等
- 统一 regime 权重表：`REGIME_STRATEGY_BIAS`（等权）与 `REGIME_BIAS`（反转主导）合并为
  `sequoia_x.regime.REGIME_BIAS` 单一来源，飞书/手机端展示的不再是错数字
- 清理 `stock_daily` 中 466 个非 A 股代码（债券/指数，182,015 行），数据过期率 67.6% → 0.4%
- `pyproject.toml` 补声明 `efinance`（optional-dependencies.financial）

## 五、移除「资金流向」伪信号与死模块

置信度评分的「资金流向」维度（满分 0.05）在文档里写作「北向资金增持」，实际实现是：
调用 `ak.stock_hsgt_individual_em(symbol)` 后**只要接口返回非空数据就恒定加 0.03**——
数据「存在」与资金「增持」无关，这是纯噪声；接口失败时退回「5 日上涨且 5 日均量 >
20 日均量」的量价启发式，又与已有的「量价配合」「量价共振」两个维度重复计分。

信号前提本身也已失效：2024-08-18 起沪深港通不再披露逐日净买入额（现仅披露成交额，
持股明细改季度），网上大量教程仍在教「北向实时流向」。

同时 `sequoia_x/flow/`（北向/两融/大宗）自初始提交起从未被任何代码 import，是死模块，
其 docstring 里的「北向资金持续流入的个股有 Alpha 信号」从未进入过评分。

- `signal/generator.py`：删除 `_calc_flow_bonus()` 及「资金流向」维度，
  并移除仅服务于它的 `_calc_confidence(symbol=...)` 参数
- 「多策略共振」满分 0.25 → 0.30，评分维度 7 → 6，满分仍为 1.00（评级阈值口径不变）
- 删除死模块 `sequoia_x/flow/`
- README 评分表、安装说明与目录树同步

## 六、推送名单并列同分时按置信度二级排序

`_run_rating` 取推送名单用的是 `all_results[:15]`，而第 15 名处常有大量同分股票
（实测总分 1.4 全市场 **36 只并列**），被截断的 21 只由输入顺序决定——名单本质上是
任意的，输入集合一变就可能换人。

- `main.py`：候选池先放宽到「第 15 名及以上 + 所有同评级同分者」（上限 200 只防御），
  生成信号后按 **(评级类别, 总分, 置信度)** 排序再取前 15
- 实测：36 只同分候选里换了 **7 只**，挤掉的正是置信度最低的（0.21~0.51），
  新进的置信度在 0.55~0.85；评级口径完全不变（15 只仍是 buy/1.4）
- 新增只读诊断脚本 `run_rating_vs_confidence.py`、`run_confidence_diff.py`

## 七、本次审查中发现、尚未处理的问题

以下都是**既有问题**，与本次改动无关，仅做记录：

- **强熊下评分与趋势软过滤对冲**：`REGIME_BIAS["强熊"]` 把 trend/momentum 权重压到
  **0.00**（有 IC 数据支撑：趋势/动量类 IC 为负、反转类为正），但趋势过滤是独立硬门——
  结果**总分 ≥1.5 的 446 只股票全部因趋势不通过被压到 watch，一只都进不了推送**。
  实际推送的 15 只来自总分 1.4 档。权重本身有依据，问题出在两者的交互。
- **评级与置信度是两把尺子**：评级 `total_score` 是截面 Z-score 合成（阈值 1.5/1.0/0.5），
  置信度是绝对 0~1（阈值 0.75/0.60/0.45/0.25）。两者 Top15 **只重叠 2 只**，
  置信度最高的 94 只在评级体系里中位排名 3435/4984。
- **评级分布极端**：4993 只里 `avoid=4348`(87%)、`watch=502`、`attention=101`、`buy=42`、
  `strong_buy=0`。原因未查。
- **回测未来函数**：`backtest/engine.py` 用当日收盘算信号、又在同一收盘价成交
  （`get_data` 切片含当日）。使回测收益偏乐观。
- **策略体系有两套**：`rating/strategies.py`（9 个函数，评级用）与 `strategy/*.py`
  （6 个类，融合评分/选股用），名字部分重叠但实现各自独立，且在多处被混用。

## 八、推送与追踪口径修复（审查后第一批）

- **追踪库一直是空的**：唯一写 `recommendations` 的是 `_run_daily`（`main.py:525`），
  而生产实际跑的是 `--rating`（`_run_rating` 不写追踪库）、定时任务也未安装。
  → `--review` 的命中率统计从来没有数据源。已在 `_run_rating` 补上 `record_signals`。
- **三处截断三个数字**：`print_console` 打 20 条、`push_feishu` 推 15 条、tracker 记 50 条，
  没有权威口径。新增 `reporter.DISPLAY_TOP_N = 15` 统一定义
  （`_run_rating` 原先另有一套 `TOP_N = 15`，一并收敛；`print_console` 的 20 未动）。
- **追踪静默丢弃低置信度推荐**：`tracker/review.py` 原有 `if sig.confidence < 0.5: continue`，
  会把推送名单里置信度最低的那批丢掉；已删除，改为记录调用方传入的全部。
- 新增只读诊断脚本 `run_rating_scale_check.py`
- **87% `avoid` 查明**：= 469 只明显负分（≤-0.1）+ 3879 只微弱负分（-0.05~0，圆整为 `-0.0`）。
  根因是「稀疏策略做 Z-score，把未触发编码成负分」（实测 `meanReversion` 89.6% 为负）
  叠加「圆整与定级用了两个不同的数」（`rating/engine.py:596` 用 `round(total,1)`、
  `:600` 用未圆整的 `total`）。后者使界面显示「总分 -0.0」却判为 `avoid`。
- 完整台账见 `docs/code-review-2026-10-13.md`（H1–H6、M1–M13、L1 共 19 条 + D1–D5 设计层 5 条
  + DOC1–DOC8 文档对齐 8 条）

## 九、IC 历史重复污染修复（审查后第二批）

`ICTracker._eval_record` 原先不持久化，导致每个尚未完成的 pending 快照在**每次运行**
都被重新评估，向 `history` 重复追加同一天同一 lookforward 的 IC 条目——实测
turtleTrade **774 条只有 41 个不同日期，同一日期最多重复 30 次**。
另有周末快照永久卡死：`trade_dates.index()` 对非交易日抛 `ValueError` 后 `continue`，
**跳过了「标记已计算」那行**，于是永不移除、每次运行重算一遍。

IC 历史是权重调整的**唯一**输入（`apply_ir_weights` 把 IR ≤ 0 的策略权重直接清零），
而 `detect_decay` 又是「从末尾数连续 IC<0 的**条目**」——数的是条目不是日期，
20 条重复就满足「连续 20 天衰减」。**权重因此由被污染的数据决定。**

- `signal/quality.py`：`_eval_record` 持久化（`__init__` 初始化、`_load` 恢复并转 int 键、
  `save` 写入）；`eval_forward_returns` 不再每次重置它；新增 `_maybe_cleanup()`；
  非交易日快照标记为已处理并清理
- 新增 `dedup_ic_history.py`：一次性迁移，按 `(策略, 日期, lookforward)` 保留最后一条
  并**按日期排序**（原顺序是「首次评估顺序」，而 `entries[-20:]` 取的是「最近 20 条」，
  顺序必须真正对应日期）。实测 **4691 → 462 条（−90%）**，
  备份 `output/ic_history.json.bak-20261013`
- 新增 `show_ic_ir.py`：去重前后 IR 与归零判定对照

**去重后 9 个策略里 3 个的判定翻转**（fundamental 保留→归零、kdj 与 turtleTrade
归零→保留），证明原先那套「2 策略权重」确实是重复条目顶出来的。更关键的是：
去重后 `turtleTrade` 的 IR 只有 **+0.0033**、`kdj` +0.098——这些决定生死的数字
全落在 ±0.2 以内。**只要还用「IR 的符号」做硬清零，权重就是掷骰子。**
该设计问题（D5）尚未处理，已列为最高优先级。

## 十、D5 收口：权重不再由噪声 IC 决定（2026-10-14）

第九节留下的 D5：`apply_ir_weights` 用 IR 的**符号**硬清零策略权重，而这些 IR 全落在
噪声带内。本次先量化、再决策、最后改代码。

### 量化（只读，新增 `show_ic_ir_significance.py`）

**1. 先查出一个确凿的 bug：horizon 混编。** `ICTracker.get_summary` 的 `entries[-60:]`
把同一天的 `lookforward=5` 与 `lookforward=20` 两条 IC 塞进同一个均值和标准差，
`detect_decay` 也在这条混编序列上数「连续负 IC」。后果是组间方差被当成噪声进分母、
|IR| 被系统性压小、符号还可能翻转：

| 策略 | 5日 IR | 20日 IR | 混编 IR（原口径） |
|---|---|---|---|
| turtleTrade | +0.096 | +0.121 | **−0.070**（符号翻转） |
| fundamental | +0.067 | −0.507 | −0.101（稀释到 1/5） |
| maCross | −0.415 | −0.612 | −0.126（稀释到 1/5） |

**2. 重叠窗口校正后的 θ。** h 日前瞻收益在连续交易日上是重叠的，教科书 SE(IR)=1/√n
不成立（实测 ρ₁ 达 0.5~0.8）。改用 Newey-West（L = h − 1）后 θ = 2×SE(IR) 落在
0.28~0.80，而原口径只有 0.29~0.56。6 个启用策略里只有 `meanReversion` 勉强过关
（约 2.2σ）；换成 Harvey-Liu-Zhu 的多重检验门槛（k=3）则一个都不剩。

**3. 样本代表性。** IC 历史只有约 42 个交易日（5 日序列）/ 35 个（20 日序列），
且**全部落在同一个市场状态（熊市）**。`output/factor_validation.json` 的
`market_regime_days` 显示 2024-01~2026-08 的 584 个交易日里 bull 318 / range 190 /
**bear 只有 71**——连那份「大」数据集本身也是牛/震荡为主。

### 决策依据

- IC 样本（约 2 个月、单一状态）不足以支撑「清零一个 0.32 权重的策略」；regime 权重表
  建立在 584 个交易日 / 5206 只股票之上，样本量高一个数量级
- Harvey-Liu-Zhu (2016) 的多重检验门槛 k=3、DeMiguel-Garlappi-Uppal (2009) 的
  「估计噪声大时先验优于估计权重」、Asness 等 (2018) 的「因子择时极难」三条一致指向
  同一结论

### 改动

- **`signal/quality.py`**：新增 `_series(strategy, lookforward)`（按期限取序列、按日期排序、
  同日去重）；`get_summary(lookforward=20)` 只在单一期限内统计，返回值加 `lookforward`；
  `detect_decay` 同步按期限拆开——此前「连续 20 日」实际只有约 10 日；`print_summary`
  按期限分块打印
- **`rating/engine.py`**：`scan_market` 不再调用 `apply_ir_weights` 与
  `_apply_ic_decay_weights`，**生产权重只由 `apply_regime`（市场状态）决定**；两个方法
  保留供诊断脚本调用，文档字符串里写入了**预注册的重新启用条件**（每 regime 独立窗口
  ≥ 20 个时才启用，k=3、Newey-West L=h−1、g(IR) 形状固定）
- **`README.md`**：重写「动态权重」一节，同步架构图与脚本表
- **`run_weight_validation.py` / `run_rating_neutral_ab.py`**：同步到新的权重口径（不再
  复刻 IR 加权与 IC 衰减）；新增 `run_d5_weights_ab.py`（冻结快照内反算）做本次改动的
  前后对照
- **`data/efinance_financial.py`**：报告期只向前推进，不再因一次抓取失败而退回旧一期（见下）

### 影响

**在同一份冻结快照内反算**（`rating_result_20261014.json`，4993 只，市场状态 = 强熊；
用 `per_strategy_scores` 复刻 `_combine_scores`，只换权重不换数据）：

| 每策略权重 | 改动前 | 改动后 |
|---|---|---|
| mean_reversion | 0.40 | 0.34 |
| kdj | 0.40 | 0.34 |
| fundamental | 0.20 | **0.32** |

- 强熊下 `REGIME_BIAS` 本就把 trend/momentum 压到 0.00，所以改动表现为 reversal 与
  fundamental 之间的再分配。**注意：旧的 IR 层一直在把 fundamental 从 0.32 压到 0.20**
  （因为去重后它的 IR 为负），与 regime 表「熊市保留防御维度」的设计意图相反
- 总分平均变化 +0.003，但分布重排：2408 只升 / 1283 只降 / 1302 只持平
- **总分 ≥1.0 的高分桶从 409 只收缩到 164 只**——新权重把 reversal 的 0.12 挪给
  fundamental，而高分股大多由反转驱动，所以高分桶明显收窄
- **按总分排序的 Top15 完全不变（15/15）**；实际推送还会叠加趋势上限
  （`_calculate_rating` 对趋势不过的股票最高只给 watch），所以高分桶的收缩基本不传导到名单

> **⚠️ 关于「诊断脚本股票池」的坑（本次踩到并已修正）**：`rating_result_*.json` 里的 code
> 是 `scan_market` **过滤后**的结果，不是生产的输入池。实测生产输入池
> `get_local_symbols()`（5237）去 ST（198）后 = **5039**，而快照只有 **4993**（差 46 只）。
> 拿快照的 code 当池去重扫，截面标准化的样本不同 → 所有股票的 Z 分平移 → Top15 对不上。
> 这不是生产缺陷：用生产口径的池在当前数据状态下重扫，Top15 **15/15 一致**、
> 评级分布**逐档相同**（见 `check_pool_parity.py`）。
> `run_d5_weights_ab.py` 因此改为**冻结快照内反算**；`run_rating_neutral_ab.py` 仍然用
> 快照当池（它的两臂共享同一池，内部比较成立，但绝对数值不等于生产口径）。

> **顺带修掉一条可复现性隐患（`data/efinance_financial.py`）**：`load_financial_all`
> 此前取的是「**第一个拉取成功的报告期**」。本次实测 09-30 只有 15 只披露（低于 1000
> 门槛被跳过）、06-30 抓取时 `Response ended prematurely` 失败，于是回退到 **2026-03-31**
> 并落盘。问题在于：当天**首次**运行若抓取失败，它会**静默回退到旧缓存或 baostock 逐股**，
> 而当天稍后成功的运行会写出不同报告期的数据——同一天两次运行就可能拿到不同的
> fundamental 输入（该策略权重 0.32）。
> 现在 `_fetch_performance(floor_report_date=...)` 以缓存里已选定的报告期为**下界**：
> **只允许向前推进；一旦能用的报告期比缓存更旧就放弃返回、保留缓存**。
> 已用桩掉 efinance 的离线测试验证三种场景（保留不退化 / 正常向前推进 / 无缓存取可用期）。

- 所有以权重为基线的历史对照（D1 的「446 只高分股被挡」、D3 的「87% avoid」等）都建立
  在旧的随机权重上，需重测
- 未处理：`ir_status` 的 0.5/1.0 阈值像是照 Grinold 式**年化** IC-IR 标尺搬来却套在
  未年化的 per-observation IR 上（`quality.py:415`，出处见 `eff2902`）。仅用于展示，
  不驱动决策，列为低优先级

## 十一、D5 之后的复测与台账更正（2026-10-15）

D5 改了评分基线，10-13 审查台账里以旧权重测出的结论需要复测。

### 复测结果（基线 = `rating_result_20261014.json`，市场状态 = 强熊）

| | 台账原值（10-13） | 当前值（10-14） |
|---|---|---|
| avoid | 4348（87%） | **2649（53.1%）** |
| watch / attention / buy | 502 / 101 / 42 | 1035 / 1306 / 3 |
| 显示 `-0.0` 但判 avoid | 3879 | **7** |
| D1：总分 ≥1.5 的股票 | 446（全被压 watch、进不了推送） | **34（其中 2 只 `buy`）** |
| total_score 中位 / P90 / max | `-0.0` | −0.20 / +0.80 / +1.60 |

「稀疏策略把未触发编码成负分」这个**机理仍然成立**（`meanReversion` 仍有 76.5% 为负），
只是权重变化改了负分的**幅度**，所以显示层不再挤在 `-0.0`。

### 关键更正：这些数字不是 D5 造成的

失效时点在 **2026-10-13 当天**——IC 历史去重后 `apply_ir_weights` 换了权重组合。
D5 在同一份快照内的效果很小（总分平均 +0.003、按总分排序的 Top15 **15/15 不变**）。
台账里「等 D5 定了再重测」的方向对了，但**不能把这些数字的变化归因于 D5**。

### 另一处必须记住的坑

台账引用的 `avoid=4348` 与它引用的文件 `output/rating_result_20261013.json` 实测的
**2619** 对不上——该文件在同日 23:04 被后一次运行**覆盖**过（`_20261014` 也在 23:07
被覆盖）。**引用评级快照必须带时间戳，`rating_result_<日期>.json` 这个说法不唯一。**

### 顺带确认

- Windows **没有** `Sequoia-X选股` 定时任务（`Get-ScheduledTask` 查无）——证实
  「定时任务从未安装」，即 DOC2 的前提成立
- D5 的结论仍然成立：当前快照用新权重反推 `total_score` 的平均绝对误差 **0.016**，
  用旧权重是 **0.101**

## 十二、文档与定时任务口径修正（2026-10-15）

### DOC2 — README 声称「每晚21:00自动运行」，实际从未安装，而且装的是错的路径

- `_install_schedule`（`main.py:292`）原本安装的是 `python main.py`（**备用回退路径**
  `_run_daily`），不是生产用的 `--rating`。照着 README 装定时任务，会得到**另一条流水线**。
  → 改为安装 `python main.py --rating`
- README 三处（`:29` 概览、安装说明、数据流架构图）改掉「每晚21:00自动运行」的既成事实
  口吻，改为「可选；本机目前没装，生产靠手动跑 `--rating`」
- 用 `Get-ScheduledTask` 确认：**确实没有** `Sequoia-X选股` 任务

### DOC3 — 「按总分取前15名」不准确

实际排序键是 `(评级类别, 总分, 置信度)`（`main.py:826-832`），README 已同步。

## 十三、M11：定级与显示统一到同一个数（2026-10-15）

`rating/engine.py` 两处（`analyze()` 与 `_cross_sectional_standardize()`）此前是
**存 `round(total, 1)`、按未圆整的 `total` 定级**。于是在阈值附近出现自相矛盾：
`total = 0.45` 显示 `0.5` 却判 `attention`；`total = −0.03` 显示 `-0.0` 却判 `avoid`。

**修法**（已与用户确认）：定级改用**存下来的那个数**——「显示什么就按什么定级」。
`run_rating_neutral_ab.py` 里的同源写法同步。

**实测影响**（一次扫描同时算新旧两套定级；强熊 / 4993 只）：

| | 旧规则 | 新规则 | 差 |
|---|---|---|---|
| strong_buy | 0 | 2 | +2 |
| buy | 3 | 1 | −2 |
| watch | 1035 | 1092 | +57 |
| attention | 1306 | 1256 | −50 |
| avoid | 2649 | 2642 | −7 |

**共 66 只变动（1.32%），全部是上移**：`attention→watch` 57、`avoid→attention` 7、
`buy→strong_buy` 2。其中 7 只正是「显示 `-0.0` 却判回避」的那批；另外两个方向说明
受影响的不止 0 这个阈值，0.5 / 1.0 / 1.5 也有边界股。

**遗留（展示层，未并入本次）**：`round(-0.03, 1)` 得到 `-0.0`，界面仍显示「总分 -0.0」。
它与评级现已自洽（`-0.0 >= 0` → attention），但显示成 `-0.0` 本身不美观，
是否归一成 `0.0` 另定。

## 十四、D1 / D2 / D4 复测（2026-10-15）

### D4 —— 0.30 档从「不可达」变成「45% 落入」，但中间档被挤空

10-13 台账记「多策略共振 0.30 档全市场 0 只触发」（当时分布 `0.15=515 / 0.10=4469`）。
当前快照（`rating_result_20261014.json`，4993 只）里「值为正」的策略个数分布：

| 正分策略数 | 只数 | 落入的共振档 |
|---|---|---|
| 3 | 2263（45.3%） | 0.30 |
| 2 | 84（1.7%） | 0.15 |
| 1 | 2422（48.5%） | 0.10 |
| 0 | 224（4.5%） | 0.10 |

**成因是 D5**：`fundamental` 从清零恢复到 0.32 后活跃策略由 2 变 3，0.30 档变得可达。

> ⚠️ **更正**：最初我按 `strategy_scores` 的**键数**统计，得出「每只都恰好 3 个策略、
> 该维度恒为 0.30 常数」——**那是错的**。生成器数的是「值为正的个数」。

**真正的设计问题**：数的是**类内均分后的 `ortho` 分**——同类内每个策略拿到同一个类别均分，
所以**一个类别为正就按它的成员数计**（reversal 有 2 个成员，一个正类别直接贡献 2）。
「3 个策略」实际可能只是「2 个类别为正」。应改成按**原始分 > 0 的策略数**计。

### D1 —— 数字已变，机理仍在

「总分 ≥1.5 的 446 只全被压 watch、进不了推送」→ 现在 **34 只，其中 2 只 `buy`**。
但「总分高」与「趋势通过」在此权重下仍是负相关，机理未变。

### D2 —— **暂时无法复测**

`confidence` 只存在于 COS 的 `priority/today.json` 与飞书推送内容里，**没有落到任何
本地文件**（`grep -l confidence output/*.json` 无结果）。复测 D2（评级 Top15 与置信度
Top15 只重叠 2 只）需要跑一次全市场信号生成；更划算的是把 `confidence` 加进评级快照的
导出字段。**在此之前，D2 的数字仍是 10-13 旧权重下的。**

## 十五、H1/H2/M1：划清两套策略体系的边界并除隐患（2026-10-15）

用户选定「**划清边界 + 除隐患**」，不做合并。

### H2 — 删掉 5 个虚构的策略类名

`main.py` 的 `strategy_key_map` 把 camelCase 键翻成 `MeanReversionStrategy` 之类，
而 `strategy/` 下**根本没有这些类**。整张表删掉，`strategy_map` 直接送 camelCase 键；
兜底值 `["RatingEngine"]` → `["rating"]`（与 reporter 的标签表对得上）。
**顺带好处**：手机端类别匹配用的就是 camelCase（`stock_detail_page.dart:764`），
两边名字空间从此一致。

### H1 — 两套体系在报告层分开

`reporter.py` 的 `label_map` 拆成 `_RATING_LABELS`（体系A：`rating/strategies.py`）
与 `_SELECTION_LABELS`（体系B：`strategy/*.py`）两张表，并去掉
`s.replace("Strategy","")` 兜底——它会把类名剥成 camelCase 的样子，正是「看起来像一套」
的来源。README 顶部写明存在两套互不相干的实现及各自驱动哪条链路。

### M1 — 类别表收敛到唯一来源

- `RatingConfig.RATING_GROUPS` 成为**唯一来源**；`STRATEGY_CATEGORIES` 由
  `_snake_categories(RATING_GROUPS, IR_STRATEGY_MAP)` 派生，不再手写第二份
- `RatingEngine.RATING_GROUPS` 改为直接引用同一对象（已验证是同一对象）
- 派生结果与原值逐类别一致（脚本比对通过）
- 未动 `ensemble.consensus.STRATEGY_CATEGORIES`（属体系B）与
  `eval/replay_scores.TECH_KEYS`（保留手写，但删掉同文件里**从未被引用**的 `KEY_ATTR`，
  并注明同步要求，否则回放校验会静默漏检）

消除的正是 M1 写的那个风险：往 `RATING_GROUPS` 加策略却忘改另一张表 →
`apply_regime` 不设权重 → `_combine_scores` 按「权重 > 0」过滤 → **静默丢弃，不报错**。

## 十六、H3 对照实验：当日收盘成交把年化虚高约 5.4pp（2026-10-15）

`backtest/engine.py` 原本用同一根 K 线的收盘价既算信号又成交——A 股没有 MOC 指令，
不可执行。新增 `BacktestConfig.fill_mode`：`same_close`（默认 = 原行为）/ `next_open`，
成交价、成交日期、涨跌停约束全部按成交日重算。**默认值未改。**

全市场、`TurtleTradeStrategy`、近一年：

| 指标 | 当日收盘 | 次日开盘 | 变化 |
|---|---|---|---|
| 年化收益 | −18.6% | −24.0% | **−5.4pp** |
| 最大回撤 | −28.1% | −33.3% | −5.2pp |
| 夏普 | −1.48 | −1.82 | −0.33 |
| 胜率 | 30.1% | 30.7% | +0.5pp |
| 交易次数 | 239 | 313 | +74 |

结论：**引擎跑出的任何正向结果都要按这个量级打折扣**；「选股无优势」的既有结论因此更稳。

**默认已于同日切换为 `next_open`**（用户确认）。影响面：`main.py --backtest`、
`run_multi_backtest.py`；README 的回测节已写明口径，**更早的回测数字与截图都带着这个偏差**。
需要旧口径做对照时显式传 `BacktestConfig(fill_mode="same_close")`。

## 十七、M10：核心步骤失败不再静默报「运行完成」（2026-10-15）

`_run_daily` 的信号生成失败只打 `warning` 就继续走到「日常模式运行完成」，
退出码仍是 0 —— 定时任务与人都不告警。

新增 `main.CoreStepFailure(RuntimeError)`，抛出去让 `main()` 已有的兜底
`except → sys.exit(1)` 生效：

- `_run_daily` 信号生成两处 `except`：`warning` → `error` + `raise`
- `_run_rating` 的 COS 同步：**顺带查出更隐蔽的一条**——`_upload_to_cos` 的**返回值被整个
  丢弃**，而它会返回 `ok=False` 表示「部分文件没传上去」（手机端会拿到旧数据甚至空数据），
  这条路径此前连 warning 都没有。现在检查返回值，失败即 raise
- `_run_daily` 的 COS 调用同样补上返回值检查

## 十八、手机端数据层专项审查（2026-10-15）

`gpfxapp/` 此前一直不在审查范围内。本次按「PC 上传 ↔ 手机端解析/合并/兜底」这条链路
做了全库只读排查，完整报告见 **`docs/mobile-datalayer-review-2026-10-15.md`**（8 项，
其中 6 项已逐条回源核实）。

**根因是两条**（其余是衍生）：

1. **本地离线分数与 PC 分数共用 `1.5/1.0/0.5` 阈值，但量纲完全不同**——
   PC 是截面 Z 合成（实测 −2.9 ~ +1.6），本地是「原始整数分 × 权重」加权和。
   PC 数据缺失时本地极易 ≥1.5 直接判 S，而这个 S 不是 PC 的 S。
2. **本地引擎不算 fundamental，而 D5 之后它在 PC 强熊下占 32%**——
   `weightFundamental` 在 `strategy_config.dart` 里字段/`toJson`/`fromJson` 全有、
   能同步下来，但 `analysis_engine.dart` 的 `weights` 字典**从不放它**。

其余：`mergePcData` 分数取 PC 而 `signals` 留本地（`pc_merge.dart:32`）；
`home_page.dart` 同文件里阈值既有字面量又有常量；手机端有**第三份 regime 权重表**
（`stock_detail_page.dart:784`）且无 fundamental 类；`macdRsiEnabled` 默认值与 PC 相反。

**顺带确认一条好消息**：配置同步链路是通的——`StrategyConfig.fromJson` 期望 camelCase，
PC `to_dict()` 输出的正是 camelCase。问题不在常量值写错，而在**同一个常量被用在两种量纲上**。

本轮**未改手机端代码**，先出报告。

## 十九、tracker/review.py 三处测量缺陷修复（2026-10-15）

`recommendations` 表的回溯逻辑有问题，导致「实盘命中率」不可引用。

1. **回溯窗口不是 7 天**：`df["date"] >= rec_date` 把推荐日之后**所有**历史都算进来，
   同一条推荐第 8 天和第 50 天回溯结论不同（实测平均回溯期 48.5 天）。
   → 固定为 `[进场日, 推荐日 + REVIEW_WINDOW_DAYS]`
2. **止损止盈判定自相矛盾**：用「整段最高价判止盈 + 整段最低价判止损」，只要期间
   既冲高又探底就两边都记命中（实测止盈 97.5% / 止损 92.6%）。
   → 按时间顺序走，先触及哪个以哪个为准，两者互斥
3. **用近似价位**：`entry_low*0.97` / `entry_low*1.05` → 改用库里存的 `stop_loss` / `target_1`

**顺带发现第 4 条（同类前视）**：进场价原用推荐日**收盘价**，而推荐正是拿那根收盘算
出来的——与回测 H3 同一条。→ 改为**推荐日次日开盘价**，与 `fill_mode="next_open"` 同口径。

### 修正前后的实测

| | 旧口径（库内 81 条） | 新口径（93 条合格记录） |
|---|---|---|
| 收涨率 | 24.7% | **67.7%** |
| 触及止损 | 92.6% | **31.2%** |
| 触及止盈 | 97.5% | 61.3% |
| 平均收益 | **−11.25%** | **+8.17%** |
| 平均回溯天数 | 48.5 | 5.0 |

同一批 93 条记录上，两种进场价的差异只有约 1.1pp（收盘 +9.29% vs 开盘 +8.17%）——
**主导因素是窗口与止损止盈逻辑，不是进场价**。（中途有个临时脚本把进场日取错了，
一度得出「差异 +10pp」的错误结论，已废弃。）

### ⚠️ 这个数字依然不能当结论用

全部 153 条记录只来自 **6 个推荐日**（06-10 / 06-11 / 07-02 / 08-19 / 10-13 / 10-14），
而 93 条「合格」记录里有 76 条来自 **06-10 与 06-11 两天**——有效样本是**约 4 个市场日**，
基本是同一段行情。修复让口径自洽、可复现，但**不构成统计证据**。
H4 补上 `_run_rating` 的 `record_signals` 后，从 10-13 起才会按日累积。

### 数据已重置

81 条按旧口径算出的回溯值已备份到 `output/recommendations_review_backup_20261015.json`
并重置为待回溯，下次 `--review` 按新口径重算。

## 二十、小修两项（2026-10-15）

### `run_rec_eval.py` 的基准改回 000905

`BENCH` 写死 `000300`，而生产三处都是 `RegimeDetector.detect("000905")`——两套基准在
668 个共同交易日里有 **205 天（30.7%）的 regime 标签不同**，于是本脚本的「分市场状态」
结论与生产对不上。同时删掉那句过期注释（「000905 尾段脏数据弃用」——那是 2026-10-11
已修的「被同号个股覆盖」bug，现在 000905 有 671 条数据、无崩断）。

> ⚠️ **`output/recommendation_eval.json` 是用旧基准跑出来的，需要重跑一次才与新口径一致。**
> 在此之前，基于它得出的「分市场状态」结论（含 2026-10-11 那次四层测评里的相关部分）
> 都带这个偏差。

### 归一「-0.0」显示

`round(-0.03, 1)` 得到 `-0.0`，界面显示成「总分 -0.0」，看着像 bug。M11 之后它与评级
已经自洽（`-0.0 >= 0` → attention），但显示仍不美观。在 `rating/engine.py` 两处
（`analyze()` 与 `_cross_sectional_standardize()`）加 `+ 0.0` 归一，
其余数值不受影响（已验证：`-0.1` 仍是 `-0.1`、`0.5` 仍是 `0.5`）。

## 二十一、顺手清理批次：M2/M3/M5/M7/M9 + M12 结案（2026-10-15）

| 条目 | 处置 |
|---|---|
| **M2** 回测成本三处不一致 | 修：`run_consensus_backtest.py` 补上卖出印花税 `0.0005`（两处平仓逻辑）|
| **M3** `max_hold_days` 用自然日 | 修：`backtest/engine.py` 改按**交易日**算（`hold_days = i - pos["entry_idx"]`）。此前 20 自然日 ≈ 14 交易日，与 `signal/models.py` 声明的「20 交易日」差 30% |
| **M5** mootdx 分支无超时 | 修：抽成 `_try_mootdx()` 并加 8 秒超时。此前顺序是反的——先试的没保护、后试的 baostock 反而有 |
| **M7** `factors` 文档过时 | 修：数据来源注释改成 baostock。**台账原文有一处不准**——`[factors]` extra 不是死配置，`fund_research/analysis/screener.py:141` 仍在用 akshare；已在 `pyproject.toml` 与 README 标注，**不能删** |
| **M9** `_check_data_freshness` 调两次 | 修：加 `phase` 参数，`_run_daily` 两处分别标「同步前 / 同步后」，日志不再出现两行一模一样 |
| **M12** `x or 0.0` 遇 `-0.0` | **排查完毕，无需改动**：全仓 4 处，没有一处在符号敏感的分支上（`eps` / `npMargin` 的 `0.0` 与 `-0.0` 同为 falsy，走同一分支；`review.py:222` 仅显示层）|
| **L1** 名称解析两处重复 | 保留：`feishu.py` 那份是「推送失败时的兜底」，与 `name_resolver` 的缓存批量查询不是同一场景，合并风险大于收益 |

**两项未做，需要你定**：

- **M4** 止损/止盈目前只在**收盘**判定一次、且用同一收盘价平仓（盘中跌破 -8% 不会触发）。
  这是建模选择——盘中触发 vs 收盘触发、成交用触发价还是收盘价、跳空怎么处理——
  且与刚上的 `fill_mode` 有交互。已在台账里标注这是简化，**未改代码**
- **M6** baostock 重连有三套实现（`utils/baostock_guard.py` 的 socket 层、
  `factors/__init__.py` 与 `rating/engine.py` 的错误码层）——要先判断是互补还是重复，
  各套的退避/重试次数不一致会很难排查

## 二十二、B 类：D4 共振维度改口径 + D2 复测（2026-10-15）

### D4 的一处测量更正

先前我按 `strategy_scores` 的**键数**统计，得出「每只都恰好 3 个策略、该维度恒为 0.30
常数」——**那是错的**。生成器数的是「值为正的个数」（`generator.py:245`）。

### 共振维度改口径（用户选定）

`strategy_count` 原本数的是 `strategy_scores` 里值为正的个数，而那是**类内均分后的
`ortho` 分**——同类内每个策略拿到同一个类别均分，于是「一个类别为正」会按它的成员数
被放大（reversal 有 2 个成员 → 一个正类别直接贡献 2），「3 个策略」实际可能只是
「2 个类别为正」。

**改动**：`main.py` 两处 `strategy_map` 的构造改用 **`raw_strategy_scores` > 0**
（每策略独立）。一次全市场扫描实测：

| | 0.30 档 | 0.15 档 | 0.10 档 |
|---|---|---|---|
| 旧口径 | 2263（45.3%） | 84（1.7%） | 2646（53.0%） |
| **新口径** | **410（8.2%）** | **1582（31.7%）** | **3001（60.1%）** |

中间档从 1.7% 恢复到 31.7%，这个维度终于能区分股票。

### D2 复测：两把尺子完全不相关

同一次扫描算出全市场 confidence：

| 口径 | 评级 Top15 ∩ 置信度 Top15 | 置信度 ≥0.75 那批在评级里的中位排名 |
|---|---|---|
| 共振项旧口径 | **0/15** | 1841 / 4993 |
| 共振项新口径 | **0/15** | 3346 / 4993 |

**比 10-13 那次（重叠 2/15）更糟**——按评级推出去的股票，置信度等级与评级毫无对应关系，
用户会在同一张卡片上看到「推荐」配一个很低的 `[Lv.X]`。
（注：共振口径的改动让它又大了些，这是副作用，不是改动本身的问题。）

**怎么统一、或怎么在界面上把两者含义说清，仍是待决项。**

### ④⑤⑥（手机端）本轮未动——发现比原选项更复杂

- **Flutter 工具链在本机不可用**（`flutter --version` 尝试联网失败），
  改完**无法构建/运行验证**，只能人工审读
- **⑤ 的 (a) 选项前提不成立**：手机端 `analysis.signals` 是 `List<Signal>`（带
  `type: bullish/bearish`），而 PC 的 `PcRatingResult.signals` 是 `List<String>`，
  且 **PC 的评级导出里根本没有 `signals` 字段**。照 (a) 直接切过去会把信号清空。
  要做需先：PC 导出补 `signals` + 手机端把字符串按关键词映射成 `Signal` 对象
- **⑥ 的 (a)** 需要手机端新解析 PC 的 `strategy_bias`（`main.py:216` 已上传，
  但手机端全库没有任何地方读它），还得补类别名映射（PC 的 `trend/momentum/`… ↔
  手机端的 趋势/动量/反转）

**因此停下等你定**：是接受「改完不能验证」先做 ④（纯本地标签改造）和 ⑥，
还是等能跑 Flutter 的环境再一起做？
