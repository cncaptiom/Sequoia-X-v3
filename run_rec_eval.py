"""测评：历史重建"当前系统推荐"的可靠性与盈利效果。

用当前评分配置（RatingConfig 默认 + 每日 REGIME_BIAS 权重，剔除财务因子，
不含 IR 自适应覆盖）回放 2024-07~2026-08 全市场每日评分，按生产口径组每日
Top15 综合推荐榜，计算未来 5/10/20 交易日收益，评估命中率 / 收益 / 相对
全A等权与基准指数的超额，分市场状态切分。

与 run_rec_realcheck.py（真实历史快照）互为交叉校验。
输出 output/recommendation_eval.json。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import time

import numpy as np
import pandas as pd

from sequoia_x.eval.replay_scores import compute_tech_series
from sequoia_x.eval.regime_series import point_in_time_regimes
from sequoia_x.rating.engine import RatingConfig

DB_PATH = os.environ.get("DB_PATH", "data/sequoia_v2.db")
HIST_START = "2024-01-01"
BENCH = "000300"
FWD = [5, 10, 20]
MEMBER_KEYS = ["turtleTrade", "meanReversion", "atrChannel", "maCross", "kdj"]
CATS = {
    "trend": ["turtleTrade", "atrChannel"],
    "momentum": ["maCross"],
    "reversal": ["meanReversion", "kdj"],
}
RATING_ORDER = {"strong_buy": 0, "buy": 1, "watch": 2, "attention": 3, "avoid": 4}
BUCKET3 = {"强牛": "bull", "弱牛": "bull", "震荡": "range", "弱熊": "bear", "强熊": "bear"}


def _is_stock(s: str) -> bool:
    return s.startswith(("60", "68", "000", "001", "002", "003", "300", "301")) and s not in (
        "000300", "000905", "399006")


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if len(x) < 10:
        return np.nan
    xr = pd.Series(x).rank().values
    yr = pd.Series(y).rank().values
    a = xr - xr.mean()
    b = yr - yr.mean()
    den = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / den) if den > 0 else np.nan


def weights_for(label: str) -> dict[str, float]:
    """当前配置在某 regime 下启用成员权重（剔除财务；fundamental 权重归 0）。"""
    cfg = RatingConfig()
    cfg.fundamental_enabled = False
    object.__setattr__(cfg, "weight_fundamental", 0.0)
    cfg.apply_regime(label)
    return {k: float(getattr(cfg, "weight_" + {
        "turtleTrade": "turtle_trade", "meanReversion": "mean_reversion",
        "atrChannel": "atr_channel", "maCross": "ma_cross", "kdj": "kdj"}[k]))
        for k in MEMBER_KEYS}


def _calc_rating(trend: bool, total: float) -> str:
    if not trend:
        if total >= 0.5:
            return "watch"
        if total >= 0:
            return "attention"
        return "avoid"
    if total >= 1.5:
        return "strong_buy"
    if total >= 1.0:
        return "buy"
    if total >= 0.5:
        return "watch"
    if total >= 0:
        return "attention"
    return "avoid"


def _winsorize_z(mat: np.ndarray) -> np.ndarray:
    """逐列 winsorize(1/99)+zscore，同 sequoia_x.signal.quality。mat (n, k)。"""
    out = np.empty_like(mat)
    for j in range(mat.shape[1]):
        x = mat[:, j].astype(float)
        lo, hi = np.nanpercentile(x, [1.0, 99.0])
        x = np.clip(x, lo, hi)
        mu = float(np.mean(x))
        sd = float(np.std(x))
        out[:, j] = (x - mu) / sd if sd > 1e-10 else 0.0
    return out


def stats_of(rets: np.ndarray) -> dict:
    a = np.asarray(rets, dtype=float)
    a = a[np.isfinite(a)]
    if len(a) == 0:
        return {}
    return {
        "n": int(len(a)),
        "mean_pct": round(float(np.mean(a)) * 100, 2),
        "median_pct": round(float(np.median(a)) * 100, 2),
        "win_rate_pct": round(100.0 * float(np.mean(a > 0)), 1),
        "up3_pct": round(100.0 * float(np.mean(a > 0.03)), 1),
        "up5_pct": round(100.0 * float(np.mean(a > 0.05)), 1),
        "down5_pct": round(100.0 * float(np.mean(a < -0.05)), 1),
        "min_pct": round(float(np.min(a)) * 100, 2),
        "max_pct": round(float(np.max(a)) * 100, 2),
    }


def load_stock_syms() -> list[str]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    rows = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM stock_daily")]
    conn.close()
    return sorted(s for s in rows if _is_stock(s))


def load_regime(start: str, end: str) -> dict[str, str]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    df = pd.read_sql(
        "SELECT date,open,high,low,close,volume FROM stock_daily "
        "WHERE symbol=? ORDER BY date", conn, params=(BENCH,))
    conn.close()
    reg = point_in_time_regimes(df)
    return {d: l for d, l in reg.items() if start <= d <= end}


def load_symbol_df(conn, sym: str) -> pd.DataFrame:
    df = pd.read_sql(
        "SELECT date,open,high,low,close,volume FROM stock_daily "
        "WHERE symbol=? AND date>=? ORDER BY date", conn, params=(sym, HIST_START))
    df["close"] = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)
    return df


def _gate_snapshot_date() -> str | None:
    """最新一个带 per_strategy_scores 内容、且数据里有K线的评级快照日。"""
    import sqlite3 as _sq

    fps = sorted(glob.glob("output/rating_result_*.json"), reverse=True)
    if not fps:
        return None
    conn = _sq.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    max_d = conn.execute("SELECT MAX(date) FROM stock_daily WHERE symbol=?", ("000905",)).fetchone()[0]
    conn.close()
    for fp in fps:
        raw = os.path.basename(fp).replace("rating_result_", "").replace(".json", "")
        iso = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
        if iso > (max_d or "9999"):
            continue
        try:
            with open(fp, encoding="utf-8") as f:
                head = json.load(f)[:300]
        except Exception:
            continue
        if any(isinstance(r.get("per_strategy_scores"), dict)
               and any(v is not None for v in r["per_strategy_scores"].values()) for r in head):
            return iso
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-07-01")
    ap.add_argument("--end", default="2026-08-21")
    ap.add_argument("--limit", type=int, default=0, help="仅处理前N只股票（调试）")
    ap.add_argument("--min-stocks", type=int, default=200)
    ap.add_argument("--out", default="output/recommendation_eval.json")
    args = ap.parse_args()

    reg = load_regime(args.start, args.end)
    eval_dates = sorted(reg)
    print(f"Regime基准 {BENCH}: {len(reg)} 个交易日 {eval_dates[0]}~{eval_dates[-1]}", flush=True)
    syms = load_stock_syms()
    if args.limit:
        syms = syms[: args.limit]
    print(f"股票池 {len(syms)} 只", flush=True)

    eval_set = set(eval_dates)
    gate = _gate_snapshot_date()
    score_dates = set(eval_dates)
    if gate:
        score_dates.add(gate)
    print(f"保真门对照快照日: {gate}（快照带 per_strategy 分的最新日）", flush=True)
    snap_cache: dict[str, dict] = {}
    if gate:
        raw = gate.replace("-", "")
        with open(f"output/rating_result_{raw}.json", encoding="utf-8") as f:
            snap_cache = {r["code"]: r for r in json.load(f)}

    reg_weights = {label: weights_for(label) for label in set(reg.values())}
    # 预计算每日 regime 权重
    daily_w: dict[str, dict[str, float]] = {d: reg_weights[reg[d]] for d in eval_dates}

    rows: list[dict] = []
    t0 = time.time()
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    for idx, sym in enumerate(syms):
        df = load_symbol_df(conn, sym)
        if df.empty or len(df) < 60:
            continue
        tech = compute_tech_series(df)
        close = df["close"]
        in_eval = df["date"].isin(score_dates).values
        valid = (tech["bar_idx"].values >= 59) & (~tech["gap_excl"].values)
        keep = in_eval & valid
        if not keep.any():
            continue
        t = tech.loc[keep]
        dts = df.loc[keep, "date"].tolist()
        rec = {
            "date": dts,
            "symbol": [sym] * len(dts),
            "trend": t["trend_ok"].astype(int).tolist(),
        }
        for k in MEMBER_KEYS:
            rec[k] = t[f"score_{k}"].astype(float).tolist()
        c = close.values
        for n in FWD:
            fwd = pd.Series(c).shift(-n) / c - 1.0
            rec[f"fwd{n}"] = fwd.iloc[keep].values.tolist()
        rows.append(rec)
        if (idx + 1) % 800 == 0:
            print(f"  评分 {idx+1}/{len(syms)} 耗时 {time.time()-t0:.0f}s", flush=True)
    conn.close()
    # 摊平为行
    flat = []
    for grp in rows:
        n = len(grp["date"])
        for i in range(n):
            row = {"date": grp["date"][i], "symbol": grp["symbol"][i],
                   "trend": grp["trend"][i]}
            for k in MEMBER_KEYS:
                row[k] = grp[k][i]
            for nd in FWD:
                row[f"fwd{nd}"] = grp[f"fwd{nd}"][i]
            flat.append(row)
    big = pd.DataFrame(flat)
    del rows, flat
    big = big.sort_values("date").reset_index(drop=True)
    print(f"评分行 {len(big)}（日期 {big['date'].nunique()}）构建耗时 {time.time()-t0:.0f}s", flush=True)

    # 逐日截面 → 合成分/评级 → 组内排序取 Top
    days_out = []
    gate_report = {}
    for date_str, blk in big.groupby("date", sort=False):
        n_m = len(blk)
        is_eval = date_str in eval_set
        if (not is_eval and date_str != gate) or n_m < args.min_stocks:
            continue
        mat = blk[MEMBER_KEYS].to_numpy(dtype=float)
        z = _winsorize_z(mat)
        if is_eval:
            w = daily_w[date_str]
            total = np.zeros(n_m)
            catw = {}
            totalw = 0.0
            for cat, members in CATS.items():
                cw = sum(w[m] for m in members)
                if cw > 0:
                    catw[cat] = cw
                    totalw += cw
            if totalw <= 0:
                totalw = 1.0
                catw = {c: 1.0 for c in CATS}
            for cat, cw in catw.items():
                members = CATS[cat]
                col_idx = [MEMBER_KEYS.index(m) for m in members]
                catmean = z[:, col_idx].mean(axis=1)
                total += catmean * (cw / totalw)
            trend = blk["trend"].to_numpy() > 0
            rating = np.array([_calc_rating(bool(trend[i]), total[i]) for i in range(len(total))])
            order = np.array([RATING_ORDER.get(r, 5) for r in rating])
            # 排序键 (rating_order, -round(total,1))
            sort_v = np.lexsort((-np.round(total, 1), order))
            top_idx = sort_v[:30]
            recs = []
            for pos, i in enumerate(top_idx):
                recs.append({
                    "rank": pos + 1,
                    "symbol": blk["symbol"].iloc[i],
                    "rating": rating[i],
                    "total": round(float(total[i]), 2),
                    **{f"fwd{n}": (float(blk[f"fwd{n}"].iloc[i]) if np.isfinite(blk[f"fwd{n}"].iloc[i]) else None) for n in FWD},
                })
            base = {}
            for n in FWD:
                arr = blk[f"fwd{n}"].to_numpy(dtype=float)
                arr = arr[np.isfinite(arr)]
                base[n] = float(np.mean(arr)) if len(arr) else np.nan
            days_out.append({
                "date": date_str, "regime": reg[date_str], "bucket3": BUCKET3.get(reg[date_str], "?"),
                "n_market": int(n_m), "base": base, "recs": recs,
            })
        if date_str == gate:
            sym2z = {}
            for i in range(len(blk)):
                sym2z[blk["symbol"].iloc[i]] = {MEMBER_KEYS[j]: float(z[i, j]) for j in range(len(MEMBER_KEYS))}
            gate_report = {"date": date_str, "by_strategy": {}}
            for k in MEMBER_KEYS:
                live = {}
                for sym in sym2z:
                    rec = snap_cache.get(sym) or {}
                    ps = rec.get("per_strategy_scores") or {}
                    if isinstance(ps, dict) and ps.get(k) is not None:
                        live[sym] = ps[k]
                common = [s for s in live if sym2z[s][k] == sym2z[s][k]]
                a = np.array([live[s] for s in common])
                b = np.array([sym2z[s][k] for s in common])
                gate_report["by_strategy"][k] = {
                    "spearman": round(float(_spearman(a, b)), 4),
                    "n": len(common),
                }
    print(f"覆盖评估日 {len(days_out)} 天", flush=True)
    if gate_report:
        print(f"保真门 {gate_report['date']}:",
              " ".join(f"{k}={v['spearman']:.3f}(n={v['n']})" for k, v in gate_report["by_strategy"].items()), flush=True)

    # ── 指标聚合 ──
    def pool(df_days, topn, nd):
        vals, excess = [], []
        for d in df_days:
            base = d["base"][nd]
            if not np.isfinite(base):
                continue
            recs = [r for r in d["recs"] if r["rank"] <= topn and r[f"fwd{nd}"] is not None]
            if not recs:
                continue
            v = np.mean([r[f"fwd{nd}"] for r in recs])
            vals.append(v)
            excess.append(v - base)
        return np.array(vals), np.array(excess)

    summary = {"pooled": {}, "series": {}, "excess": {}, "regime": {}, "by_rating": {}}
    for nd in FWD:
        summary["pooled"][str(nd)] = stats_of(np.array(
            [r[f"fwd{nd}"] for d in days_out for r in d["recs"]
             if r["rank"] <= 15 and r[f"fwd{nd}"] is not None]))
    for topn in (5, 15):
        for nd in FWD:
            v, e = pool(days_out, topn, nd)
            if len(v) == 0:
                continue
            summary["series"].setdefault(str(topn), {})[str(nd)] = {
                "n_days": int(len(v)),
                "mean_pct": round(float(np.mean(v)) * 100, 2),
                "median_pct": round(float(np.median(v)) * 100, 2),
                "win_days_pct": round(100.0 * float(np.mean(v > 0)), 1),
                "cum_pct": round((np.prod(1 + v) - 1) * 100, 2),
            }
            summary["excess"].setdefault(str(topn), {})[str(nd)] = {
                "mean_pct": round(float(np.mean(e)) * 100, 2),
                "win_days_pct": round(100.0 * float(np.mean(e > 0)), 1),
            }
    # 市场平均前向收益（逐日全市场等权基准，按评估日池化）
    base_series = {str(n): float(np.nanmean([d["base"][n] for d in days_out])) for n in FWD}
    # 分市场状态
    for nd in FWD:
        summary["regime"][str(nd)] = {}
        for bucket in ("bull", "range", "bear"):
            ds = [d for d in days_out if d["bucket3"] == bucket]
            v = np.array([r[f"fwd{nd}"] for d in ds for r in d["recs"]
                          if r["rank"] <= 15 and r[f"fwd{nd}"] is not None])
            if len(v) == 0:
                continue
            e = np.array([r[f"fwd{nd}"] - d["base"][nd]
                          for d in ds for r in d["recs"]
                          if r["rank"] <= 15 and r[f"fwd{nd}"] is not None
                          and np.isfinite(d["base"][nd])])
            st = stats_of(v)
            e = e[np.isfinite(e)]
            st["excess_pct"] = round(float(np.mean(e)) * 100, 2) if len(e) else None
            st["n_days"] = len(ds)
            summary["regime"][str(nd)][bucket] = st
    # 评级分桶（20日）——买入级及以上
    strong = [r for d in days_out for r in d["recs"] if r["rating"] in ("strong_buy", "buy")]
    uniq = sorted({r["symbol"] for d in days_out for r in d["recs"] if r["rank"] <= 15})
    report = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "config": {
            "bench": BENCH, "regime_note": f"{BENCH} 到 {eval_dates[-1]}；000905 尾段脏数据(08-24起 -99.9% 崩断)弃用",
            "weights": "RatingConfig 默认 + 每日 REGIME_BIAS，剔除财务因子(fundamental=0)折算；未含 IR 自适应覆盖",
            "entry": "评分日收盘买入，N 交易日(自有bar)收盘卖出；未计成本/涨跌停/停牌不可买",
            "universe": "A股(6/0/3开头) 每评估日 ≥60根K线、近120根无>21%复权跳变、当日有成交",
            "start": args.start, "end": args.end, "top_n_headline": 15,
        },
        "data": {
            "symbols": len(syms), "eval_days": len(days_out),
            "regime_days": {k: int(v) for k, v in
                            pd.Series([d["regime"] for d in days_out]).value_counts().items()},
        },
        "summary_pooled_top15": summary["pooled"],
        "series_equalweight": summary["series"],
        "excess_vs_market": summary["excess"],
        "market_baseline_avg_fwd_pct": {k: round(v * 100, 2) for k, v in base_series.items()},
        "by_regime3": summary["regime"],
        "buy_above_count": len(strong),
        "unique_rec_stocks_top15": len(uniq),
        "gate_parity": gate_report,
        "honesty_notes": [
            "回放为'当前配置'在历史的反播：权重按2026年数据校准，回放存在轻微前视乐观；真 out-of-sample 需实盘再积累数周",
            "剔除财务因子：仅代表技术因子部分的推荐；fundamental 无历史只能现期单看",
            "未含实盘 IR 自适应覆盖层（近期会把多数策略权重清零，仅留正IR者）——按设计权重近似，故与真实当日Top15会不同",
            "无交易成本/滑点/印花税；未过滤开盘涨停不可买、跌停不可卖；停牌按自有bar顺延",
            "幸存者偏差：库内基本为现上市股票，退市股缺失",
        ],
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"\n已写出 → {args.out}")
    for nd in FWD:
        p = summary["pooled"][str(nd)]
        s = summary["series"].get("15", {}).get(str(nd), {})
        ex = summary["excess"].get("15", {}).get(str(nd), {})
        print(f"  Top15 {nd:>2}日: 单票池化 n={p['n']:6d} 均={p['mean_pct']:+.2f}% 中位={p['median_pct']:+.2f}% "
              f"胜率={p['win_rate_pct']}% 跌5%占={p['down5_pct']}% "
              f"| 逐日等权均={s.get('mean_pct','-')}% 超额={ex.get('mean_pct','-')}%")
    print("  全A等权基准均前向(单票池化口径基准请以逐日base为准): ",
          {k: v['mean_pct'] for k, v in summary['pooled'].items()})
    return report


if __name__ == "__main__":
    main()
