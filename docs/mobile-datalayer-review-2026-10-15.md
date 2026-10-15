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

## 处理进展（2026-10-15）

用户选定：①本地分数不复用 S/A/B（选小的改法）、②合并口径、③删 regimeWeights，
但执行时发现 ②③ 比原设想复杂，**本轮只做了 ①**。

### ✅ 已做：本地分数不再复用 PC 的 S/A/B 标签

- `home_page.dart` 的推荐卡片：新增 `hasPc` 判断（卡片本来就会去查 PC 评级），
  PC 缺失时档位标签改为 **`本地优` / `本地中` / `本地弱`**，不再显示 S/A/B
- `stock_detail_page.dart` 的 `_buildDecisionCard`：本来就有 `hasPc`；
  PC 缺失时 `label` 改为 **`本地估算`**、去掉星级（星级是 PC 分位的表达），
  评级配色改为中性灰，不再套用 PC 的档位配色
- 顺带修了同一文件里 `_buildPicksSummary` 用字面量 `1.5/1.0/0.5` 的问题，
  改用 `AppTheme.scoreS/A/B` 常量（此前改阈值漏改这处会让统计行与卡片等级对不上）

**验证**：`dart analyze lib/` → **0 error / 0 warning**（195 条均为既有 info 级 lint）；
`dart format` 对 `home_page.dart` 干净、`stock_detail_page.dart` 只剩两处**与本次无关**
的旧漂移。**但没有运行 App 看实际渲染**——本机 `flutter --version` 会尝试联网取 tags
而失败，没有可用设备/模拟器。

### ⏳ 未做：②合并口径、③regimeWeights

两项都比原选项复杂，需要先补前提：

- **②（合并整体用 PC）**：手机端 `analysis.signals` 是 `List<Signal>`（带
  `bullish/bearish` 类型），而 PC 的 `PcRatingResult.signals` 是 `List<String>`，
  且 **PC 的评级导出里根本没有 `signals` 字段**——照原方案切过去会把信号清空。
  需要先：PC 导出补 `signals`，手机端再把字符串按关键词映射回 `Signal` 对象
- **③（删 regimeWeights）**：需要手机端新解析 `strategy_bias`（`main.py:216` 已上传，
  但手机端全库没有任何地方读它），还要补类别名映射
  （PC 的 `trend` / `momentum` / `reversal` / `fundamental` ↔ 手机端的 趋势 / 动量 / 反转）

### 仍遗留

- `_buildDecisionCard` 里**建仓/关注/观望**的分支仍在用 PC 阈值判本地分——
  这次只改了标签与配色，那一层没动（已在代码里标注）
- 见第 6 条：`macdRsiEnabled` 默认值与 PC 相反

