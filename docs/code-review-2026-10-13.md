# Sequoia-X v3 代码审查台账

**日期**：2026-10-13
**触发原因**：修复「资金流向」伪信号时连续发现既有问题，用户判断「继续打地鼠不对」，要求先看全貌。
**范围**：核心 Python（`main.py` + `sequoia_x/`）。**不含** `gpfxapp/`（Flutter）、`fund_research/`、各 `run_*.py` 脚本。

---

## 方法与本台账的可靠性

- 由 Claude 用**只读工具**（读取 / 检索）人工完成，**未做逐行完整通读**——`main.py`、`rating/engine.py`、`backtest/engine.py` 等关键文件重点读过，其余为定向检索。
- 原本计划用并行子代理分块扫描，但**被安全分类器故障全程阻断**，故改为人工。
- 每条都标了**如何验证**。标注「**待确认**」的表示只读到间接证据、未最终证实，**不应直接当结论**。
- 「文档对齐」一块**未扫**（见文末）。

### 严重度定义

| 级别 | 含义 |
|---|---|
| 🔴 CRITICAL / HIGH | 会导致**错误结论、错误行为或数据问题** |
| 🟡 MEDIUM | 可维护性 / 一致性缺陷，短期不致错但会累积 |
| 🟢 LOW | 重复、命名、文档滞后 |

---

## 一、台账总表

| 编号 | 级别 | 问题 | 位置 |
|---|---|---|---|
| H1 | 🔴 | 两套并存的策略体系被混用 | `rating/strategies.py` ↔ `strategy/*.py` |
| H2 | 🔴 | 策略名映射含 5 个不存在的类名 | `main.py:721` |
| H3 | 🔴 | 回测未来函数：当日收盘算信号 + 当日收盘成交 | `backtest/engine.py:289,296` |
| H4 | 🔴 | 两条流水线互不相通，`--rating` 不写追踪库 | `main.py:300,1152,1197` |
| H5 | 🔴 | 追踪静默丢弃置信度 <0.5 的推荐，统计口径不标注 | `tracker/review.py:88,182` |
| H6 | 🔴 | IC 历史被重复追加（同日期最多 30 次）+ 周末快照永久卡死 | `signal/quality.py:192,239` |
| M1 | 🟡 | 策略类别 4 份定义、三套命名 | `rating/engine.py:93,345` 等 |
| M2 | 🟡 | 回测交易成本三处不一致，一处漏印花税 | `run_consensus_backtest.py:98` |
| M3 | 🟡 | `max_hold_days` 口径不一：自然日 vs 交易日 | `backtest/engine.py:235` |
| M4 | 🟡 | 止损/止盈仅按收盘判定并收盘成交，未标注为简化 | `backtest/engine.py:238-253` |
| M5 | 🟡 | 股票名解析的 mootdx 分支无超时保护 | `utils/name_resolver.py:14-28` |
| M6 | 🟡 | baostock 重连有三套实现 | `baostock_guard.py` / `factors` / `rating` |
| M7 | 🟡 | `factors` 文档字符串与依赖声明均停留在旧实现 | `factors/__init__.py:92-94` |
| M9 | 🟡 | `_check_data_freshness` 在同一流程里调用两次 | `main.py:332,544` |
| M10 | 🟡 | 推荐模块导入失败被降级为 warning，流程仍报「运行完成」 | `main.py:539-545` |
| M11 | 🟡 | 圆整与定级用了两个不同的数（展示 `-0.0`，判定为负） | `rating/engine.py:596,600` |
| M12 | 🟢 | `x or 0.0` 遇 `-0.0` 会静默变号（待全局排查） | 待查 |
| M13 | 🟡 | `_run_daily` 是死代码（约 220 行），入口「无参数运行」从未被使用 | `main.py:329-545,1197` |
| L1 | 🟢 | 股票名解析在两处重复实现 | `name_resolver.py` ↔ `notify/feishu.py:53` |

---

## 二、详细条目（缺陷类）

### 🔴 H1 — 两套并存的「策略」体系被混用

**现象**：项目里有两套完全独立、名字部分重叠的策略实现。

| | 体系 A：评级策略 | 体系 B：选股策略 |
|---|---|---|
| 位置 | `sequoia_x/rating/strategies.py` | `sequoia_x/strategy/*.py` |
| 形式 | 函数（`ma_volume_strategy:56` …） | 类（`MaVolumeStrategy:13` …） |
| 数量 | **9 个** | **6 个** |
| 分类表 | `rating/engine.py:345 RATING_GROUPS` | `ensemble/consensus.py:6 STRATEGY_CATEGORIES` |
| 用途 | 评级评分 | 融合评分 / `--daily` 选股 |

只有 `maVolume`/`MaVolume`、`turtleTrade`/`TurtleTrade` 两个名字重叠，而它们是**两份独立实现**。

**证据它被当成了一套**：`recommender/reporter.py:144` 的 `label_map` 把两套键塞进同一个字典（既含 `maVolume`/`meanReversion`，又含 `highTightFlag`/`rpsBreakout`）。

**为什么重要**：README 说的「6 个选股策略」和评级实际跑的 9 个不是一回事；任何跨体系的比较都没有共同口径。

**如何验证**：比对 `RATING_GROUPS`（9 键）与 `STRATEGY_CATEGORIES`（6 个类名），看交集有几个。

> **2026-10-15 已按「划清边界 + 除隐患」处理**（不合并）：
> - `reporter.py` 的 `label_map` 拆成 `_RATING_LABELS`（体系A）与 `_SELECTION_LABELS`（体系B）
>   两张表，并去掉 `s.replace("Strategy","")` 兜底——它会把类名剥成 camelCase 的样子，
>   正是「看起来像一套」的来源
> - `main.py` 送进 `SignalGenerator` 的 `strategy_map` 不再虚构 `XxxStrategy` 名，
>   直接传 camelCase 键（见 H2），两条流水线的名字空间从此分野明确
> - README 顶部明确写出这是**两套互不相干的策略实现**，并各自说明驱动哪条链路
>
> 未做：合并成一套（需重写 `--daily`、多策略回测、`--factors` 三条链路，风险大）。

---

### 🔴 H2 — `main.py:721` 的策略名映射含 5 个不存在的类名

```python
"meanReversion": "MeanReversionStrategy", "volumePrice": "VolumePriceStrategy",
"atrChannel": "AtrChannelStrategy", "macdRsi": "MacdRsiStrategy", "maCross": "MaCrossStrategy",
```

这 5 个类**全仓不存在**（`strategy/` 里只有 `MaVolumeStrategy`、`TurtleTradeStrategy` 对得上）。

