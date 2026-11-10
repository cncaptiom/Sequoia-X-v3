"""trend 类权重校准：历史回放上的单参数扫描。

背景：2026-11-09 修好海龟突破窗口（此前 close >= 含当日 high 的最大值，
数学上几乎不可能成立，生产里 turtleTrade 全市场恒为 0 分）之后，trend 类
第一次真正参与打分，Top15 的历史表现反而变差。说明现有 trend 权重是在
该信号"死掉"时校准的，需要重算。

方法：
  · 复用 run_rec_eval.build_score_matrix —— 评分与生产 strategies 逐点一致，
    且只构建一次；权重扫描只重算截面合成与 Top15，不重复跑评分。
  · 每个 regime 把 trend 权重乘以因子 f，momentum/reversal 按原比例吸收剩余
    预算（非财务类总预算不变，保持与生产表结构一致）。
  · 样本内 2024-07-01~2025-12-31 选参，样本外 2026-01-01~2026-08-21 验证。
  · 指标：Top15 逐日等权前向收益、相对当日全A等权基准的超额。

用法:
    python run_trend_calib.py [--limit N] [--factors 0,0.25,0.5,0.75,1,1.5,2]
"""

from __future__ import annotations

import argparse
import json
import pickle
import time

import numpy as np
import pandas as pd

import run_rec_eval as ev
from sequoia_x.regime import REGIME_BIAS

CATS_NONFUND = ("trend", "momentum", "reversal")
SPLIT_DATE = "2026-01-01"


def _base_weights(label: str) -> dict[str, float]:
    """生产 REGIME_BIAS 去掉 fundamental 后的三类权重（与回放口径一致）。"""
    bias = REGIME_BIAS[label]
    return {c: float(bias[c]) for c in CATS_NONFUND}


def weights_for_factor(label: str, f: float) -> dict[str, float]:
    """把 trend 权重乘以 f，其余两类按原比例吸收，三类总和保持不变。"""
    base = _base_weights(label)
    total = sum(base.values())
    rest = base["momentum"] + base["reversal"]
    trend = base["trend"] * f
    if trend > total or rest <= 0:
        # trend 已吃掉全部预算：三类只剩 trend
        return {"trend": total, "momentum": 0.0, "reversal": 0.0}
    scale = (total - trend) / rest
    return {"trend": trend,
            "momentum": base["momentum"] * scale,
            "reversal": base["reversal"] * scale}


def prepare_blocks(big: pd.DataFrame, min_stocks: int) -> list[dict]:
    """把评分长表切成逐日截面，预先算好 z 分与未来收益，供多组权重复用。"""
    blocks = []
    for date_str, blk in big.groupby("date", sort=False):
        if len(blk) < min_stocks:
            continue
        mat = blk[ev.MEMBER_KEYS].to_numpy(dtype=float)
        z = ev._winsorize_z(mat)
        fwd = {}
        for n in ev.FWD:
            arr = blk[f"fwd{n}"].to_numpy(dtype=float)
            finite = np.isfinite(arr)
            fwd[n] = {"values": arr, "finite": finite,
                      "base": float(np.mean(arr[finite])) if finite.any() else np.nan}
        blocks.append({
            "date": date_str,
            "n": len(blk),
            "z": z,
            "cat_idx": {c: [ev.MEMBER_KEYS.index(m) for m in ev.CATS[c]] for c in ev.CATS},
            "trend": blk["trend"].to_numpy() > 0,
            "fwd": fwd,
        })
    return blocks


def evaluate(blocks: list[dict], regime_of: dict[str, str],
             factor: float, top_n: int = 15, trend_mode: str = "both") -> dict:
    """按某 trend 因子跑一遍 Top15，返回逐日等权收益与超额。

    trend_mode 用来做归因对照：
      both   = 类别分 = mean(z_turtle, z_atr)（修复后的真实口径）
      atr    = 只用 z_atr（等价于"海龟不参与"）
      turtle = 只用 z_turtle
    注意：`atr` 且 f=0.5 时，趋势类的有效贡献与修复前（turtle 恒为 0、
    类别分 = z_atr/2）完全一致。
    """
    trend_cols = {"both": ["turtleTrade", "atrChannel"],
                  "atr": ["atrChannel"],
                  "turtle": ["turtleTrade"]}[trend_mode]
    cat_cache: dict[str, dict[str, float]] = {}
    per_date = []
    for b in blocks:
        label = regime_of.get(b["date"])
        if label is None:
            continue
        if label not in cat_cache:
            cat_cache[label] = weights_for_factor(label, factor)
        w = cat_cache[label]
        total_w = sum(w.values())
        if total_w <= 0:
            continue

        z = b["z"]
        total = np.zeros(b["n"])
        for cat in CATS_NONFUND:
            if w[cat] <= 0:
                continue
            if cat == "trend":
                idx = [ev.MEMBER_KEYS.index(m) for m in trend_cols]
            else:
                idx = b["cat_idx"][cat]
            total += z[:, idx].mean(axis=1) * (w[cat] / total_w)

        rating = np.array([ev._calc_rating(bool(t), float(s))
                           for t, s in zip(b["trend"], total)])
        order = np.array([ev.RATING_ORDER.get(r, 5) for r in rating])
        rank = np.lexsort((-np.round(total, 1), order))[:top_n]

        row = {"date": b["date"], "regime": label, "top": rank}
        for n in ev.FWD:
            f = b["fwd"][n]
            sel = f["finite"][rank]
            base = f["base"]
            if sel.any() and np.isfinite(base):
                row[n] = float(np.mean(f["values"][rank][sel])) - base
        per_date.append(row)
    return {"dates": per_date}


