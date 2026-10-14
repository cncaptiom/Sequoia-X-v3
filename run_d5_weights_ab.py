"""D5 对照：在**同一份冻结快照**内，比较新旧两套权重下的评分。

背景见 docs/code-review-2026-10-13.md 的 D5 与 CHANGELOG 第十节。

为什么不用「重扫一次再比」：唯一能复现生产结果的是「用生产的**输入池**、在当前数据状态下
重扫」（实测 Top15 15/15、评级分布逐档一致，见 `check_pool_parity.py`）。而拿**历史快照**
当基准的跨运行比对一定会把「数据状态变化」混进来——快照里的 code 是 `scan_market`
【过滤后】的结果（本例 4993 只），生产的输入池却是 `get_local_symbols()` 去 ST 后的
**5039** 只，两者差 46 只，截面标准化（winsorize + Z-score）的样本因此不同，所有股票的
Z 分都会平移。**这种比对得出的「权重影响」是假的。**

本脚本改为对同一个 `rating_result_*.json` 精确反算：用 `per_strategy_scores`
（各策略的截面 Z 分，只取决于股票池、与权重无关）复刻 `RatingEngine._combine_scores`，
只换权重、不换数据。

局限：快照没有导出 `trend_passed`，而 `_calculate_rating` 会把趋势不过的股票评级
上限压到 watch，所以**评级标签无法精确重算**。本脚本只比评分与「按总分排序」的名单。

只读。用法:
    python run_d5_weights_ab.py                 # 当日 regime 的新权重 vs 改动前的旧权重
    python run_d5_weights_ab.py --regime 震荡
    python run_d5_weights_ab.py --smoke         # 不联网的快速自检
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 改动前生产的实际每策略权重（2026-10-14 强熊，由当次快照的类别权重反推：
# 快照里只有 reversal / fundamental 两类，reversal 0.80 在 meanReversion 与 kdj 间
# 由当时的 IR 比例决定，这里按等分近似）
DEFAULT_OLD = "mean_reversion=0.40,kdj=0.40,fundamental=0.20"
TOPN = 15


def _smoke() -> None:
    """不联网、不扫全市场的快速自检。"""
    import py_compile

    for f in ("sequoia_x/signal/quality.py", "sequoia_x/rating/engine.py"):
        py_compile.compile(f, doraise=True)
    print("[OK] 两个改动文件编译通过")

    from sequoia_x.rating.engine import RatingConfig
    from sequoia_x.regime import REGIME_BIAS
    from sequoia_x.signal.quality import ICTracker

    print("\n[一] 生产权重 = 纯 apply_regime（不再叠加 IR / IC 衰减）")
    for label in ("强牛", "弱牛", "震荡", "弱熊", "强熊"):
        cfg = RatingConfig()
        cfg.apply_regime(label)
        live = {k: round(float(getattr(cfg, a)), 4)
                for k, a in cfg.WEIGHT_ATTRS.items() if float(getattr(cfg, a)) > 0}
        print(f"  {label:<4} 活跃 {len(live)} 个 | 合计 {sum(live.values()):.4f} | "
              + ", ".join(f"{k}={v}" for k, v in sorted(live.items())))
    print(f"  （REGIME_BIAS 类别权重：{REGIME_BIAS['震荡']}）")

    print("\n[二] get_summary 按前瞻期拆开（不再把 5日/20日 混算）")
    tracker = ICTracker()
    for lf in sorted({int(e.get("lookforward", 0))
                      for entries in tracker.history.values() for e in entries} - {0}):
        s = tracker.get_summary(lf)
        if not s:
            print(f"  前瞻期 {lf}日: 无数据")
            continue
        print(f"  前瞻期 {lf}日: {len(s)} 个策略")
        for k, v in sorted(s.items(), key=lambda kv: -abs(kv[1]["ir_20d"]))[:3]:
            print(f"    {k:<14} n={v['samples']:<4} ir_20d={v['ir_20d']:+.3f} "
                  f"连续负={v['neg_consecutive']:<3} 衰减={v['decayed']}")

    print("\n[三] detect_decay 数的是「日」不是「条目」")
    for s in ("kdj", "meanReversion", "turtleTrade"):
        for lf in (5, 20):
            print(f"    {s:<14} {lf}日 序列 {len(tracker._series(s, lf)):<3} 条（同日已去重），"
                  f"末尾连续负 IC = {tracker.detect_decay(s, lf)[1]} 天")

    print("\n[四] print_summary（main.py 在 --rating 末尾会调用）")
    tracker.print_summary()
    print("  [OK] print_summary 未抛异常")


def _load_snapshot(path: str | None) -> tuple[str, list[dict]]:
    if path is None:
        files = sorted(glob.glob(os.path.join("output", "rating_result_*.json")))
        if not files:
            sys.exit("找不到 output/rating_result_*.json")
        path = files[-1]
    with open(path, encoding="utf-8") as fh:
        return os.path.basename(path), json.load(fh)


def _parse_weights(spec: str) -> dict[str, float]:
    out = {}
    for part in spec.split(","):
        k, _, v = part.partition("=")
        out[k.strip()] = float(v)
    return out


def _combine(per_strategy: dict[str, float], strat_w: dict[str, float],
             groups: dict[str, list[str]], camel_to_snake: dict[str, str]) -> float:
    """复刻 RatingEngine._combine_scores。

    类别权重 = 类内【启用且权重>0】策略的权重之和，归一化后按类别平均分加权。
    strat_w 以 snake_case 为键。
    """
    cat_w: dict[str, float] = {}
    active_members: dict[str, list[str]] = {}
    for cat, members in groups.items():
        live = [m for m in members
                if m in per_strategy and strat_w.get(camel_to_snake.get(m, ""), 0.0) > 0]
        if live:
            cat_w[cat] = sum(strat_w[camel_to_snake[m]] for m in live)
            active_members[cat] = live
    total = sum(cat_w.values())
    if total <= 0:
        return 0.0
    return sum(
        (sum(per_strategy[m] for m in active_members[c]) / len(active_members[c]))
        * (w / total)
        for c, w in cat_w.items()
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="同一快照内新旧权重的评分对照")
    ap.add_argument("--file", default=None, help="评级快照，默认取最新 rating_result_*.json")
    ap.add_argument("--old", default=DEFAULT_OLD,
                    help=f"改动前的每策略权重（snake_case=值，逗号分隔），默认 {DEFAULT_OLD}")
    ap.add_argument("--regime", default=None, help="指定市场状态，默认自动检测")
    ap.add_argument("--smoke", action="store_true", help="只做不联网的快速自检")
    args = ap.parse_args()

    if args.smoke:
        _smoke()
        return

    from sequoia_x.rating.engine import RatingConfig, RatingEngine

    name, recs = _load_snapshot(args.file)
    recs = [r for r in recs if r.get("per_strategy_scores")]
    print("=" * 92)
    print(f"D5 对照（同一快照内反算，不跨运行） │ {name} │ {len(recs)} 只")
    print("=" * 92)

    cfg = RatingConfig()
    camel_to_snake = dict(cfg.IR_STRATEGY_MAP)
    old_w = _parse_weights(args.old)

    if args.regime:
        label = args.regime
    else:
        from sequoia_x.core.config import get_settings
        from sequoia_x.data.engine import DataEngine
        from sequoia_x.regime import RegimeDetector
        label = RegimeDetector(DataEngine(get_settings())).detect("000905").label
    cfg.apply_regime(label)
    new_w = {k: round(float(getattr(cfg, a)), 4) for k, a in cfg.WEIGHT_ATTRS.items()}

    print(f"\n【一】每策略权重（当日市场状态 = {label}）")
    print("  旧: " + ", ".join(f"{k}={v}" for k, v in sorted(old_w.items()) if v > 0))
    print("  新: " + ", ".join(f"{k}={v}" for k, v in sorted(new_w.items()) if v > 0))

    groups = RatingEngine.RATING_GROUPS
    o = lambda r: _combine(r["per_strategy_scores"], old_w, groups, camel_to_snake)
    n = lambda r: _combine(r["per_strategy_scores"], new_w, groups, camel_to_snake)

    err = [abs(r["total_score"] - o(r)) for r in recs]
    print(f"\n【二】口径校验：旧权重重算的总分 vs 快照记录的总分，"
          f"平均绝对误差 {sum(err) / len(err):.3f}（per_strategy_scores 在快照里只有 1 位小数）")

    delta = [n(r) - o(r) for r in recs]
    print(f"\n【三】总分变化（新 − 旧）: 平均 {sum(delta) / len(delta):+.3f} | "
          f"范围 {min(delta):+.2f} ~ {max(delta):+.2f}")
    print(f"  升高 {sum(1 for x in delta if x > 0.05)} | "
          f"降低 {sum(1 for x in delta if x < -0.05)} | "
          f"持平 {sum(1 for x in delta if abs(x) <= 0.05)}")

    print("\n【四】跨越总分阈值（只算分数；是否变成评级档位还受趋势上限约束）")
    for th in (1.0, 0.5, 0.0):
        up = sum(1 for r in recs if o(r) < th <= n(r))
        dn = sum(1 for r in recs if n(r) < th <= o(r))
        print(f"  总分 {th:+.1f}: 升入 {up} | 降出 {dn}")

    top_o = [r["code"] for r in sorted(recs, key=lambda r: -o(r))[:TOPN]]
    top_n = [r["code"] for r in sorted(recs, key=lambda r: -n(r))[:TOPN]]
    print(f"\n【五】Top{TOPN}（按总分排序，未计趋势上限）")
    print(f"  旧: {' '.join(top_o)}")
    print(f"  新: {' '.join(top_n)}")
    print(f"  重叠 {len(set(top_o) & set(top_n))}/{TOPN}")


if __name__ == "__main__":
    main()