**影响有限但已确认**：`generator.py` 里 `strategies` 只用于 `len()`（`:245`）、`"+".join()`（`:370`）、存进 `SignalTrade.strategies` 供显示，**从不按名字解析类**。所以不会报错，但飞书报告的策略标签会走 `reporter.py:149` 的 `s.replace("Strategy","")` 兜底，显示成 `MeanReversion`——**看起来像体系 B 的策略，实际是体系 A 的**。

**如何验证**：`grep -rn "class MeanReversionStrategy" .` → 无结果。

> **2026-10-15 已修**：`main.py` 里那张映射表整张删掉——`strategy_map` 直接送 camelCase
> 键（`meanReversion` 等），不再翻译成虚构的类名。兜底值也从 `["RatingEngine"]` 改成
> `["rating"]`，与 `reporter` 的 `_RATING_LABELS` 对得上。
> 同源的两份拷贝也在 `run_confidence_diff.py:38` 与 `run_rating_vs_confidence.py:35`
> —— 那两个是一次性诊断脚本，未改（`grep` 仍能命中，别误当成生产代码）。
> **顺带的好处**：手机端的类别匹配用的就是 camelCase（`stock_detail_page.dart:764`），
> 现在两边名字空间一致了。

---

### 🔴 H3 — 回测引擎存在未来函数

**证据链**：

1. `backtest/engine.py:208-216` — 在 `current_date` 当天运行策略
2. `strategy/base.py:59-60` — `get_data()` 的切片**含当日**：`df = df[df["date"] <= self.reference_date]`
3. `strategy/ma_volume.py:63` — 判放量用**当日全天成交量**：`last["volume"] > last["vol_ma20"] * threshold`
4. `backtest/engine.py:289,296` — 用**同一根 K 线的收盘价**成交：
   ```python
   current_price = float(df_cut["close"].iloc[-1])
   entry_price = current_price * (1 + cfg.slippage)
   ```

**为什么是问题**：收盘价与全天成交量要收盘后才知道。用当日收盘算出「金叉+放量」，再假设自己在同一个收盘价买入——A 股没有 MOC 指令，无法执行。

**方向**：回测收益**系统性偏乐观**。因此「选股无优势」的既有结论**更稳**（真实只会更差）；但引擎跑出的任何**正向**结果都要打折扣。

**注意**：`base.py:55` 注释写着「防止未来信息泄漏」——它只挡住了更晚日期的信息，没挡住这一条，会给人错误的安全感。

**如何验证**：把开仓价改为「次日开盘价」，跑同一段历史，比较收益率差异。

> **2026-10-15 实测**（全市场股票池、`TurtleTradeStrategy`、近一年；用新增的
> `BacktestConfig.fill_mode` 跑同一份数据的 A/B）：
>
> | 指标 | 当日收盘成交（现状） | 次日开盘成交 | 变化 |
> |---|---|---|---|
> | 年化收益 | −18.6% | **−24.0%** | **−5.4pp** |
> | 最大回撤 | −28.1% | −33.3% | −5.2pp |
> | 夏普 | −1.48 | −1.82 | −0.33 |
> | 胜率 | 30.1% | 30.7% | +0.5pp |
> | 交易次数 | 239 | 313 | +74 |
>
> 即：**同一条策略，当日收盘成交把年化虚高了约 5.4 个百分点**，回撤也被美化 5.2pp、
> 夏普美化 0.33。方向与预期一致（偏乐观），量级不小。
>
> 修法已就位：`BacktestConfig.fill_mode`（`"same_close"` / `"next_open"`），
> 入参、出参、涨跌停约束都已按成交日重算；**默认值仍是 `same_close`（未改行为）**，
> 切换需要产品决策。

---

### 🔴 H4 — 两条日常流水线互不相通，只有一条写追踪库

| | `_run_daily`（**默认**，无参数） | `_run_rating`（`--rating`） |
|---|---|---|
| 入口 | `main.py:1197` | `main.py:1152` |
| 选股依据 | 6 策略 → `ConsensusEngine` 融合评分 | `RatingEngine` 评级 |
| 写 `rating_result_*.json` | ❌ | ✅（`:691`） |
| 写追踪库 | ✅（`:525`） | ❌ **完全没有** |
| COS 上传 | 仅 signals + regime（`:535`） | ratings + signals + regime（`:841`） |

定时任务跑的是**无参数版本**（`main.py:300`，`schtasks` 的 `/tr` 里没有参数）。

**为什么重要**：`--review` 只读追踪库（`main.py:1026-1030`）。所以：
- 若实际跑的是 `--rating`（`rating_result_*.json` 每天都有，说明它在跑）→ **`--review` 的命中率里一条都不是 `--rating` 推的**；
- 若跑的是默认模式 → **手机端拿不到刷新过的评级**（`_run_daily` 上传时没带 `rating_results`）。

无论哪种，两条线之间都有东西是断的。**「实盘 37% 盈利」若来自 `--review`，测的可能不是你以为的那批。**

**如何验证**：跑一次 `--rating`，查 `recommendations` 表是否新增行。

> **已确认（2026-10-13）**：用户只跑 `--rating` 且未装定时任务 → 走的是 `_run_rating`，
> 它当时**完全不写追踪库**；`_run_daily` 是死代码（见 M13）。故追踪库为空、`--review` 无数据源。
> 已在 `_run_rating` 补上 `RecommendationTracker(...).record_signals(signals)`。

---

### 🔴 H5 — 追踪模块静默丢弃置信度 <0.5 的推荐

`tracker/review.py:80-89`：

```python
def record_signals(self, signals, ...):
    """记录推荐信号到数据库（只记录置信度 >= 0.5 的信号）。"""
    for sig in signals:
        if sig.confidence < 0.5:
            continue          # 静默跳过，日志只报已记录条数
```

`get_stats()`（`:182-199`）算的是 `recommendations` 表里的「总体命中率」，**没有任何地方标注分母被过滤过**。

**用 2026-10-13 真实数据量化的影响**：推送 15 只里
- 置信度 ≥0.5：**9 只**
- 被丢弃：**6 只**（000690=0.38、002058=0.21、002259=0.25、002640=0.45、300426=0.45、300199=0.48）

**40% 的推荐从未进入统计**，且被丢的恰是置信度最低的那批——若低置信度推荐表现更差，命中率会被系统性高估。

**附带**：`0.5` 是第三个独立阈值体系（评级 `1.5/1.0/0.5`、置信度 `0.75/0.60/0.45/0.25`、追踪 `0.5`），只在这一行出现。