def summarize(dates: list[dict], start: str, end: str) -> dict:
    rows = [d for d in dates if start <= d["date"] <= end]
    out = {"n_days": len(rows)}
    for n in ev.FWD:
        vals = np.array([d[n] for d in rows if n in d], dtype=float)
        out[str(n)] = {
            "excess_pct": round(float(np.mean(vals)) * 100, 3) if len(vals) else None,
            "win_days_pct": round(100 * float(np.mean(vals > 0)), 1) if len(vals) else None,
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="仅处理前N只股票（调试）")
    ap.add_argument("--min-stocks", type=int, default=200)
    ap.add_argument("--factors", default="0,0.5,1,1.5,2,3")
    ap.add_argument("--trend-mode", default="both", choices=["both", "atr", "turtle"],
                    help="trend 类口径：both=海龟+ATR（真实生产），atr/turtle=只留其一（归因用）")
    ap.add_argument("--out", default="output/trend_calibration.json")
    ap.add_argument("--cache", default="output/trend_calib_scores.pkl",
                    help="评分矩阵缓存；存在则直接复用（评分与权重无关）")
    args = ap.parse_args()

    factors = [float(x) for x in args.factors.split(",")]
    start, end = "2024-07-01", "2026-08-21"

    reg = ev.load_regime(start, end)
    eval_dates = sorted(reg)
    print(f"Regime基准 {ev.BENCH}: {len(reg)} 个交易日 {eval_dates[0]}~{eval_dates[-1]}", flush=True)

    syms = ev.load_stock_syms()
    if args.limit:
        syms = syms[: args.limit]
    print(f"股票池 {len(syms)} 只", flush=True)

    t0 = time.time()
    import os
    if os.path.exists(args.cache):
        with open(args.cache, "rb") as fh:
            blocks, cached_syms = pickle.load(fh)
        print(f"复用评分缓存 {args.cache}（{len(blocks)} 天，股票池 {len(cached_syms)}）", flush=True)
        if cached_syms != syms:
            print("  警告：缓存股票池与本次不一致，重新构建", flush=True)
            blocks = None
    else:
        blocks = None

    if blocks is None:
        big = ev.build_score_matrix(syms, set(eval_dates))
        blocks = prepare_blocks(big, args.min_stocks)
        del big
        with open(args.cache, "wb") as fh:
            pickle.dump((blocks, syms), fh)
        print(f"截面就绪: {len(blocks)} 天，耗时 {time.time()-t0:.0f}s（已缓存）", flush=True)

    results = []
    for f in factors:
        dates = evaluate(blocks, reg, f, trend_mode=args.trend_mode)["dates"]
        row = {
            "factor": f,
            "trend_mode": args.trend_mode,
            "full": summarize(dates, start, end),
            "train": summarize(dates, start, "2025-12-31"),
            "valid": summarize(dates, SPLIT_DATE, end),
            "by_regime": {
                bucket: summarize([d for d in dates if ev.BUCKET3.get(d["regime"]) == bucket],
                                  start, end)
                for bucket in ("bull", "range", "bear")
            },
            # 逐日超额序列：用于配对显著性检验（不同权重在同一天用同一批股票）
            "daily": [{k: v for k, v in d.items() if k != "top"} for d in dates],
        }
        results.append(row)
        print(f"  f={f:<4} 全样本 超额5/10/20="
              f"{row['full']['5']['excess_pct']:+.3f}/"
              f"{row['full']['10']['excess_pct']:+.3f}/"
              f"{row['full']['20']['excess_pct']:+.3f}  "
              f"验证期={row['valid']['5']['excess_pct']:+.3f}/"
              f"{row['valid']['10']['excess_pct']:+.3f}/"
              f"{row['valid']['20']['excess_pct']:+.3f}", flush=True)

    print("\n各 regime 的当前权重（f=1）与候选:")
    for label in ("强牛", "弱牛", "震荡", "弱熊", "强熊"):
        print(f"  {label}: 当前 {_base_weights(label)}")

    payload = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "trend_mode": args.trend_mode,
               "split": {"train_start": start, "train_end": "2025-12-31",
                         "valid_start": SPLIT_DATE, "valid_end": end},
               "results": results}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(f"\n已写出 → {args.out}")


if __name__ == "__main__":
    main()
