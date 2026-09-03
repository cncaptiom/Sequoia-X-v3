"""真实历史推荐快照（rating_result_*.json）的可靠性与盈利测评。

对 output/ 下已存的每日评级结果 JSON（2026-06-11~09-03）按生产口径重建
Top15 推荐列表，用 stock_daily 前复权K线计算未来 5/10/20 交易日收益，
评估命中率 / 收益 / 相对全A等权与指数基准的超额，分市场状态切分。

这是"真实发生了什么"的视图（含当时实盘权重/财务因子/IR覆盖），
与 run_rec_eval.py 的"历史重建当前系统"互为交叉校验。
"""

from __future__ import annotations

import glob
import json
import os
import sqlite3

import numpy as np
import pandas as pd

RATING_ORDER = {"strong_buy": 0, "buy": 1, "watch": 2, "attention": 3, "avoid": 4}
DB_PATH = os.environ.get("DB_PATH", "data/sequoia_v2.db")
START = "2024-01-01"
FWD = [5, 10, 20]


def _load_snapshots(snap_dir: str = "output") -> list[dict]:
    files = sorted(glob.glob(os.path.join(snap_dir, "rating_result_*.json")))
    snaps = []
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as f:
                rows = json.load(f)
        except Exception:
            continue
        raw = os.path.basename(fp).replace("rating_result_", "").replace(".json", "")
        date_str = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
        snaps.append({"date": date_str, "file": fp, "rows": rows})
    return snaps


def _is_stock(s: str) -> bool:
    return s.startswith(("60", "68", "000", "001", "002", "003", "300", "301")) and s not in (
        "000300", "000905", "399006")


def top15_of(rows: list[dict], top_n: int = 15) -> list[dict]:
    """按生产口径取推荐 TopN：评级严重度升序 → total_score 降序。"""
    scored = [r for r in rows if r.get("total_score") is not None]
    scored.sort(key=lambda r: (RATING_ORDER.get(r.get("rating", "avoid"), 5),
                               -float(r.get("total_score", -9e9))))
    return scored[:top_n]


def load_close_map() -> dict[str, pd.Series]:
    """每只股票 open/close 序列（date 索引），供任意 (symbol, date) 前向收益。"""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    df = pd.read_sql(
        "SELECT symbol, date, open, close FROM stock_daily WHERE date>=? ORDER BY symbol, date",
        conn, params=(START,))
    conn.close()
    df = df[df["symbol"].map(_is_stock)]
    out = {}
    for sym, g in df.groupby("symbol"):
        g = g.sort_values("date")
        out[sym] = g.set_index("date")[["open", "close"]]
    return out


def trading_days() -> set[str]:
    """市场交易日 = 任一已存K线的日期（指数或个股）。"""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT DISTINCT date FROM stock_daily WHERE symbol IN ('000300','000905','399006')").fetchall()
    conn.close()
    return {r[0] for r in rows}


def forward_returns(sym: str, rec_date: str, close_map: dict[str, pd.Series]) -> dict:
    """rec_date 收盘买入，N 个交易日（该股自有 bar）后收盘卖出。"""
    ohlc = close_map.get(sym)
    if ohlc is None or rec_date not in ohlc.index:
        return {}
    i = ohlc.index.get_loc(rec_date)
    base = float(ohlc["close"].iloc[i])
    if base <= 0:
        return {}
    res = {}
    for n in FWD:
        if i + n < len(ohlc):
            res[str(n)] = float(ohlc["close"].iloc[i + n]) / base - 1.0
    return res


def stats_of(rets: list[float], n: int) -> dict:
    if not rets:
        return {}
    a = np.array(rets)
    return {
        "n": int(len(a)),
        "mean_ret_pct": round(float(np.mean(a)) * 100, 2),
        "median_ret_pct": round(float(np.median(a)) * 100, 2),
        "win_rate_pct": round(100.0 * float(np.mean(a > 0)), 1),
        "up3_pct": round(100.0 * float(np.mean(a > 0.03)), 1),
        "up5_pct": round(100.0 * float(np.mean(a > 0.05)), 1),
        "down5_pct": round(100.0 * float(np.mean(a < -0.05)), 1),
        "min_pct": round(float(np.min(a)) * 100, 2),
        "max_pct": round(float(np.max(a)) * 100, 2),
    }


def main(top_n: int = 15, snap_dir: str = "output", out_json: str = "output/recommendation_real_check.json") -> dict:
    snaps = _load_snapshots(snap_dir)
    snaps.sort(key=lambda x: x["date"])
    days = trading_days()
    snaps = [s for s in snaps if s["date"] in days]
    print(f"真实快照 {len(snaps)} 份（交易日），区间 {snaps[0]['date']} ~ {snaps[-1]['date']}", flush=True)

    print("加载K线 close/open 映射...", flush=True)
    close_map = load_close_map()

    rec_rows = []   # 每条推荐 (date, symbol, rating, fwdN...)
    daily: dict[str, dict] = {}
    for s in snaps:
        rows = top15_of(s["rows"], top_n=top_n)
        date_str = s["date"]
        recs = []
        for r in rows:
            sym = r["code"]
            fw = forward_returns(sym, date_str, close_map)
            if not fw:
                continue
            rec = {"date": date_str, "symbol": sym, "rating": r.get("rating", ""),
                   "name": r.get("name", "")}
            rec.update(fw)
            rec_rows.append(rec)
            recs.append(rec)
        if recs:
            daily[date_str] = recs
    print(f"可评估推荐：{len(rec_rows)} 条，覆盖 {len(daily)} 个日期", flush=True)

    # 逐日 TopN 组合（等权，未成交日剔除）
    combos = {str(n): [] for n in FWD}
    for d, recs in daily.items():
        for n in FWD:
            vals = [x[str(n)] for x in recs if str(n) in x]
            if vals:
                combos[str(n)].append(float(np.mean(vals)))

    out = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "config": {"top_n": top_n, "entry": "rec日收盘买入，N交易日(自有bar)收盘卖出",
                   "fwd": FWD, "snapshot_files": len(snaps),
                   "note": "真实历史评级快照（含当时实盘权重/财务因子/IR覆盖），多为旧权重；与 run_rec_eval 历史重建互为校验"},
        "summary_pooled": {},
        "summary_combined_series": {},
        "unique_stocks": int(len({x["symbol"] for x in rec_rows})),
    }
    for n in FWD:
        pool = [x[str(n)] for x in rec_rows if str(n) in x]
        out["summary_pooled"][str(n)] = stats_of(pool, n)
        vals = combos[str(n)]
        if vals:
            a = np.array(vals)
            out["summary_combined_series"][str(n)] = {
                "n_days": int(len(a)),
                "mean_pct": round(float(np.mean(a)) * 100, 2),
                "median_pct": round(float(np.median(a)) * 100, 2),
                "win_days_pct": round(100.0 * float(np.mean(a > 0)), 1),
                "total_pct": round((np.prod(1 + a) - 1) * 100, 2),
            }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n已写出 → {out_json}")
    for n in FWD:
        p = out["summary_pooled"][str(n)]
        c = out["summary_combined_series"][str(n)]
        print(f"  {n:>2}日: 单票池化n={p['n']:5d} 均收益={p['mean_ret_pct']:+.2f}% "
              f"中位={p['median_ret_pct']:+.2f}% 胜率={p['win_rate_pct']}% "
              f"| 逐日等权{n:>2}日: n={c['n_days']} 均={c['mean_pct']:+.2f}% 累计={c['total_pct']:+.2f}%")
    return out


if __name__ == "__main__":
    main()