**如何验证**：`SELECT COUNT(*) FROM recommendations WHERE date_recorded='2026-10-13'`，与当日推送条数对比。

> **⚠️ 实测更正（2026-10-13 当日，向用户确认后）**
> 用户实际**只手动跑 `--rating`，从未装过定时任务**。而写追踪库的只有 `_run_daily`
> （`main.py:525`，入口是「无参数运行」）。因此 **`recommendations` 表一直是空的**——
> `--review` 的命中率统计**从来没有数据源**。
>
> 所以本条的性质不是「统计有偏、高估 40%」，而是**「这个功能从未生效」**。
> 上文的「40% 被丢弃」是「假如先修好 H4、又保留该过滤」的假设情形，不是已发生的偏差。
> 另外：用户手上的「实盘 37% 盈利」来自 `run_rec_realcheck.py` / `run_rec_eval.py`
> （直接读 `rating_result_*.json` 重建快照），**不受本条影响**。

---

### 🔴 H6 — IC 历史被重复污染，周末快照永久卡死（**已修**）

`signal/quality.py`。IC 历史是权重调整（`apply_ir_weights` / IC 衰减）的唯一输入，
所以这条直接决定了「哪些策略参与评分」。

**bug 1：`_eval_record` 不持久化 → 同一日期重复追加**

`quality.py:192-194`（原）每次运行都新建空的 `_eval_record`，于是**所有尚未移出
`pending` 的快照，每跑一次就被重新评估并追加一条**。

实测 `output/ic_history.json`：

| 策略 | 条数 | 不同日期 | 同一日期最多重复 |
|---|---|---|---|
| turtleTrade / meanReversion / maCross / kdj | 774 | **41** | **30** |
| maVolume / volumePrice / macdRsi | 252 | 13 | 22 |
| atrChannel | 333 | 21 | 23 |
| fundamental | 506 | 28 | 30 |

**bug 2：周末快照永久卡死**

`trade_dates.index(d_str)` 对非交易日抛 `ValueError` → 走 `continue`（`:239-240`），
**跳过了下面「标记此 lookforward 已计算」那行** → 永不移除、每次运行重算一遍。
`pending` 里躺着 `2026-07-04`(六)、`07-05`(日)、`07-26`(日)、`08-16`(日) 等一批。

**修复**（`signal/quality.py`）：`_eval_record` 持久化进 JSON（`__init__` 初始化、
`_load` 恢复并转 int 键、`save` 写入）；`eval_forward_returns` 不再每次重置它；
新增 `_maybe_cleanup()`；非交易日快照标记为已处理并清理。

**数据清理**：`dedup_ic_history.py`（一次性迁移，默认预览、`--apply` 写入），
按 `(策略, 日期, lookforward)` 保留最后一条并**按日期排序**（原顺序是「首次评估顺序」，
而 `entries[-20:]` 取的是「最近 20 条」，顺序必须真正对应日期）。
结果 **4691 → 462 条（−90%）**，备份留在 `output/ic_history.json.bak-20261013`。

**为什么这条是 🔴**：见 D5——`detect_decay` 数的是**条目**不是日期，
20 条重复就能满足「连续 20 天衰减」，直接触发 IC 衰减降权。

**如何验证**：`python dedup_ic_history.py`（预览）、`python show_ic_ir.py`（去重前后 IR 对照）。

---

## 三、详细条目（一致性与可维护性）
### 🟡 M1 — 策略类别有 4 份定义、三套命名

| 位置 | 命名 | 数量 |
|---|---|---|
| `rating/engine.py:93` `RatingConfig.STRATEGY_CATEGORIES` | snake_case | 9 |
| `rating/engine.py:345` `RatingEngine.RATING_GROUPS` | camelCase | 9 |
| `ensemble/consensus.py:6` `STRATEGY_CATEGORIES` | 类名 | 6 |
| `eval/replay_scores.py:22` `TECH_KEYS` | camelCase | **5** |

前两份靠 `IR_STRATEGY_MAP`（`engine.py:112`）手工对应。**风险**：往 `RATING_GROUPS` 加策略却忘了改 `STRATEGY_CATEGORIES`，`apply_regime` 不会给它设权重，而 `_combine_scores` 用「权重 > 0」过滤 → **静默丢弃，不报错**。

> **2026-10-15 已修（第 1、2 份）**：
> - `RatingConfig.RATING_GROUPS` 成为**唯一来源**（camelCase）；`STRATEGY_CATEGORIES`
>   由 `_snake_categories(RATING_GROUPS, IR_STRATEGY_MAP)` 派生，不再手写
> - `RatingEngine.RATING_GROUPS` 改为 `= RatingConfig.RATING_GROUPS`（同一对象，已测）
> - 派生结果与原值逐类别一致（已用脚本比对）
>
> 未动：第 3 份 `ensemble.consensus.STRATEGY_CATEGORIES` 属于**体系B**（类名、6 个），
> 与评级体系本就不是一回事（见 H1）；第 4 份 `eval/replay_scores.TECH_KEYS` 保留手写，
> 但删掉了同文件里**从未被引用的** `KEY_ATTR`，并注明它必须与「启用且 replay 能算」的
> 集合同步，否则回放校验会静默漏检。

### 🟡 M2 — 回测交易成本三处不一致

| 位置 | 佣金 | 印花税 | 滑点 |
|---|---|---|---|
| `sequoia_x/backtest/engine.py:28-30` | 0.0003 | **0.0005** | 0.001 |
| `run_consensus_backtest.py:98-99` | 0.0003 | **无** | 0.001 |
| `fund_research/backtest/etf_backtest.py:27-28` | 0.0005 | — | 0.0005 |
| `fund_research/core/config.py:20` | 0.001 | — | — |

`run_consensus_backtest.py` 是**给融合评分出结论的脚本**，缺卖出印花税（0.05%）→ 每轮往返少算约 5bp，结果偏乐观。对比：`run_rec_eval.py:415` 明确标注了自己的口径，该脚本没有。

### 🟡 M3 — `max_hold_days` 口径不一致

- `backtest/engine.py:235`：`(pd.Timestamp(current_date) - pd.Timestamp(pos["entry_date"])).days` → **自然日**
- `signal/models.py:33`：`max_hold_days: int = 20  # 最长持有天数（交易日）`

回测里的 20 = 20 自然日 ≈ **14 个交易日**，比信号模型宣称的短约 30%。

### 🟡 M4 — 止损/止盈仅按收盘判定，并用同一收盘价成交

