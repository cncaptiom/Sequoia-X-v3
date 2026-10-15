"""对照实验：把「未触发策略」从截面标准化样本中剔除，评级分布会怎样变。

背景（详见 docs/code-review-2026-10-13.md 的 D3）：
  `_cross_sectional_standardize`（rating/engine.py:568）对每个策略做 winsorize + Z-score，
  样本包含**所有启用该策略的股票**——包括原始分为 0（没触发）的那批。截面均值被少数
  触发者拉高，于是 Z(0) < 0：「没触发」被编码成「负分」。实测 meanReversion 89.6% 为负、
  全市场 87% 的 total_score 是微弱负值 → 判 avoid（其中 3879 只圆整后还显示成 -0.0）。

  **变体**：构建标准化样本时跳过原始分为 0 的股票。未触发者在 std_scores 里便回落到
  原始值 0（中性），不再贡献负分。

实现保证：两个实验臂**共用同一个 `_standardize_variant`**，只靠 `skip_zeros` 开关区分，
因此差异只可能来自这一处行为。

与生产对齐的做法：
  - **权重**只来自 `apply_regime`（市场状态）——2026-10-14 起生产不再叠加 IR 加权与
    IC 衰减，见 CHANGELOG 第十节。`ic_tracker=None` 传给 `scan_market`，避免写库
  - **股票池**用最近一次 `--rating` 的 `rating_result_*.json` 里的 code。**注意这不是生产的
    输入池**：那是 `scan_market` 过滤后的结果（4993 只），生产实际喂进去的是
    `get_local_symbols()` 去 ST 后的 5039 只。截面标准化的样本不同，所以本脚本的
    **绝对数值不等于生产口径**；好在两臂共用同一个池、只比臂间差异，结论不受影响。

只读：不推送飞书、不传 COS、不写库。但会做两次全市场评级扫描。
用法: python run_rating_neutral_ab.py [--limit N]
校验：基线分档应与 rating_result_*.json 一致（如 avoid=4348）；对不上说明仍未平价。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import types
from collections import Counter

from sequoia_x.rating.engine import AnalysisResult, RatingEngine
from sequoia_x.signal.quality import standardize, winsorize


def _standardize_variant(self, results: list[AnalysisResult], *, skip_zeros: bool) -> None:
    """复刻 RatingEngine._cross_sectional_standardize，多一个 skip_zeros 开关。

    skip_zeros=True 时，原始分为 0（未触发）的股票不进该策略的标准化样本，
    于是下面 `m.get(r.symbol, v)` 对它们回落到 v 本身 = 0（中性）。
    """
    if len(results) < 20:
        return

    raw_by_strat: dict[str, list[tuple[str, float]]] = {}
    for r in results:
        for s, v in r.raw_strategy_scores.items():
            if skip_zeros and v == 0:
                continue
            raw_by_strat.setdefault(s, []).append((r.symbol, v))

    std_by_strat: dict[str, dict[str, float]] = {}
    for s, pairs in raw_by_strat.items():
        if len(pairs) < 10:
            continue
        trimmed = winsorize([v for _, v in pairs], 1.0, 99.0)
        zs = standardize(trimmed)
        std_by_strat[s] = dict(zip([sym for sym, _ in pairs], zs))

    for r in results:
        std_scores: dict[str, float] = {}
        for s, v in r.raw_strategy_scores.items():
            m = std_by_strat.get(s)
            std_scores[s] = m.get(r.symbol, v) if m else v
        total, ortho, cat = self._combine_scores(std_scores)
        r.total_score = round(total, 1)
        r.strategy_scores = {k: round(v, 1) for k, v in ortho.items()}
        r.per_strategy_scores = {k: round(v, 1) for k, v in std_scores.items()}
        r.category_scores = cat
        r.rating = self._calculate_rating(r.trend_passed, r.total_score)


def _run_arm(engine, symbols, regime_label: str, *, skip_zeros: bool):
    from sequoia_x.rating.engine import RatingConfig

    config = RatingConfig()
    config.apply_regime(regime_label)   # 生产唯一的权重来源

    eng = RatingEngine(config=config, data_engine=engine)

    weights = {k: round(float(getattr(config, a)), 4) for k, a in config.WEIGHT_ATTRS.items()}
    print("  生产权重: " + ", ".join(f"{k}={v}" for k, v in sorted(weights.items()) if v > 0))

    eng._cross_sectional_standardize = types.MethodType(
        lambda self, results: _standardize_variant(self, results, skip_zeros=skip_zeros),
        eng,
    )
    return eng.scan_market(
        symbols=symbols,
        data_loader=lambda s: engine.get_ohlcv_rating_safe(s),
        top_n=99999,
        min_bars=60,
        ic_tracker=None,
    )


def _dist(results) -> Counter:
    return Counter(str(r.rating) for r in results)


def main() -> None:
    parser = argparse.ArgumentParser(description="未触发策略中性化的 A/B 对照")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 只，0 表示全部")
    args = parser.parse_args()

    from sequoia_x.core.config import get_settings
    from sequoia_x.data.engine import DataEngine
    from sequoia_x.regime import RegimeDetector
    from sequoia_x.utils.name_resolver import get_name_resolver

    # 股票池用最近一次 --rating 的实际口径（已过滤 ST），保证与生产一致
    files = sorted(glob.glob(os.path.join("output", "rating_result_*.json")))
    if not files:
        sys.exit("找不到 output/rating_result_*.json，无法复刻生产股票池")
    with open(files[-1], encoding="utf-8") as fh:
        recs = json.load(fh)
    symbols = [r["code"] for r in recs]
    if args.limit:
        symbols = symbols[: args.limit]

    # 预热名称缓存（用评级文件里的真名），避免 mootdx/baostock 联网阻塞
    resolver = get_name_resolver()
    resolver._names = {r["code"]: r.get("name") or r["code"] for r in recs}
    resolver._loaded = True

    engine = DataEngine(get_settings())

    regime = RegimeDetector(engine).detect("000905")
    print(f"股票池: {os.path.basename(files[-1])} → {len(symbols)} 只 | 市场状态: {regime.label}")
    print("跑基线（现状：未触发参与标准化）...")
    base = _run_arm(engine, symbols, regime.label, skip_zeros=False)
    print(f"  完成 {len(base)} 只")

    print("跑变体（未触发剔除出标准化样本）...")
    var = _run_arm(engine, symbols, regime.label, skip_zeros=True)
    print(f"  完成 {len(var)} 只")

    # ── 原始分稀疏性：验证「0 = 未触发」这个前提 ──
    print()
    print("【〇】原始分（raw_strategy_scores）稀疏性")
    raw_keys: set[str] = set()
    for r in base:
        raw_keys |= set(r.raw_strategy_scores)
    for k in sorted(raw_keys):
        vals = [r.raw_strategy_scores[k] for r in base if k in r.raw_strategy_scores]
        if not vals:
            continue
        zeros = sum(1 for v in vals if v == 0)
        negs = sum(1 for v in vals if v < 0)
        print(
            f"  {k:<16} n={len(vals):<5} 零 {zeros / len(vals):>6.1%} "
            f"负 {negs:>5} 正 {len(vals) - zeros - negs:>5} "
            f"| min {min(vals):+.2f} max {max(vals):+.2f}"
        )

    # ── 评级分布对比 ──
    ORDER = ["strong_buy", "buy", "watch", "attention", "avoid"]
    db, dv = _dist(base), _dist(var)
    print()
    print("【一】评级分布对比")
    print(f"  {'评级':<14}{'基线':>10}{'变体':>10}{'变化':>10}")
    for k in ORDER:
        print(f"  {k:<14}{db.get(k, 0):>10}{dv.get(k, 0):>10}{dv.get(k, 0) - db.get(k, 0):>+10}")

    # ── 逐股变档 ──
    rank = {k: i for i, k in enumerate(ORDER)}  # 0 最好
    base_map = {r.symbol: r for r in base}
    up = down = same = 0
    examples_up, examples_down = [], []
    for r in var:
        b = base_map.get(r.symbol)
        if b is None:
            continue
        rb, rv = str(b.rating), str(r.rating)
        if rb == rv:
            same += 1
        elif rank[rv] < rank[rb]:
            up += 1
            if len(examples_up) < 10:
                examples_up.append(f"{r.symbol} {rb}→{rv} (总分 {b.total_score:+.2f}→{r.total_score:+.2f})")
        else:
            down += 1
            if len(examples_down) < 10:
                examples_down.append(f"{r.symbol} {rb}→{rv} (总分 {b.total_score:+.2f}→{r.total_score:+.2f})")

    print()
    print("【二】逐股评级变化（变体相对基线）")
    print(f"  升级 {up} | 降级 {down} | 不变 {same}")
    if examples_up:
        print("  升级样例:")
        for line in examples_up:
            print(f"    {line}")
    if examples_down:
        print("  降级样例:")
        for line in examples_down:
            print(f"    {line}")

    # ── 总分分布对比 ──
    print()
    print("【三】total_score 分布对比")
    for name, res in (("基线", base), ("变体", var)):
        ts = sorted(r.total_score for r in res)
        n = len(ts)
        neg = sum(1 for t in ts if t < 0)
        z = sum(1 for t in ts if t == 0)
        print(
            f"  {name}: <0 {neg} ({neg / n:.1%}) | ==0 {z} ({z / n:.1%}) | "
            f"中位 {ts[n // 2]:+.2f} | P90 {ts[int(0.9 * n)]:+.2f} | max {ts[-1]:+.2f}"
        )

    print()
    print("注：变体会同时改变已触发股票的 Z 尺度（样本变成『只有触发者』），")
    print("    因此这不只是『把未触发者从负分改成 0』，是整体重标定。")


if __name__ == "__main__":
    main()
