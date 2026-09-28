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
- [ ] 阶段三续: 周线/60分钟线多周期确认过滤假突破
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

- `main.py` 日常流程中 `strategy_regime_weights` 计算后仅用于日志，未传入 `ConsensusEngine`
  （该引擎按策略数量与类型打分，不接权重；实际评分权重以 `--rating` 路径的 `REGIME_BIAS` 为准）
- `sequoia_x/regime/__init__.py` 的 `REGIME_STRATEGY_BIAS` 仍为等权表（供 `RegimeResult.strategy_bias` 展示），
  与 `rating/engine.py` 的 `REGIME_BIAS` 并存，二者口径不一致
- `efinance` 未声明在 `pyproject.toml`，需手动 `pip install efinance`（缺失自动回退）