`backtest/engine.py:238-243` 每日只在收盘比较一次 `pnl_pct`，触发后 `:253` 用同一收盘价平仓。盘中跌破 -8% 不会触发，也没有盘中滑点。方向不确定，但**未在任何文档或注释里说明这是简化**。

> **2026-10-15 已修**：止损/止盈改为按**盘中**（当日 `low`/`high`）判定，并在**触发价**成交
> ——这两类是可以预挂的条件单，盘中触发即可成交是标准口径。
> 跳空越过触发价时以当日 `open` 成交（取更差的一边）。
> **时间到期（`time_exit`）不是条件单**，仍与开仓同口径、按 `fill_mode` 决定成交时点，
> 所以「当日收盘成交」与「次日开盘成交」两种口径的差异只作用在开仓与 time_exit 上。
>
> **量化差异未做**（要先跑旧口径做对照，一次全市场回测十几分钟）。若之后要量化，
> 直接看 `exit_reason` 分布里 `stop_loss` 的占比变化即可。

### 🟡 M5 — 股票名解析的 mootdx 分支无超时保护

`utils/name_resolver.py:10-28`：先试 mootdx（**无超时**），失败才降级 baostock（**有 5 秒超时线程**）。先试的那条没防，后试的那条才防。

**为什么重要**：这与今天查到的 akshare 北向接口是同一类风险——数据源不可达时不是报错而是**挂住整个进程**。`get_name()` 在信号生成链路里每只股票都会调（`generator.py:178`）。

**待确认**：mootdx 的 `client.stocks()` 现在是否还能通（K 线接口已死，证券列表已改用 baostock）。

### 🟡 M6 — baostock 重连有三套实现

- `utils/baostock_guard.py` — 全局补丁，替换 `send_msg`（`sequoia_x/__init__.py:5-7` 统一安装）
- `factors/__init__.py:192-196` — 又一份「错误码 → logout+login 重试」
- `rating/engine.py:679-683` — 再一份

guard 的设计意图是唯一出口。**待确认**：三者是互补（socket 层 vs 错误码层）还是重复；若重复，各套的退避/重试次数不一致会很难排查。

> **2026-10-15 已查清并处理**：
>
> - **guard 与另外两处是互补的，层不同**：`baostock_guard` 在**传输层**
>   （patch `socketutil.send_msg`：recv 超时、断线重发），另两处在**错误码层**
>   （socket 没抛异常、但库返回了非零网络类 `error_code`）。两层都要保留。
> - **但另两处是同一个重试循环的重复拷贝**：`rating/engine.py` 与
>   `factors/__init__.py` 各写了一份**逐行相同**的实现（只差一个节流），
>   网络类错误码集合也各写了一遍。
>   → 已抽到 `sequoia_x/utils/baostock_guard.py::query_with_reconnect`
>   （`BS_NETWORK_ERRORS` + 3 次重试 + 1.0×(n) 退避 + 可选节流）。
>   `RatingEngine` 用 `throttle_interval=0.15`（保持原节流），
>   `FactorCalculator` 用 0（保持原频率）。两处都退化成一行委托，调用点未动。
> - 已用桩验证：网络类错误码重试到成功（3 次）、正常错误码（无数据）只调 1 次不重试、
>   始终失败时 3 次后返回。

### 🟡 M7 — `factors` 的文档与依赖声明停留在旧实现

```python
# factors/__init__.py:92-94
  - 财务数据: akshare (stock_financial_abstract_ths 或 stock_yjbb_em)
  - 市值: akshare stock_info_a_code_name
```

实际代码只 import **baostock**（`:196, :225`），全文件无 akshare。

**同一根因的另两处**：`pyproject.toml:26-28` 的 `[factors]` extra 只装 akshare，而主包已无任何代码引用它；`README.md:262` 的安装说明跟着错。

### 🟡 M9 — `_check_data_freshness` 被调用两次

`main.py:332`（开头）与 `main.py:544`（结尾）调用完全相同，输出不区分，看日志会以为跑了两遍。

### 🟡 M10 — 推荐模块失效仍报「运行完成」（**已修 2026-10-15**）

`main.py:539-545`：`except ImportError` 与 `except Exception` 都只 `logger.warning`，然后继续走 `_check_data_freshness` 和 `logger.info("日常模式运行完成")`。**核心功能整体失效、退出码仍为 0**，定时任务不会告警。

> **2026-10-15 已修**。新增 `main.CoreStepFailure(RuntimeError)`，把它抛出去让 `main()`
> 里已有的兜底 `except → sys.exit(1)` 生效：
> - `_run_daily` 的信号生成两处 `except`：`warning` → `error` + `raise`
> - `_run_rating` 的 COS 同步：**顺带发现更隐蔽的一条**——`_upload_to_cos` 的**返回值被整个
>   丢弃**，而它会返回 `ok=False` 表示「部分文件没传上去」。也就是说踩到部分失败时
>   连一条 warning 都没有。现在改为检查返回值，失败即 raise
> - `_run_daily` 里的 COS 调用同样补上了返回值检查
>
> 未改：`_run_backtest` 每个策略的失败已经 `logger.error` 并记进报告（`main.py:911`），
> 属于正常降级，不动。

### 🟡 M11 — 圆整与定级用了两个不同的数（**已修 2026-10-15**）

`rating/engine.py:596` 和 `:600`：

```python
r.total_score = round(total, 1)                              # 展示用：圆整
r.rating = self._calculate_rating(r.trend_passed, total)     # 定级用：未圆整
```

当 `total` 落在 (−0.05, 0) 时，`round(total, 1)` 得到 **`-0.0`**（界面上就是「0 分」），
但 `total < 0` → 判 `avoid`。

**实测影响**：3879 只（占全市场 **77.7%**）处于这个缝隙——界面显示 0 分、系统判「回避」。
（注明：该数字是 2026-10-13 的。见 D3 复测注——这个缝隙现在只有 **7 只**。）

**修法二选一，效果不同**：

- 定级改用圆整后的值 → 这些股票从 `avoid` 变 `attention`，评级分布变动
- 展示不圆整（或对 `-0.0` 做归一） → 界面显示真实的微弱负值

**这是行为改变，需要产品决策**，不是纯修 bug。

