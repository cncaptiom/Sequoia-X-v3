# gpfxapp 手机端数据层审查（2026-10-15）

手机端此前一直不在审查范围内。本次专项排查 **PC 上传的数据 ↔ 手机端解析/合并/兜底** 这条链路。
只读审查，未改动任何代码。

> 方法与置信度：由子代理做全库扫描，**关键条目另行逐条回源核实**（下表逐条标注）。
> 标「已核实」的，是本次实际读过源文件确认的。

## 结论先说

配置同步这条链路**是通的**：`StrategyConfig.fromJson` 期望 camelCase，PC `to_dict()`
输出的正是 camelCase；`AppTheme.scoreS/A/B` 也确实等于 PC 的 `1.5/1.0/0.5`。
**问题不在于常量的值写错，而在于同一个常量被用在两种量纲的分数上。**

---

## 🔴 1. 本地离线分数与 PC 分数共用阈值，但量纲完全不同（已核实）

- PC `total_score` 是**截面 Z 合成**，实测全市场范围 −2.9 ~ +1.6（中位 −0.2）
- 本地 `analysis_engine.dart:46-66`：`totalScore = Σ(原始整数分 × 归一化权重)`，
  原始分是 `score.add(3)` / `score.add(-3)` 这类 ±1~5 的**整数**
- 两者都用 `app_theme.dart` 的 `1.5/1.0/0.5` 定档

**后果**：PC 数据缺失时（离线、或当天没跑 `--rating`），本地分数极易 ≥1.5 直接判 S 级，
而这个 S 与 PC 的 S 不是一回事。

## 🔴 2. 本地引擎不算 fundamental，但它现在占 PC 权重 32%（已核实，比原报告更具体）

- 本地只实现 8 个策略；`analysis_engine.dart:50-61` 的 `weights` 字典**从不放 fundamental**
- `strategy_config.dart` 里 `weightFundamental` 字段、`toJson`、`fromJson` **全都有**
  （`:83 / :188 / :239`）—— 也就是说 PC 的 `weightFundamental` 能同步下来，
  **但本地打分从来不用它**

**后果**：D5 之后 PC 强熊下 fundamental 拿 0.32，本地对应位置是空的。
即使配置同步成功，本地分数的构成也与 PC 不同，**无法对齐**。

## 🟠 3. `mergePcData` 混用两个数据源（已核实）

`pc_merge.dart:32` 写的是 `signals: original.signals` —— 合并后 `strategyScores`
是 PC 的 Z 分，`signals` 仍是本地生成的。而 `stock_detail_page.dart:841-846` 用
`analysis.signals` 数 bullish/bearish、用 `analysis.strategyScores` 算类别均分。

**后果**：类别条的「积极/关注/观望」由「PC 分数 + 本地信号」共同决定，两者可能互相矛盾。

## 🟠 4. 同一个文件里阈值既有字面量又有常量（已核实）

- `home_page.dart:256-261` `_buildPicksSummary`：`sc >= 1.5 / 1.0 / 0.5` **字面量**
- `home_page.dart:371-377` 卡片定级：`AppTheme.scoreS / scoreA / scoreB` 常量

**后果**：改阈值时漏改统计行，首页的「S x / A x / B x」会与卡片等级不一致。

## 🟠 5. 手机端有第三份 regime 权重表（已核实）

`stock_detail_page.dart:784-790`：

| 状态 | 手机端 趋势/动量/反转 | PC `REGIME_BIAS` |
|---|---|---|
| 强牛 | 0.43 / 0.22 / 0.35 | trend 0.22 / momentum 0.12 / **reversal 0.34 / fundamental 0.32** |
| 强熊 | 0.05 / 0.20 / 0.75 | trend 0.00 / momentum 0.00 / reversal 0.68 / fundamental 0.32 |

手机端只有三类、没有 fundamental，数字也全部不同。
**后果**：类别条上展示的「建议权重」与 PC 实际用的权重对不上。

## 🟡 6. `macdRsiEnabled` 默认值与 PC 相反（已核实）

`strategy_config.dart:143` 默认 `true`，PC `RatingConfig.macd_rsi_enabled = False`
（`rating/engine.py:75`，注明「回测确认：IR 最差」）。
配置同步只在 `pc_data_provider.dart:337` 的 `!hasCustomizedStrategy()` 时才执行。

**后果**：自定义过设置的用户，本地 `macdRsi` 一直参与打分，而 PC 根本不算它。

## 🟡 7. `mergePcData` 三个调用点各自判断（已核实）

`stock_detail_page.dart:213 / 236 / 259` 各自判断 `pcRatingX != null` 再合并，
任一为 null 就退回本地。

**后果**：同一页面不同区块可能一个用 PC、一个用本地——与历史上「首页关注 / 详情页回避」
是同一类问题。

## 🟢 8. PC 侧 `top_picks` 的兜底标签用置信度冒充等级（未逐条核实，影响低）

`main.py` 的 `rating_label_map.get(s.symbol, "S级" if s.confidence >= 0.75 ...)` ——
兜底时用置信度阈值冒充评级标签，而手机端 `home_page.dart:371` 是按**总分**定级。
实际中 `top_picks` 的 symbol 基本都在 `rating_map` 里，兜底很少触发。

---

## 建议的处理顺序（未执行）

1. **第 1、2 条**是根因：本地分数与 PC 分数不是同一把尺子。要么让本地引擎改用与 PC
   相同的口径（含 fundamental），要么**本地分数不要复用 S/A/B 标签**——单独一套命名，
   并在界面上标明「本地估算」。
2. 第 3、7 条是同一类：合并时两个字段来自不同源。合并要么整体用 PC、要么整体用本地。
3. 第 4、6 条是小的收敛：阈值统一到 `AppTheme` 常量；`macdRsiEnabled` 默认值对齐。
4. 第 5 条：手机端的 `regimeWeights` 要么删掉（PC 已同步 `strategy_bias`），
   要么改成从 PC 同步。
