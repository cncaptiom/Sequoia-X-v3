"""对比 v3.2 → v3.3 置信度评分差异（只读：不写库、不推送、不联网）。

背景：v3.3 删除了「资金流向」伪信号维度（原名「北向资金增持」，满分 0.05），
并把「多策略共振」满分由 0.25 提到 0.30，满分仍为 1.00。

本脚本用**当前代码**跑一遍信号生成，拿到真实的 `confidence` 和 `score_details`，
再按旧公式反推旧置信度，统计 S/A/B/C/D 等级有多少只股票会变动。

反推依据：唯一被改动的两项是
    「多策略共振」0.25 → 0.30（因此旧值比新值少 0.05，仅当 ≥3 策略时）
    「资金流向」f（被删除，旧值比新值多 f，f ∈ {0, 0.03, 0.05}）
其余维度、风险控制、市场状态修正、周线过滤都不受本次改动影响，
且市场状态修正只依赖 regime 字符串、周线分支只依赖 df，都与分数无关。
唯一的非线性是收尾的 min(score, 1.0) 与 max(score*0.6, 0.05)，
脚本会先反解出封顶前的分数再重新收尾，因此除封顶/触底边界外是精确值。

用法:
    python run_confidence_diff.py                  # 用最近的 rating_result_*.json
    python run_confidence_diff.py --date 20261013  # 指定日期
    python run_confidence_diff.py --limit 800      # 只跑前 800 只，快速抽样
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

THRESHOLDS = [("S", 0.75), ("A", 0.60), ("B", 0.45), ("C", 0.25)]
LEVEL_RANK = {"S": 4, "A": 3, "B": 2, "C": 1, "D": 0}  # 不能用字符串比较：ASCII 里 "S" > "D"
FLOW_SCENARIOS = [0.0, 0.03, 0.05]

STRATEGY_KEY_MAP = {
    "maVolume": "MaVolumeStrategy",
    "turtleTrade": "TurtleTradeStrategy",
    "meanReversion": "MeanReversionStrategy",
    "volumePrice": "VolumePriceStrategy",
    "atrChannel": "AtrChannelStrategy",
    "macdRsi": "MacdRsiStrategy",
    "maCross": "MaCrossStrategy",
}

MULTI_RESONANCE_NEW = 0.30
MULTI_RESONANCE_OLD = 0.25
CROSSOVER_LOSS = MULTI_RESONANCE_OLD - MULTI_RESONANCE_NEW  # -0.05

# 生产排序键（rating/engine.py:912）：先按评级类别，再按总分降序。
# 不能只按 total_score 排——趋势不过的股票评级被压到 watch，但总分可能很高，
# 只按总分排会漏掉生产实际推送的那批。
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
    # 容差：周线不过阈时 conf = pre*0.6，反解再乘回会有浮点残差，
    # 例如 0.75*0.6 得到 0.4499999999999999，不加容差会被误判到下一级
    for name, thr in THRESHOLDS:
        if conf >= thr - 1e-9:
            return name
    return "D"


def latest_rating_file() -> str:
    files = sorted(glob.glob(os.path.join("output", "rating_result_*.json")))
    if not files:
        sys.exit("找不到 output/rating_result_*.json，请先跑一次 python main.py --rating")
    return files[-1]


def pre_cap_score(conf: float, weekly: float) -> tuple[float | None, str]:
    """从最终置信度反解封顶前的分数。

    Returns:
        (score, kind)，kind ∈ {"exact", "capped", "floored"}；
        capped/floored 表示分数落在 min()/max() 的边界上，无法反解。
    """
    if weekly > 0:  # 周线通过：conf = min(score + 0.10, 1.0)
        if conf >= 1.0:
            return None, "capped"
        return conf - 0.10, "exact"

    if weekly < 0:  # 周线不通过：conf = min(max(score * 0.6, 0.05), 1.0)
        if conf <= 0.05 + 1e-9:
            return None, "floored"
        return conf / 0.6, "exact"

    # 周线未参与：conf = min(score, 1.0)
    if conf >= 1.0:
        return None, "capped"
    return conf, "exact"


def final_score(pre: float, weekly: float) -> float:
    if weekly > 0:
        return min(pre + 0.10, 1.0)
    if weekly < 0:
        return min(max(pre * 0.6, 0.05), 1.0)
    return min(pre, 1.0)


def quant_fallback_bonus(df) -> float:
    """复刻旧 _calc_flow_bonus 的量价回退分支（akshare 走不通时才会用到）。

    只有 0 和 0.05 两种取值，不存在 0.03 —— 0.03 是 akshare 分支专属。
    """
    if df is None or len(df) < 5:
        return 0.0
    recent = df.tail(5)
    rising = bool(recent["close"].iloc[-1] > recent["close"].iloc[0])
    vol_ok = bool(recent["volume"].mean() > df["volume"].tail(20).mean())
    return 0.05 if (rising and vol_ok) else 0.0


def build_strategy_map(records: list[dict]) -> dict[str, list[str]]:
    strategy_map: dict[str, list[str]] = {}
    for rec in records:
        triggered = [
            STRATEGY_KEY_MAP.get(k, k)
            for k, v in (rec.get("strategy_scores") or {}).items()
            if v > 0
        ]
        if not triggered:
            triggered = ["RatingEngine"]
        strategy_map[rec["code"]] = triggered
    return strategy_map


def main() -> None:
    parser = argparse.ArgumentParser(description="对比 v3.2/v3.3 置信度评分差异")
    parser.add_argument("--date", help="rating_result 日期，如 20261013；默认取最新")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 只，0 表示全部")
    args = parser.parse_args()

    rating_path = (
        os.path.join("output", f"rating_result_{args.date}.json")
        if args.date
        else latest_rating_file()
    )
    if not os.path.exists(rating_path):
        sys.exit(f"找不到评级文件: {rating_path}")

    with open(rating_path, encoding="utf-8") as fh:
        records = json.load(fh)
    if args.limit:
        records = records[: args.limit]

    # 用评级文件里的名称预热缓存，避免 generate() 里的 NameResolver 走 mootdx/baostock 联网
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
    strategy_map = build_strategy_map(records)

    print(f"评级文件: {rating_path}")
    print(f"候选股票: {len(strategy_map)} 只 | 市场状态: {regime.label}")
    print("生成信号中（只读本地库，不联网、不写库）...")

    gen = SignalGenerator(engine, total_capital=1_000_000.0)
    signals = gen.batch_generate(
        symbols=[r["code"] for r in records],
        strategy_map=strategy_map,
        market_regime=regime.label,
    )

    if not signals:
        sys.exit("没有生成任何信号，无法对比（检查库内数据是否过期）")

    kinds = {"exact": 0, "capped": 0, "floored": 0}
    new_levels: dict[str, int] = {}
    multi_dist: dict[float, int] = {}
    shifts: dict[float, dict[str, int]] = {
        f: {"better": 0, "worse": 0, "same": 0} for f in FLOW_SCENARIOS
    }
    examples: dict[float, list[str]] = {f: [] for f in FLOW_SCENARIOS}
    at_risk = {name: {"below": 0, "above": 0} for name, _ in THRESHOLDS}
    # 逐股实际 f（按量价回退分支复刻）——只在 akshare 分支失效时成立
    real = {"better": 0, "worse": 0, "same": 0}
    real_f_dist = {0.0: 0, 0.05: 0}

    for sig in signals:
        details = sig.score_details or {}
        conf = float(sig.confidence)
        weekly = float(details.get("周线过滤", 0.0))
        multi = float(details.get("多策略共振", 0.0))
        is_multi = abs(multi - MULTI_RESONANCE_NEW) < 1e-9

        new_lv = level_of(conf)
        new_levels[new_lv] = new_levels.get(new_lv, 0) + 1
        multi_dist[multi] = multi_dist.get(multi, 0) + 1

        for name, thr in THRESHOLDS:
            if 0.0 <= thr - conf <= 0.05:
                at_risk[name]["below"] += 1
            elif 0.0 <= conf - thr <= 0.05:
                at_risk[name]["above"] += 1

        pre, kind = pre_cap_score(conf, weekly)
        kinds[kind] += 1

        # 逐股实际 f：复刻旧代码在 akshare 分支失效时的量价回退
        try:
            f_real = quant_fallback_bonus(engine.get_ohlcv(sig.symbol))
        except Exception:
            f_real = 0.0
        real_f_dist[f_real] = real_f_dist.get(f_real, 0) + 1

        if kind != "exact":
            for f in FLOW_SCENARIOS:
                shifts[f]["same"] += 1
            real["same"] += 1
            continue

        base_delta = CROSSOVER_LOSS if is_multi else 0.0
        for f in FLOW_SCENARIOS:
            # 旧分 = 新分 + (旧多策略共振 - 新) + 旧资金流向
            old_conf = final_score(pre + base_delta + f, weekly)
            old_lv = level_of(old_conf)
            if old_lv == new_lv:
                shifts[f]["same"] += 1
                continue
            # 站在生产视角：新等级比旧等级差 = 这次改动把该股降了级
            key = "worse" if LEVEL_RANK[new_lv] < LEVEL_RANK[old_lv] else "better"
            shifts[f][key] += 1
            if len(examples[f]) < 10:
                examples[f].append(
                    f"{sig.symbol} 旧{old_lv}→新{new_lv} "
                    f"(旧{old_conf:.4f} → 新{conf:.4f}, 多策略={multi})"
                )

        real_conf = final_score(pre + base_delta + f_real, weekly)
        real_lv = level_of(real_conf)
        if real_lv == new_lv:
            real["same"] += 1
        elif LEVEL_RANK[new_lv] < LEVEL_RANK[real_lv]:
            real["worse"] += 1
        else:
            real["better"] += 1

    confs = sorted(float(s.confidence) for s in signals)
    n = len(confs)
    print()
    print("=" * 66)
    print(f"信号数: {n}")
    print(f"置信度: min {confs[0]:.4f} | 中位 {confs[n // 2]:.4f} | max {confs[-1]:.4f}")
    dist = " ".join(f"{lv}={new_levels.get(lv, 0)}" for lv, _ in THRESHOLDS)
    print(f"新等级分布: {dist} D={new_levels.get('D', 0)}")
    print(f"反解情况: 精确 {kinds['exact']} | 封顶 {kinds['capped']} | 触底 {kinds['floored']}")
    multi_line = " ".join(
        f"{k:.2f}={v}" for k, v in sorted(multi_dist.items(), reverse=True)
    )
    n_multi = sum(v for k, v in multi_dist.items() if abs(k - MULTI_RESONANCE_NEW) < 1e-9)
    print(f"多策略共振分布: {multi_line}")
    print(f"  → 触发 ≥3 策略（0.30 档，即本次加分的那档）的股票: {n_multi} 只")
    print()
    print("阈值附近（±0.05 内）的股票数——这些才可能升降级:")
    for name, thr in THRESHOLDS:
        print(
            f"  {name} (≥{thr:.2f}): 下方 {at_risk[name]['below']} 只 | "
            f"上方 {at_risk[name]['above']} 只"
        )
    print()
    for f in FLOW_SCENARIOS:
        s = shifts[f]
        print(
            f"【若旧资金流向 f={f:.2f}】降级 {s['worse']} | 升级 {s['better']} "
            f"| 不变 {s['same']}"
        )
        for line in examples[f]:
            print(f"    {line}")

    print()
    print(
        f"【逐股实际 f（量价回退口径）】降级 {real['worse']} | "
        f"升级 {real['better']} | 不变 {real['same']}"
    )
    print(
        f"    其中量价条件成立(f=0.05) {real_f_dist.get(0.05, 0)} 只 | "
        f"不成立(f=0) {real_f_dist.get(0.0, 0)} 只"
    )

    # ── 生产口径：_run_rating 只对 (评级类别, 总分) 前 15 名生成信号并推送（main.py:699） ──
    top = sorted(records, key=production_sort_key)[:15]
    top_sigs = gen.batch_generate(
        [r["code"] for r in top], build_strategy_map(top), regime.label
    )
    print()
    print("=" * 66)
    print(f"【生产口径】总分 Top{len(top)} —— 这是 _run_rating 实际会推送的那批")
    for sig in top_sigs:
        d = sig.score_details or {}
        conf = float(sig.confidence)
        weekly = float(d.get("周线过滤", 0.0))
        multi = float(d.get("多策略共振", 0.0))
        pre, kind = pre_cap_score(conf, weekly)
        base_delta = CROSSOVER_LOSS if abs(multi - MULTI_RESONANCE_NEW) < 1e-9 else 0.0
        try:
            fr = quant_fallback_bonus(engine.get_ohlcv(sig.symbol))
        except Exception:
            fr = 0.0
        if kind == "exact":
            old_txt = "  ".join(
                f"f={f:.2f}:{level_of(final_score(pre + base_delta + f, weekly))}"
                for f in FLOW_SCENARIOS
            )
            real_txt = (
                f"实际f={fr:.2f}→旧"
                f"{level_of(final_score(pre + base_delta + fr, weekly))}"
            )
        else:
            old_txt = f"({kind}，无法反解)"
            real_txt = old_txt
        print(
            f"  {sig.symbol} 策略数={len(sig.strategies)} 多策略={multi:.2f} "
            f"| 新 {level_of(conf)}({conf:.4f}) | 旧 {old_txt} | {real_txt}"
        )
    print("=" * 66)
    print("注 1：降级/升级均相对旧版而言，且最多只差一级（|Δ| ≤ 0.05）。")
    print("注 2：封顶/触底的股票无法精确反解，一律计为「不变」，")
    print("      但这些情形下等级必然同为 S 或同为 D，不影响结论。")


if __name__ == "__main__":
    main()