> **已修（方案一，2026-10-15）**：定级改用**存下来的那个数**——「显示什么就按什么定级」。
> `analyze()`（`:474`）与 `_cross_sectional_standardize()`（`:622`）两处同源写法都改了，
> 诊断脚本 `run_rating_neutral_ab.py` 同步。
>
> **实测影响**（一次扫描同时算新旧两套定级；强熊 / 4993 只）：
>
> | | 旧规则 | 新规则 | 差 |
> |---|---|---|---|
> | strong_buy | 0 | 2 | +2 |
> | buy | 3 | 1 | −2 |
> | watch | 1035 | 1092 | +57 |
> | attention | 1306 | 1256 | −50 |
> | avoid | 2649 | 2642 | −7 |
>
> **共 66 只变动（1.32%），全部是上移**：`attention→watch` 57、`avoid→attention` 7、
> `buy→strong_buy` 2。其中 `avoid→attention` 的 7 只正是「显示 `-0.0` 却判回避」的那批；
> 另外两个方向说明**受影响的不止 0 这个阈值**，0.5 / 1.0 / 1.5 同样有边界股。
>
> **遗留（展示层，未并入本次）**：`round(-0.03, 1)` 得到 `-0.0`，界面仍会显示「总分 -0.0」。
> 它现在与评级自洽了（`-0.0 >= 0` → attention），但显示成 `-0.0` 本身不美观，
> 是否归一成 `0.0` 另定。

### 🟢 M12 — `x or 0.0` 遇 `-0.0` 会静默变号（待全局排查）

`-0.0` 在 Python 里是 falsy，`-0.0 or 0.0` 得到 `+0.0`。本台账的诊断脚本就因此
一度得出「代码与数据矛盾」的错误结论。

**待确认**：项目代码里是否存在同类写法（`or 0.0` / `or 0` 接可能为 `-0.0` 的浮点），
若有且用于判断分支，会产生静默的符号错误。未排查。

### 🟡 M13 — `_run_daily` 是死代码

`main.py:329-545`（约 220 行）唯一的入口是 `main()` 的兜底分支（`main.py:1197`，
即「无参数运行 `python main.py`」）。用户实际**只手动跑 `--rating`，从未装过定时任务**，
因此这条路径在生产中从未执行。

**它不是无害的死代码**：

- 它是**唯一**写追踪库的路径（`main.py:525`）——H5 的「追踪库一直是空的」正源于此
- 它包含半套生产逻辑（6 策略 → `ConsensusEngine` 融合评分 → 信号 → 飞书 → COS）
- 它和 `_run_rating` 各有独立的推送、上传内容，容易误判「哪条是真的」

**建议**：明确它是「备用回退路径」并写进 README，或者删掉。二选一，不要维持现状——
否则以后每次读 `main.py` 都要先判断这两条线谁生效。

### 🟢 L1 — 股票名解析两处重复

`utils/name_resolver.py`（带缓存 + mootdx→baostock）与 `notify/feishu.py:53-60`（另一套 baostock 查名 + 失败兜底用代码）。动机合理，但同一件事两份实现、两套失败策略。

---

### 🧹 2026-10-15「顺手清理」批次结果

逐条核实后的处置（未列出的项见各自条目的「已修」注）：

| 条目 | 结论 |
|---|---|
| **M2** 回测成本不一致 | **已修** `run_consensus_backtest.py`：补上卖出印花税 `0.0005`（两处平仓逻辑）。注：该脚本内部还有**两段几乎相同的回测循环**、且同样用「当日收盘算信号 + 同日收盘成交」，未动，建议后续合并重写 |
| **M3** `max_hold_days` 自然日 | **已修** `backtest/engine.py`：持仓天数改按**交易日**算（`hold_days = i - pos["entry_idx"]`），与 `signal/models.py` 声明的「20 交易日」一致。此前 20 自然日 ≈ 14 交易日 |
| **M4** 止损止盈仅按收盘判定 | **未改，需要你定**。这是建模选择（盘中触发 vs 收盘触发、成交用触发价还是收盘价、跳空怎么处理），且与刚上的 `fill_mode` 有交互。当前只在代码里没有说明这是简化——**已在上方标注** |
| **M5** mootdx 无超时 | **已修** `utils/name_resolver.py`：mootdx 抽成 `_try_mootdx()` 并加 8 秒超时。此前顺序是反的——先试的没保护、后试的 baostock 反而有 |
| **M6** baostock 重连三套 | **未改，需要先判互补还是重复**。需要实际读三处（`utils/baostock_guard.py` socket 层 / `factors/__init__.py` 错误码层 / `rating/engine.py` 错误码层）再定，不是能顺手改的 |
| **M7** `factors` 文档过时 | **已修**：`factors/__init__.py` 的数据来源注释改成 baostock。**但台账原文有一处不准**——`[factors]` extra 并非死配置，`fund_research/analysis/screener.py:141` 仍在用 akshare；已在 `pyproject.toml` 与 README 标注清楚，**不能删** |
| **M9** `_check_data_freshness` 调两次 | **已修** `main.py`：加 `phase` 参数，`_run_daily` 的两处分别标「同步前 / 同步后」。日志里不再出现两行一模一样的输出 |
| **M12** `x or 0.0` 遇 `-0.0` | **已排查完毕，无需改动**。全仓 4 处：`factors/__init__.py:266`、`rating/engine.py:803/804`（都是 `eps`/`npMargin`，而使用它们的判断是 `if eps and eps > 0`——`0.0` 与 `-0.0` 同为 falsy，行为相同）；`tracker/review.py:222`（仅显示层）。**没有一处在符号敏感的分支上** |
| **L1** 名称解析两处重复 | **保留**。`feishu.py` 那份是「推送失败时的兜底」，与 `name_resolver` 的缓存批量查询不是同一场景；合并收益小、风险大于收益 |

---

## 四、设计层问题（不是缺陷，但影响更大）

以下为 2026-10-13 实测发现，已记入 `CHANGELOG.md` 第七节。

### D1 — 强熊下评分与趋势软过滤对冲

`REGIME_BIAS["强熊"]`（`regime/__init__.py:73`）把 trend/momentum 权重压到 **0.00**，有 IC 数据支撑（`output/weight_validation.json`：趋势/动量类 IC 为负、反转类为正）。但「趋势过滤」是**独立硬门**。

**实测后果**：总分 ≥1.5 的 **446 只**股票全部因趋势不通过被压到 `watch`，**一只都进不了推送**；实际推送的 15 只来自总分 1.4 档。因「总分高」与「趋势通过」在此权重下负相关，推送名单等价于**「在趋势通过的股票里挑反转分最低的」**。

**权重本身不该改**——要改的是它与过滤门的交互，或让推送口径与评分口径一致。

