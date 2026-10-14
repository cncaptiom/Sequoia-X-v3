"""验证评级总分为何 87% 为负（只读 rating_result_*.json，不连库、不联网）。

假设：`_cross_sectional_standardize`（rating/engine.py:568）对每个策略做截面 Z-score，
而策略信号是**稀疏**的——多数股票不触发，原始分为 0。若 0 分占多数，截面均值会被
少数高分拉高，于是 **Z(0) < 0**：「没触发」被算成「负分」，叠加后 total_score 变负 → avoid。

本脚本用导出文件里已有的 `per_strategy_scores`（= 标准化后的 Z 分）和 `raw_strategy_scores`
来验证或推翻这个假设，并给出 total_score 的实际分布。

用法: python run_rating_scale_check.py [--date 20261013] [--top 15]
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys


def _total(r: dict) -> float:
    """读取 total_score。

    注意**不能**写成 `float(r.get("total_score") or 0.0)`：
    -0.0 在 Python 里是 falsy，`-0.0 or 0.0` 会得到 +0.0，
    从而把"微弱负分"误判成"恰好为零"。评级引擎里这部分股票正是靠
    `score >= 0` 为假被判成 avoid 的。
    """
    v = r.get("total_score")
    return 0.0 if v is None else float(v)


def main() -> None:
    parser = argparse.ArgumentParser(description="评级评分尺度检查")
    parser.add_argument("--date", help="rating_result 日期，如 20261013；默认取最新")
    parser.add_argument("--top", type=int, default=15, help="展示 total_score 最高的前 N 只")
    args = parser.parse_args()

    files = sorted(glob.glob(os.path.join("output", "rating_result_*.json")))
    path = (
        os.path.join("output", f"rating_result_{args.date}.json")
        if args.date
        else (files[-1] if files else "")
    )
    if not path or not os.path.exists(path):
        sys.exit(f"找不到评级文件: {path}")

    with open(path, encoding="utf-8") as fh:
        recs = json.load(fh)
    n = len(recs)
    print(f"评级文件: {path}")
    print(f"股票数: {n}")

    # ── 1. total_score 分布 ──
    totals = sorted(_total(r) for r in recs)
    neg = sum(1 for t in totals if t < 0)
    zero = sum(1 for t in totals if t == 0)
    print()
    print("【一】total_score 分布")
    print(f"  <0: {neg} ({neg / n:.1%}) | ==0: {zero} ({zero / n:.1%}) | >0: {n - neg - zero} ({(n - neg - zero) / n:.1%})")
    for q, name in [(0.10, "P10"), (0.25, "P25"), (0.50, "中位"), (0.75, "P75"), (0.90, "P90"), (0.99, "P99")]:
        print(f"  {name}: {totals[min(int(q * n), n - 1)]:.2f}")
    print(f"  max: {totals[-1]:.2f} | min: {totals[0]:.2f}")

    # ── 1b. rating × total_score 交叉表（核对评级是否真由 total_score 决定） ──
    from collections import Counter

    print()
    print("【一b】rating × total_score 交叉表")
    print("  说明：_calculate_rating 里 avoid 只在 score < 0 时返回（两个分支都是），")
    print("        若 avoid 出现在 score >= 0 的档位，说明定级用的不是 total_score。")
    buckets = [
        ("<0 (≤-0.1)", lambda t: t < 0),
        ("-0.0", lambda t: t == 0 and math.copysign(1, t) < 0),
        ("+0.0", lambda t: t == 0 and math.copysign(1, t) > 0),
        ("(0,0.5)", lambda t: 0 < t < 0.5),
        ("[0.5,1)", lambda t: 0.5 <= t < 1.0),
        ("[1,1.5)", lambda t: 1.0 <= t < 1.5),
        (">=1.5", lambda t: t >= 1.5),
    ]
    ratings = sorted({str(r.get("rating")) for r in recs})
    print(f"  {'total_score':<12}" + "".join(f"{x:>12}" for x in ratings) + f"{'合计':>10}")
    for name, pred in buckets:
        row = Counter()
        for r in recs:
            if pred(_total(r)):
                row[str(r.get("rating"))] += 1
        total = sum(row.values())
        print(f"  {name:<12}" + "".join(f"{row.get(x, 0):>12}" for x in ratings) + f"{total:>10}")

    # 检查 -0.0：round(-0.03, 1) == -0.0，json 会写成 -0.0，而 -0.0 < 0 为 False
    neg_zero = [r for r in recs if repr(_total(r)) == "-0.0"]
    print(f"  total_score 为 -0.0 的股票: {len(neg_zero)} 只")
    tiny_neg = [r for r in recs if -0.05 < _total(r) < 0]
    print(f"  total_score 落在 (-0.05, 0) 的股票: {len(tiny_neg)} 只")

    # 空 strategy_scores / category_scores：若这些股票保住默认值 Rating.avoid
    # （engine.py:293），则 avoid 里有很大一部分根本不是"评分算出来的"
    empty_strat = [r for r in recs if not (r.get("strategy_scores") or {})]
    empty_cat = [r for r in recs if not (r.get("category_scores") or {})]
    print(f"  strategy_scores 为空的: {len(empty_strat)} 只 (这些的 rating 分布: "
          + ", ".join(f"{k}={v}" for k, v in Counter(str(r.get('rating')) for r in empty_strat).most_common())
          + ")")
    print(f"  category_scores 为空的: {len(empty_cat)} 只 (这些的 rating 分布: "
          + ", ".join(f"{k}={v}" for k, v in Counter(str(r.get('rating')) for r in empty_cat).most_common())
          + ")")

    # ── 2. 每个策略的标准化分（Z）分布：检验「未触发是否拿到负分」 ──
    keys: set[str] = set()
    for r in recs:
        keys |= set((r.get("per_strategy_scores") or {}).keys())
    print()
    print("【二】per_strategy_scores（标准化 Z 分）符号分布")
    print(f"  {'策略':<16}{'负':>8}{'零':>8}{'正':>8}   负占比")
    for k in sorted(keys):
        vals = [r["per_strategy_scores"][k] for r in recs
                if k in (r.get("per_strategy_scores") or {})]
        if not vals:
            continue
        v_neg = sum(1 for v in vals if v < 0)
        v_zero = sum(1 for v in vals if v == 0)
        v_pos = len(vals) - v_neg - v_zero
        print(
            f"  {k:<16}{v_neg:>8}{v_zero:>8}{v_pos:>8}   {v_neg / len(vals):.1%}"
            f"  (n={len(vals)})"
        )

    # ── 3. 原始分是否为稀疏零（假设的直接检验） ──
    raw_keys: set[str] = set()
    for r in recs:
        raw_keys |= set((r.get("raw_strategy_scores") or {}).keys())
    if raw_keys:
        print()
        print("【三】raw_strategy_scores（正交化前原始分）零值占比")
        for k in sorted(raw_keys):
            vals = [r["raw_strategy_scores"][k] for r in recs
                    if k in (r.get("raw_strategy_scores") or {})]
            if not vals:
                continue
            z = sum(1 for v in vals if v == 0)
            print(f"  {k:<16} 零值 {z}/{len(vals)} ({z / len(vals):.1%})")
    else:
        print()
        print("【三】导出文件里没有 raw_strategy_scores，无法直接检验稀疏性")

    # ── 4. 类别分与总分的关系 ──
    print()
    print("【四】category_scores 分布（各类别平均）")
    cat_keys: set[str] = set()
    for r in recs:
        cat_keys |= set((r.get("category_scores") or {}).keys())
    for k in sorted(cat_keys):
        vals = sorted(
            float(r["category_scores"][k]) for r in recs
            if k in (r.get("category_scores") or {})
        )
        if not vals:
            continue
        m = len(vals)
        n_neg = sum(1 for v in vals if v < 0)
        print(
            f"  {k:<14} n={m:<5} 负 {n_neg / m:.1%} | 中位 {vals[m // 2]:+.2f} | "
            f"P90 {vals[int(0.9 * m)]:+.2f} | max {vals[-1]:+.2f}"
        )

    # ── 5. 总分最高的那批，看看是哪一类在贡献 ──
    print()
    print(f"【五】total_score 最高的前 {args.top} 只")
    for r in sorted(recs, key=_total, reverse=True)[: args.top]:
        cats = r.get("category_scores") or {}
        cat_txt = " ".join(f"{k}={v:+.1f}" for k, v in sorted(cats.items()))
        print(
            f"  {r['code']} 评级={str(r.get('rating')):<10} 总分={_total(r):+.2f}"
            f" | {cat_txt}"
        )

    print()
    print("判读：若【二】中多数策略的负占比接近『1 - 触发率』，即未触发的股票普遍拿到负 Z 分，")
    print("      则假设成立——『没触发信号』被当成『负分』，而不是中性的 0。")


if __name__ == "__main__":
    main()
