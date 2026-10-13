"""诊断：评级总分与置信度是两把尺子，排序几乎不重叠。

背景：
  - 评级总分（rating total_score）是**截面 Z-score 合成**（rating/engine.py:568
    先 winsorize + Z-score 再加权），阈值 1.5/1.0/0.5，量纲不是 0~1，可正可负；
  - 置信度（signal confidence）是 6 个维度**绝对加权**的 0~1 分，
    阈值 0.75/0.60/0.45/0.25（reporter.py:131 / cloud.py:193）。

`_run_rating` 按 total_score 取前 15 名推送（main.py:699），
但 reporter.py:154 对每只股票同时打印评级和 `[Lv.{置信度等级}]`。
如果两个排序不重叠，用户就会看到「推荐」的股票被系统自己标成 C/D。

本脚本量化：
  1. 按评级 Top15 与按置信度 Top15 的重叠度
  2. 评级 Top15 的置信度构成（哪个维度是 0）
  3. 置信度 ≥0.75（S 级）的那批股票，在评级里排第几

只读：不联网、不写库、不推送。
用法: python run_rating_vs_confidence.py [--date 20261013] [--limit N]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

THRESHOLDS = [("S", 0.75), ("A", 0.60), ("B", 0.45), ("C", 0.25)]

STRATEGY_KEY_MAP = {
    "maVolume": "MaVolumeStrategy",
    "turtleTrade": "TurtleTradeStrategy",
    "meanReversion": "MeanReversionStrategy",
    "volumePrice": "VolumePriceStrategy",
    "atrChannel": "AtrChannelStrategy",
    "macdRsi": "MacdRsiStrategy",
    "maCross": "MaCrossStrategy",
}

# 生产排序键（rating/engine.py:912）：先按评级类别，再按总分降序。
# 不能只按 total_score 排——趋势不过的股票评级被压到 watch，但总分可能很高，
# 只按总分排会把这些股票排到 strong_buy/buy 前面，复现不出生产的 Top15。
RATING_ORDER = {
    "strong_buy": 0,
    "buy": 1,
    "watch": 2,
    "attention": 3,
    "avoid": 4,
}


def production_sort_key(rec: dict) -> tuple:
    return (
        RATING_ORDER.get(rec.get("rating"), 5),
        -(float(rec.get("total_score") or 0.0)),
    )


def level_of(conf: float) -> str:
    for name, thr in THRESHOLDS:
        if conf >= thr - 1e-9:
            return name
    return "D"


def build_strategy_map(records: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for rec in records:
        triggered = [
            STRATEGY_KEY_MAP.get(k, k)
            for k, v in (rec.get("strategy_scores") or {}).items()
            if v > 0
        ]
        out[rec["code"]] = triggered or ["RatingEngine"]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="评级总分 vs 置信度 口径诊断")
    parser.add_argument("--date", help="rating_result 日期，如 20261013；默认取最新")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 只，0 表示全部")
    args = parser.parse_args()

    files = sorted(glob.glob(os.path.join("output", "rating_result_*.json")))
    rating_path = (
        os.path.join("output", f"rating_result_{args.date}.json")
        if args.date
        else (files[-1] if files else "")
    )
    if not rating_path or not os.path.exists(rating_path):
        sys.exit(f"找不到评级文件: {rating_path}")

    with open(rating_path, encoding="utf-8") as fh:
        records = json.load(fh)
    if args.limit:
        records = records[: args.limit]

    from sequoia_x.utils.name_resolver import get_name_resolver

    resolver = get_name_resolver()
    resolver._names = {r["code"]: r.get("name") or r["code"] for r in records}
    resolver._loaded = True

    from sequoia_x.core.config import get_settings
    from sequoia_x.data.engine import DataEngine
    from sequoia_x.regime import RegimeDetector
    from sequoia_x.signal import SignalGenerator

    engine = DataEngine(get_settings())
    regime = RegimeDetector(engine).detect("000905")

    # 复现生产排序：records 先按 (评级类别, 总分降序) 排，main.py:699 取前 15
    by_rating = sorted(records, key=production_sort_key)
    rating_rank = {r["code"]: i + 1 for i, r in enumerate(by_rating)}
    rating_info = {r["code"]: r for r in records}

    print(f"评级文件: {rating_path}")
    print(f"候选股票: {len(records)} 只 | 市场状态: {regime.label}")
    print("生成信号中（只读本地库）...")

    gen = SignalGenerator(engine, total_capital=1_000_000.0)
    signals = gen.batch_generate(
        symbols=[r["code"] for r in records],
        strategy_map=build_strategy_map(records),
        market_regime=regime.label,
    )
    if not signals:
        sys.exit("没有生成任何信号")

    by_conf = sorted(signals, key=lambda s: float(s.confidence), reverse=True)

    print()
    print("=" * 78)
    print("【一】生产排序 Top15（评级类别 → 总分降序）—— _run_rating 实际推送的那批")
    print(
        f"{'代码':<8}{'评级':>10}{'置信度':>8}{'等级':>5}{'总分':>8}{'策略数':>7}"
        f"  置信度构成(只有非零项)"
    )
    for rec in by_rating[:15]:
        sig = next((s for s in signals if s.symbol == rec["code"]), None)
        detail = ""
        if sig is not None:
            items = sorted(
                (k, v) for k, v in (sig.score_details or {}).items() if abs(v) > 1e-9
            )
            detail = " ".join(f"{k}={v:.2f}" for k, v in items)
        conf = float(sig.confidence) if sig is not None else float("nan")
        n_strat = len(sig.strategies) if sig is not None else 0
        print(
            f"{rec['code']:<8}{str(rec.get('rating', '')):>10}{conf:>8.4f}{level_of(conf):>5}"
            f"{float(rec.get('total_score') or 0):>8.2f}{n_strat:>7}  {detail}"
        )

    print()
    print("【二】按置信度 Top15 —— 置信度体系认为最好的那批")
    print(f"{'代码':<8}{'置信度':>8}{'等级':>5}{'总分':>8}{'评级排名':>9}  触发的策略")
    for sig in by_conf[:15]:
        rec = rating_info.get(sig.symbol, {})
        print(
            f"{sig.symbol:<8}{float(sig.confidence):>8.4f}{level_of(float(sig.confidence)):>5}"
            f"{float(rec.get('total_score') or 0):>8.2f}{rating_rank.get(sig.symbol, 0):>9}"
            f"  {','.join(sig.strategies)}"
        )

    top_rating = {r["code"] for r in by_rating[:15]}
    top_conf = {s.symbol for s in by_conf[:15]}
    print()
    print("【三】两个 Top15 的重叠")
    print(f"  交集: {len(top_rating & top_conf)} 只 {sorted(top_rating & top_conf)}")

    print()
    print("【四】置信度 ≥0.75（S 级）的那批，在评级里排第几")
    s_sigs = [s for s in by_conf if float(s.confidence) >= 0.75 - 1e-9]
    print(f"  共 {len(s_sigs)} 只")
    if s_sigs:
        ranks = sorted(rating_rank.get(s.symbol, len(records)) for s in s_sigs)
        print(f"  评级排名: 最好 {ranks[0]} | 中位 {ranks[len(ranks) // 2]} | 最差 {ranks[-1]}")
        in_top = sum(1 for r in ranks if r <= 15)
        print(f"  其中进入评级 Top15 的: {in_top} 只")
        print(f"  进入评级 Top100 的: {sum(1 for r in ranks if r <= 100)} 只")
        print("  明细（前 10 只）:")
        for sig in s_sigs[:10]:
            rec = rating_info.get(sig.symbol, {})
            print(
                f"    {sig.symbol} 置信度{float(sig.confidence):.4f}({level_of(float(sig.confidence))}) "
                f"总分{float(rec.get('total_score') or 0):.2f} 评级排名{rating_rank.get(sig.symbol, 0)}"
            )

    print()
    print("【五】全市场置信度等级 × 评级分布")
    from collections import Counter

    lv_count = Counter(level_of(float(s.confidence)) for s in signals)
    print("  置信度等级:", " ".join(f"{k}={lv_count.get(k, 0)}" for k, _ in THRESHOLDS)
          + f" D={lv_count.get('D', 0)}")
    for lv, _ in THRESHOLDS:
        group = [
            rating_rank.get(s.symbol, len(records))
            for s in signals
            if level_of(float(s.confidence)) == lv
        ]
        if group:
            group.sort()
            print(
                f"    {lv} 级 {len(group):>4} 只 | 评级排名 最好{group[0]:>5} "
                f"中位{group[len(group) // 2]:>5} 最差{group[-1]:>5}"
            )
    print()
    print("【六】总分并列 / 高分被软过滤挡住的情况")
    score_count: Counter = Counter(
        round(float(r.get("total_score") or 0.0), 1) for r in records
    )
    for s, c in sorted(score_count.items(), reverse=True)[:10]:
        print(f"    总分 {s:>6.1f}: {c:>5} 只")
    if len(by_rating) >= 15:
        cut_score = round(float(by_rating[14].get("total_score") or 0.0), 1)
        tie = score_count.get(cut_score, 0)
        print(
            f"  → 生产第 15 名的总分 {cut_score:.1f}，全市场同分 {tie} 只；"
            f"取 15 只后其余 {max(tie - 15, 0)} 只被丢弃（按输入顺序截断，本质任意）"
        )
    rating_hist: Counter = Counter(str(r.get("rating", "")) for r in records)
    print("  → 评级分布: " + " ".join(f"{k}={v}" for k, v in rating_hist.most_common()))
    high = [r for r in records if float(r.get("total_score") or 0.0) >= 1.5]
    print(
        f"  → 总分 ≥1.5（按阈值本该是 strong_buy）的 {len(high)} 只中，"
        f"实际评为 strong_buy 的只有 {rating_hist.get('strong_buy', 0)} 只"
        f" —— 差额即被趋势软过滤压到 watch 的高分股"
    )

    print()
    print("【七】新旧推送名单对比（模拟 main.py 的新选法，不改生产）")
    conf_of = {s.symbol: float(s.confidence) for s in signals}
    old_list = [r["code"] for r in by_rating[:15]]
    if len(by_rating) >= 15:
        cut = production_sort_key(by_rating[14])
        pool = [r for r in by_rating if production_sort_key(r) <= cut][:200]
    else:
        pool = list(by_rating)
    new_list = [
        r["code"]
        for r in sorted(
            pool, key=lambda r: (production_sort_key(r), -conf_of.get(r["code"], 0.0))
        )
    ][:15]
    print(f"  候选池（含并列同分）: {len(pool)} 只")
    print(f"  旧名单（all_results[:15]）: {' '.join(old_list)}")
    print(f"  新名单（+置信度二级键）  : {' '.join(new_list)}")
    kept = [c for c in new_list if c in old_list]
    print(f"  新名单中保留自旧名单: {len(kept)} 只；新进 {15 - len(kept)} 只")
    changed = [c for c in new_list if c not in old_list]
    if changed:
        print("  新进的股票（置信度 / 总分 / 评级排名）:")
        for c in changed:
            rec = rating_info.get(c, {})
            print(
                f"    {c} 置信度{conf_of.get(c, float('nan')):.4f}"
                f"({level_of(conf_of.get(c, 0.0))}) 总分{float(rec.get('total_score') or 0):.2f}"
                f" 评级排名{rating_rank.get(c, 0)}"
            )

    print("=" * 78)
    print("说明：评级总分是截面 Z-score 尺度（阈值 1.5/1.0/0.5），置信度是绝对 0~1 尺度")
    print("      （阈值 0.75/0.60/0.45/0.25）。两者排序不一致本身不是 bug。")


if __name__ == "__main__":
    main()