> **2026-10-15 复测（D5 之后）**：上述「446 只 / 一只都进不了推送」**已不成立**。
> 当前基线（`rating_result_20261014.json`，市场状态 = 强熊）：总分 ≥1.5 的只有
> **34 只**，其中 **2 只**评级为 `buy`；推送 Top15 里含 3 只 `buy`。
> 数字的移动主要发生在 **2026-10-13 当天**（IC 历史去重让 `apply_ir_weights` 换了
> 权重组合），**不是 D5 造成的**——D5 在同一份快照内的效果是「总分平均 +0.003、
> 按总分排序的 Top15 15/15 不变」。

### D2 — 评级与置信度是两把尺子

- 评级 `total_score`：截面 Z-score 合成（`rating/engine.py:568` winsorize+Z-score），阈值 `1.5/1.0/0.5`
- 置信度 `confidence`：6 维度绝对加权 0~1，阈值 `0.75/0.60/0.45/0.25`

**实测**：两者 Top15 **只重叠 2 只**；置信度最高的 94 只在评级体系里**中位排名 3435/4984**，仅 6 只进评级前 100。

> **2026-10-15 复测（当前基线，一次全市场扫描算 confidence）**：
>
> | 口径 | 评级 Top15 ∩ 置信度 Top15 | 置信度 ≥0.75 那批在评级里的中位排名 |
> |---|---|---|
> | 共振项旧口径 | **0/15** | 1841 / 4993 |
> | 共振项新口径（D4 改动后） | **0/15** | 3346 / 4993 |
>
> **两把尺子完全不相关**，而且比 10-13 那次（重叠 2/15）更糟。
> 即：按评级推出去的股票，其置信度等级与评级没有对应关系——用户会在同一张卡片上
> 同时看到「推荐」和一个很低的 `[Lv.X]`。怎么统一（或怎么把两者含义在界面上说清）
> 仍待决定。

### D3 — 评级分布极端：87% 是 `avoid`（**已查明**）

实测分布：`avoid=4348`(87%)、`watch=502`、`attention=101`、`buy=42`、`strong_buy=0`（全市场 4993 只）。

**查明后的真相**：`avoid` = 469 只明显负分（≤−0.1）+ **3879 只微弱负分（−0.05 ~ 0，四舍五入后为 `-0.0`）**。
`total_score` 的 P10 / P25 / 中位 / P75 **全部是 `-0.0`**。

两个原因叠加：

1. **稀疏策略做 Z-score，把「未触发」编码成「负分」。**
   `engine.py:465` 把所有启用策略的原始分（含 0）都放进 `raw_strategy_scores`，
   `:586-588` 对该向量做 winsorize + Z-score。未触发的股票原始分为 0，
   而截面均值被少数触发者拉高 → **Z(0) < 0**。实测 `meanReversion` **89.6% 为负**，
   正好 ≈ 1 − 触发率。强熊又把 0.68 权重给反转类，于是大多数股票拿到微弱负分。
2. **圆整与定级用了两个不同的数**（见 M11）。

**影响**：「回避」吞掉 87% 的市场，失去区分度——「完全没有信号」（−0.03）与
「真的很差」（−3.9）归成同一档；用户在界面上看到「总分 −0.0 / 回避」，无法分辨是哪种。

**如何验证**：`python run_rating_scale_check.py`（读 `output/rating_result_*.json`，秒出）。

> **2026-10-15 复测（D5 之后）**：本条的数字**已全部失效**。当前基线
> （`rating_result_20261014.json`）：
>
> | | 本条原值(10-13) | 当前值(10-14) |
> |---|---|---|
> | avoid | 4348（87%） | **2649（53.1%）** |
> | watch | 502 | 1035 |
> | attention | 101 | 1306 |
> | buy | 42 | 3 |
> | 显示 `-0.0` 但判 avoid | 3879 | **7** |
> | total_score 中位 / P90 / max | `-0.0` | −0.20 / +0.80 / +1.60 |
>
> 失效时点同样是 **2026-10-13 当天**，不是 D5（理由见 D1 的复测注）。
> **「稀疏策略把未触发编码成负分」这个机理仍然成立**——`meanReversion` 仍有 76.5% 为负，
> 只是权重变化让负分的**幅度**改了，所以显示层不再挤在 `-0.0`。
>
> ⚠️ **另一个必须记住的点**：本条引用的 `avoid=4348` 与该文件
> `output/rating_result_20261013.json` 实测的 **2619** 对不上——因为该文件在同一天
> 23:04 被后一次运行**覆盖**过（同日 23:07 还覆盖过一次 `_20261014`）。
> **引用评级快照必须带时间戳，`rating_result_<日期>.json` 这个说法本身不唯一。**
>
> 两个成因里的「**圆整与定级用了两个不同的数**」（M11）已于同日修复，见下。

**对照实验**：`run_rating_neutral_ab.py` 把「未触发（原始分 0）的股票剔出标准化样本」
跑了一遍 A/B（股票池与权重均复刻生产：基线分档与 `rating_result_*.json` 完全一致）：

| 档位 | 基线 | 中性化后 | 变化 |
|---|---|---|---|
| buy | 42 | 13 | −29 |
| watch | 502 | 132 | −370 |
| attention | 101 | 4389 | +4288 |
| avoid | 4348 | 459 | −3889 |

**中性化做了两件方向相反的事**：把 3889 只从 `avoid` 释放成 `attention`，
但**把头部压垮**（最高分 2.00 → 1.20）。原因是剔掉占 86% 的零后样本均值升高，
已触发股票的 Z 分被整体压缩——**现在头部的高分，有一部分是靠「把没触发的股票
也算进样本、把均值拉低」垫出来的**。

**因此结论是：两个方案都不是答案。** 现状让 87% 落进「负」，中性化让 79% 落进「0」，
只是把同一坨退化质量从 `avoid` 搬到 `attention`。真正的问题见下条。

> 注：本条的定位过程里，诊断脚本自身出过一次 bug——`float(x.get("total_score") or 0.0)`
> 会把 `-0.0` 变成 `+0.0`（`-0.0` 是 falsy），一度得出「代码与数据矛盾」的错误结论。
> 项目代码在这一处是自洽的。但同类写法值得全局排查（见 M12）。

> ⚠️ **本文档写作期间又发现 H6：IC 历史的重复污染会让权重组合发生不可复现的变化。**
> 上面这张表是在「2 策略权重（meanReversion + fundamental）」下得到的；
> 去重后 next `--rating` 的权重会变成 4 个策略（见 D5），**这张表的数值随之失效**。

### D4 — 「多策略共振 0.30 档」全市场从不触发（**2026-10-15 复测：已翻转**）

实测多策略共振分布 `0.15=515 只，0.10=4469 只`——**没有一只有 ≥3 个策略**。`README.md` 说的「≥3 个策略选中满分」是永不发生的情况。

> **2026-10-15 复测：0.30 档从「全市场 0 只」变成「2263 只」，但中间档被挤空。**
>
> 当前快照（`rating_result_20261014.json`，4993 只）里「值为正」的策略个数分布：
>
> | 正分策略数 | 只数 | 落入的共振档 |
> |---|---|---|
> | 3 | 2263（45.3%） | 0.30 |
> | 2 | 84（1.7%） | 0.15 |
> | 1 | 2422（48.5%） | 0.10 |
> | 0 | 224（4.5%） | 0.10 |
>
> 对比 10-13 的 `0.15=515 / 0.10=4469`：**0.30 档从不可达变得有 45% 落入**，
> 而 0.15 档几乎消失。成因是 D5 把 `fundamental` 从清零恢复到 0.32，活跃策略由 2 变 3。
>
> > ⚠️ **更正**：最初我按 `strategy_scores` 的**键数**统计，得出「每只都恰好 3 个策略、
> > 该维度恒为 0.30 常数」——**那是错的**。生成器数的是「值为正的个数」
> > （`generator.py:245` 的 `len(strategies)`，而 `strategies` 已按 `sv > 0` 过滤）。
> > 键数恒为 3（3 个启用策略各一个键），但值可以为负。
>
> **真正的设计问题**：`strategy_count` 数的是 `strategy_scores` 里值为正的个数，而那是
> **类内均分后的 `ortho` 分**（`_combine_scores` 给同类内每个策略赋同一个类别均分）——
> 于是**一个类别为正就要按它的成员数计**（reversal 有 `meanReversion` + `kdj` 两个成员，
> 一个正类别直接贡献 2）。「3 个策略」实际可能只是「2 个类别为正」。
> 要么改成按**原始分 `raw_strategy_scores` > 0 的策略数**计（每策略独立，不被成员数放大），
> 要么改成按**类别数**计。
>
> **2026-10-15 已按前者改**（`main.py` 两处 `strategy_map` 的构造改用 `raw_strategy_scores`），
> 一次全市场扫描实测档位分布：
>
> | | 0.30 档 | 0.15 档 | 0.10 档 |
> |---|---|---|---|
> | 旧口径（ortho 类内均分 > 0） | 2263（45.3%） | 84（1.7%） | 2646（53.0%） |
> | **新口径（原始分 > 0）** | **410（8.2%）** | **1582（31.7%）** | **3001（60.1%）** |
>
> 中间档从 1.7% 恢复到 31.7%，这个维度终于能区分股票了。
> **副作用**：它同时让 D2 的裂口略微变大（见下）——置信度 ≥0.75 那批在评级里的
> 中位排名从 1841 掉到 3346。

### D5 — 权重由「噪声 IC 的符号」决定，不可复现（**最根本的一条**）

`apply_ir_weights`（`rating/engine.py:149`）的逻辑是：**IR <= 0 的策略权重直接清零**。
而 IR = avg_ic / ic_std，全部来自 H6 里那份被重复污染的历史。

**去重前后对照**（`python show_ic_ir.py`）：

| 策略 | 去重前 n / ir_60 | 去重后 n / ir_60 | 判定 |
|---|---|---|---|
| fundamental | 506 / **+0.452** 保留 | 49 / **−0.181** 归零 | **翻转** |
| kdj | 774 / **−0.915** 归零 | 75 / **+0.098** 保留 | **翻转** |
| turtleTrade | 774 / **−0.488** 归零 | 75 / **+0.004** 保留 | **翻转** |
| meanReversion | 774 / +1.084 保留 | 75 / +0.599 保留 | — |
| volumePrice | 252 / +0.396 保留 | 26 / +0.379 保留 | — |

**9 个策略里 3 个的保留/归零判定，仅仅因为去重就翻转了。**

去重后 `apply_ir_weights` 会给出的权重：
`meanReversion 0.5548 / volumePrice 0.3511 / kdj 0.0907 / turtleTrade 0.0033`

两点值得盯：

1. `turtleTrade` 的 IR 是 **+0.0033**（几乎为零），却因为「符号恰好为正」而拿到权重；
   `kdj` 是 +0.098。**这些决定生死的数字全落在 ±0.2 以内——纯噪声。**
2. `maCross` 更典型：`ir_20d = +0.1734` 为正，但因样本 ≥60 而取 `ir_60d = −0.0624` → 归零。
   **换一个窗口，它就由「好策略」变「坏策略」。**

另有旁证：两套独立 IC 计算（本文件的 `ic_history` 与 `output/weight_validation.json`）
在 **turtleTrade / maCross / kdj / fundamental 四个策略上符号相反**——
同一批数据、两套算法、结论打架，说明这些 IC 的符号没有意义。

**结论**：**只要还用「IR 的符号」做硬清零，权重就是掷骰子。** 去重只是让这个随机
过程换了一次随机结果。今天之前所有评级结论（含 D1–D3 的实测），都是在某一个
随机抽出的权重组合上得到的。

**修复方向**（未做，需产品决策）：IC 落在噪声区间时不应硬清零，应退回
`apply_regime` 的权重。需先定义「多大算有信号」（例如 |IR| < 0.5 视为无效）。
注意 `apply_ir_weights` 已有 `len(ir_values) < 3: return` 的兜底思路，但阈值形同虚设
——本次 3 个正 IR 就足以触发整套清零。

---

## 五、建议处理顺序

### 已完成（2026-10-13）

- **H5** — `tracker/review.py` 删除 `confidence < 0.5` 过滤；新增 `reporter.DISPLAY_TOP_N`
  统一「`print_console` 20 / `push_feishu` 15 / tracker 50」三处不一致的截断
- **H4** — `_run_rating` 补上 `record_signals`（此前追踪库为空、`--review` 无数据源）
- **M13** — `_run_daily` 标注为备用路径（`main.py` docstring + README）
- **H6** — `_eval_record` 持久化 + 非交易日快照清理；`dedup_ic_history.py` 清理历史
  （4691 → 462 条），备份 `output/ic_history.json.bak-20261013`

### 待处理（按优先级）

1. **D5**（权重由噪声 IC 的符号决定）— **最根本**。不修，后面所有评分调整都建立在
   一个随机抽出的权重组合上。需要你先定「多小算噪声」（建议 |IR| < 0.5 视为无效，
   退回 `apply_regime` 的权重）
2. **H3**（回测未来函数）— 决定所有回测结论的可信度；建议先做「次日开盘成交」对照实验
3. **M10**（推荐模块失败仍报「运行完成」）— 改成非零退出码或飞书告警
4. **D3 + M11**（评分尺度 / 圆整与定级不一致）— **等 D5 定了再做**，否则又是白调
5. **H1 / H2 / M1**（策略体系混乱）— 需要设计决策，不是修 bug
6. **D1 / D2 / D4** — 单独立项
7. M2–M9、M12、L1 — 顺手清理

### 为什么 D5 排第一

今天所有的实测——D1 的「446 只高分股被挡」、D3 的「87% avoid」、中性化对照实验的
分布变化——都是在**某一个随机抽出的权重组合**上得到的。去重让 9 个策略里 3 个的
判定翻转，说明这个组合不可复现。**在修好权重的确定性之前，调评分是白费。**

### 本次新增的只读工具

| 脚本 | 用途 |
|---|---|
| `run_rating_scale_check.py` | 总分分布、原始分稀疏性、rating×总分交叉表（秒出） |
| `run_rating_neutral_ab.py` | 「未触发中性化」的 A/B 对照（复刻生产股票池与权重，两次全市场扫描） |
| `show_ic_ir.py` | 去重前后的 IR 与归零判定对照 |
| `dedup_ic_history.py` | IC 历史去重迁移（默认预览，`--apply` 写入） |

---

## 六、未覆盖范围

- ~~**文档对齐**：`README.md` / `CHANGELOG.md` 与代码的逐项核对~~ → **已完成，见第七节**
- `gpfxapp/`（Flutter 手机端）、`fund_research/`、`run_*.py` 脚本**未审查**
- `rating/engine.py`、`signal/generator.py` 的策略内部算法**未逐行核验**（只查了口径与边界）
- 子代理分块扫描因工具通道故障未能执行，本次为人工定向审查，**覆盖率低于一次完整审查**

---

## 七、文档对齐（2026-10-13 补扫）

逐项核对 `README.md` 的声明与代码实际。

### ✅ 核对**正确**的部分（值得记一笔）

- `REGIME_EXPOSURE` 仓位表 100/70/50/30/15 —— 与 `regime/__init__.py:49-55` 一致
- `REGIME_BIAS` 五档类别权重表 —— 与 `regime/__init__.py:68-74` 完全一致
- 「默认权重（2026-08 校准）」9 项 —— 与 `rating/engine.py:81-89` 逐项一致
- 置信度 6 维度表与附加修正（−0.13~+0.05 / ±0.10 / ±0.10）—— 与 `generator.py` 一致
- 数据源说明（新浪快照/baostock/efinance/mootdx/基准指数 `sh.` 前缀）—— 与代码一致
- 因子验证依据「5206只 / 584个交易日」—— 与 `output/factor_validation.json` 一致

### 🟡 DOC1 — 评级阈值表声称的分位数与实际差 2.6~8 倍

`README.md:152-158` 与 `rating/engine.py:827-828` 的注释都写「阈值对齐全市场评分分布」：

| 阈值 | 声称 | 实测（2026-10-13，4993 只） | 差 |
|---|---|---|---|
| ≥1.5 (S) | 前 0.2% | **0.00%**（0 只） | 该档从未出现 |
| ≥1.0 (A) | 前 7% | **0.84%**（42 只） | 少 8.3 倍 |
| ≥0.5 (B) | 前 28% | **10.90%**（544 只） | 少 2.6 倍 |
| <0 (回避) | 无定义 | **87.08%**（4348 只） | 无分位含义 |

**最可能的原因**：`apply_ir_weights` 把 9 个策略清到只剩 2 个，综合分的取值范围被压缩
（最高分 2.00 封顶），**而阈值没跟着调**。参见 D3 / D5。

### 🟡 DOC2 — 「PC端每晚21:00自动运行」与实际不符（**已修 2026-10-15**）

`README.md:29` 写「PC端 (每晚21:00自动运行)」，`:308-310` 给出
`--install-schedule`。但用户**从未安装定时任务，只手动跑 `--rating`**。
更麻烦的是：**装了那个任务跑的是 `python main.py`，即备用回退路径 `_run_daily`**
（见 M13），不是生产实际用的 `--rating`。照着 README 装，会得到一条与现在不同的流水线。

> **修法**：①`_install_schedule`（`main.py:292`）改为安装
> `python main.py --rating`，照 README 装得到的就是生产那条流水线；
> ②README 三处表述改掉「每晚21:00自动运行」的既成事实口吻，改为「可选，本机目前没装」。
> 顺带确认：`Get-ScheduledTask` 查无 `Sequoia-X选股`，确实是**从未安装**。

### 🟢 DOC3 — 「按总分取前15名」不准确（**已修 2026-10-15**）

`README.md:32`。实际排序键是 **`(评级类别, 总分)`**（`rating/engine.py:912`），
今天已在 `main.py` 改成 `(评级类别, 总分, 置信度)`。纯按总分排会选出完全不同的一批。

> **修法**：README 改为「按「评级类别 → 总分 → 置信度」排序取前15名」，
> 与 `main.py:826-832` 的实际口径一致。

### 🟢 DOC4 — 「多策略共振 ≥3个策略选中满分」描述的是永不发生的情况

`README.md:168`。实测全市场 0 只有 ≥3 个策略（最多 2 个）。即 D4。

### 🟢 DOC5 — `REGIME_BIAS` 的位置写错

`README.md:136` 写「`rating/engine.py` 的 `REGIME_BIAS`」。实际定义在
`regime/__init__.py:68`，`engine.py` 只是 `from sequoia_x.regime import REGIME_BIAS` 转发。

### 🟢 DOC6 — 代码注释里的风险控制区间写错

`signal/generator.py` 的注释写「风险控制修正（-0.10 ~ +0.05）」，但
ATR −0.05 / VaR −0.03 / 回撤 −0.05 可叠加，实际下限是 **−0.13**。
（README 写的 −0.13 是对的，代码注释错了。）

### 🟢 DOC7 — 手机端首页条数（**待确认**）

`README.md:215` 说首页显示「前10只」，而 `push_feishu` 用的是 `DISPLAY_TOP_N = 15`。
手机端读的是 `signals/today.json`，其条数取决于 `cloud.upload_signals` 是否截断——
**未核实**。

### 🟢 DOC8 — 「日常模式」仍作为正常选项呈现

`README.md:288`「或日常模式（增量更新 + 6策略选股 + 推送）」。
虽然 `:316` 已改成「备用回退路径」，但 `:285` 那句「推荐」紧挨着 `:288` 的日常模式，
读者会以为两者等价。
