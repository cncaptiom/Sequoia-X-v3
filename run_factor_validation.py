"""全量因子预测力验证：用 2024 年起全市场历史数据重建因子序列，
分市场状态测各因子对 5/10/20 日未来收益的 IC、五分位收益、信号触发后收益。

输出 JSON 到 output/factor_validation.json（含结论摘要与明细）。
仅做只读分析，不修改任何业务代码。
"""

import json
import os
import sqlite3
import time
from collections import defaultdict

import numpy as np
import pandas as pd

os.environ.setdefault("DB_PATH", "data/sequoia_v2.db")

DB_PATH = "data/sequoia_v2.db"
START = "2024-01-02"        # 全市场截面从此日起可用
LOOKBACK = 260              # 因子计算需要的最长回看（约一年交易日）
FWD = [5, 10, 20]
BENCH = "000300"            # 市场状态基准（无后缀）

# 跳变过滤阈值（前复权残留假跳变）
GAP = 0.21


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman 秩相关，纯 numpy 实现（避免 scipy 依赖差异）。"""
    if len(x) < 10:
        return np.nan
    xs = pd.Series(x).rank().values
    ys = pd.Series(y).rank().values
    a = xs - xs.mean()
    b = ys - ys.mean()
    den = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / den) if den > 0 else np.nan


def _winsorize(arr: np.ndarray, lo: float = 1.0, hi: float = 99.0) -> np.ndarray:
    a = arr.copy()
    a = np.nan_to_num(a, nan=np.nanmean(a)) if np.isnan(a).all() else a
    p = np.nanpercentile(a, [lo, hi])
    return np.clip(a, p[0], p[1])


def _ic_stats(ics: np.ndarray, overlap: int) -> dict:
    """IC 序列的均值/IR 及**自相关修正**后的显著性。

    N 日前瞻收益是重叠的：相邻交易日的 IC 并非独立样本，普通 mean/std
    会把标准误低估约 sqrt(overlap) 倍，导致 IR 与显著性虚高。
    这里用 Newey-West（Bartlett 核，滞后 = overlap-1）估计长期方差。

    返回的 `n_eff = n / overlap` 是独立样本数的粗略下界，用于判断显著性时
    应同时看它——n_eff 很小时 t_nw 本身也不可靠。
    """
    x = np.asarray(ics, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 3:
        return {"mean_ic": None, "ir": None, "ir_nw": None, "t_naive": None,
                "t_nw": None, "n_days": n, "n_eff": 0}

    mean = float(x.mean())
    std = float(x.std(ddof=1))
    lags = max(0, int(overlap) - 1)
    centered = x - mean
    var_nw = float((centered ** 2).sum() / n)
    for k in range(1, lags + 1):
        gamma = float((centered[k:] * centered[:-k]).sum() / n)
        var_nw += 2.0 * (1.0 - k / (lags + 1)) * gamma
    var_nw = max(var_nw, 0.0)

    se_naive = std / np.sqrt(n) if std > 0 else np.nan
    se_nw = np.sqrt(var_nw / n) if var_nw > 0 else np.nan
    return {
        "mean_ic": round(mean, 4),
        "ir": round(mean / std, 2) if std > 0 else 0,
        "ir_nw": round(mean / np.sqrt(var_nw), 2) if var_nw > 0 else None,
        "t_naive": round(mean / se_naive, 2) if se_naive and not np.isnan(se_naive) else None,
        "t_nw": round(mean / se_nw, 2) if se_nw and not np.isnan(se_nw) else None,
        "n_days": n,
        "n_eff": int(n / max(1, overlap)),
    }


def load_kline() -> dict[str, pd.DataFrame]:
    """加载全市场日线，按 symbol 分组。返回 {symbol: DataFrame}。"""
    t0 = time.time()
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    q = f"""
        SELECT symbol, date, open, high, low, close, volume, turnover
        FROM stock_daily
        WHERE date >= '{START}'
        ORDER BY symbol, date
    """
    df = pd.read_sql_query(q, conn)
    conn.close()
    # 过滤指数/垃圾符号（保留 A 股：6/0/3 开头）
    def _is_stock(s):
        return s.startswith(("60", "68", "000", "001", "002", "003", "300", "301"))
    df = df[df["symbol"].map(_is_stock)].copy()
    print(f"  K线行数 {len(df)}, 股票 {df['symbol'].nunique()}, 载入耗时 {time.time()-t0:.1f}s", flush=True)
    out = {}
    for sym, g in df.groupby("symbol"):
        g = g.sort_values("date").reset_index(drop=True)
        out[sym] = g
    return out


def compute_factors(sym: str, g: pd.DataFrame) -> pd.DataFrame | None:
    """对单只股票计算因子序列。返回长表行（含 fwd 收益）。"""
    if len(g) < 30:
        return None
    c = g["close"].astype(float)
    v = g["volume"].astype(float)
    h = g["high"].astype(float)
    l = g["low"].astype(float)

    # 跳变过滤：单日 |涨跌| > 21% 视为复权残留，该日标记 NaN（影响后续滚动）
    daily_ret = c.pct_change()
    bad = daily_ret.abs() > GAP
    c = c.mask(bad)          # 异常日 close 置 NaN
    h = h.mask(bad)
    l = l.mask(bad)

    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    ma120 = c.rolling(120).mean()
    ma5 = c.rolling(5).mean()
    ma10 = c.rolling(10).mean()

    # ATR
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()

    # RSI
    delta = c.diff()
    up = delta.clip(lower=0).rolling(14).mean()
    dn = (-delta.clip(upper=0)).rolling(14).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))

    d = pd.DataFrame(index=g.index)
    d["date"] = g["date"]
    d["symbol"] = sym
    d["close"] = c
    # 未来收益（Y）
    for n in FWD:
        d[f"fwd{n}"] = c.shift(-n) / c - 1.0
    # 因子（X）
    d["mom5"] = c / c.shift(5) - 1
    d["mom10"] = c / c.shift(10) - 1
    d["mom20"] = c / c.shift(20) - 1
    d["mom60"] = c / c.shift(60) - 1
    d["mom120"] = c / c.shift(120) - 1
    d["rsi14"] = rsi
    d["bias20"] = c / ma20 - 1
    d["bias60"] = c / ma60 - 1
    d["dist_ma120"] = c / ma120 - 1
    d["vol_ratio"] = v / v.rolling(20).mean() - 1
    d["atr_pct"] = atr / c
    d["vol20"] = daily_ret.rolling(20).std()
    d["pos120"] = c / c.rolling(120).max()
    d["pos250"] = c / c.rolling(250).max()
    d["trend_ma"] = ((ma5 > ma10) & (ma10 > ma20)).astype(float)

    # 类策略信号（触发日 = 1）
    prev20_high = c.shift(1).rolling(20).max()
    d["sig_turtle"] = ((c > prev20_high) & (c > 0)).astype(float)
    d["sig_ma_golden"] = ((ma5 > ma20) & (ma5.shift(1) <= ma20.shift(1)) & (v > v.rolling(20).mean() * 1.5)).astype(float)
    roll120_max = c.rolling(120).max()
    # rps120 需要横截面，先用 mom120 近似占位，后面统一算
    d["sig_rps"] = np.nan  # 占位，横截面阶段再填
    # 均值回归：布林下轨 + RSI 超卖
    bb_low = ma20 - 2 * c.rolling(20).std()
    d["sig_meanrev"] = ((c < bb_low) & (rsi < 30)).astype(float)
    # 高窄旗形：40日涨幅>60% + 10日振幅<15% + 缩量
    ret40 = c / c.shift(40) - 1
    amp10 = (c.rolling(10).max() / c.rolling(10).min() - 1)
    d["sig_hightight"] = ((ret40 > 0.6) & (amp10 < 0.15) & (v < v.rolling(20).mean() * 0.6)).astype(float)

    # 只保留需要的列 + 有效数据行
    cols = ["date", "symbol", "close", "fwd5", "fwd10", "fwd20",
            "mom5", "mom10", "mom20", "mom60", "mom120", "rsi14", "bias20", "bias60",
            "dist_ma120", "vol_ratio", "atr_pct", "vol20", "pos120", "pos250", "trend_ma",
            "sig_turtle", "sig_ma_golden", "sig_rps", "sig_meanrev", "sig_hightight"]
    d = d[cols]
    return d.dropna(subset=["mom60"])


def compute_market_regime(kline: dict) -> dict[str, str]:
    """用基准指数 close/MA120 判定每日市场状态：bull/range/bear。"""
    g = kline.get(BENCH)
    if g is None:
        print(f"  警告：基准 {BENCH} 不在K线中，改用 000905", flush=True)
        g = kline.get("000905")
    if g is None:
        print("  警告：无基准指数，市场状态标记为 unknown", flush=True)
        return {}
    c = g["close"].astype(float)
    ma120 = c.rolling(120).mean()
    ratio = c / ma120
    regime = pd.Series("range", index=g.index, dtype=object)
    regime[ratio > 1.03] = "bull"
    regime[ratio < 0.97] = "bear"
    # 最后 120 天无 MA120，用 close 相对启动日判断
    for i in range(len(g)):
        if np.isnan(ma120.iloc[i]):
            base = c.iloc[max(0, i - 120)]
            r = c.iloc[i] / base
            regime.iloc[i] = "bull" if r > 1.03 else "bear" if r < 0.97 else "range"
    return dict(zip(g["date"], regime))


def main():
    print("加载K线...", flush=True)
    kline = load_kline()
    bench_sym = BENCH if BENCH in kline else "000905"
    regime = compute_market_regime(kline)
    print(f"  市场状态日期: {len(regime)} (bull/range/bear)", flush=True)

    print("计算因子...", flush=True)
    frames = []
    t0 = time.time()
    for i, (sym, g) in enumerate(kline.items()):
        if sym in (BENCH, "000905", "399006"):
            continue
        f = compute_factors(sym, g)
        if f is not None and len(f):
            frames.append(f)
        if (i + 1) % 1000 == 0:
            print(f"  进度 {i+1}/{len(kline)} 耗时 {time.time()-t0:.0f}s", flush=True)
    df = pd.concat(frames, ignore_index=True)
    del frames
    print(f"  长表行数 {len(df)} 耗时 {time.time()-t0:.0f}s", flush=True)

    # RPS120：mom120 的横截面百分位
    print("计算 RPS120 横截面...", flush=True)
    df["rps120"] = df.groupby("date")["mom120"].rank(pct=True) * 100
    # sig_rps：RPS>=90 且 收盘在120日最高90%以上
    df["sig_rps"] = ((df["rps120"] >= 90) & (df["close"] >= df.groupby("symbol")["close"].transform(lambda s: s.rolling(120).max()) * 0.90)).astype(float)

    # 市场状态列
    df["regime"] = df["date"].map(regime).fillna("unknown")

    # ══════════════════════════
    # 分析 1：因子 IC（分市场状态）
    # ══════════════════════════
    factor_cols = ["mom5", "mom10", "mom20", "mom60", "mom120", "rsi14", "bias20", "bias60",
                   "dist_ma120", "vol_ratio", "atr_pct", "vol20", "pos120", "pos250", "trend_ma", "rps120"]
    sig_cols = ["sig_turtle", "sig_ma_golden", "sig_rps", "sig_meanrev", "sig_hightight"]

    print("计算 IC...", flush=True)
    # ic_results[factor][fwd] = [(regime, ic)] 明细；聚合到 ic_summary[factor][fwd][regime]
    ic_details = {f: {n: [] for n in FWD} for f in factor_cols + sig_cols}
    for date, grp in df.groupby("date"):
        if len(grp) < 300:
            continue
        rg = grp["regime"].iloc[0]
        for n in FWD:
            y = grp[f"fwd{n}"].values
            for f in factor_cols + sig_cols:
                x = grp[f].values
                mask = ~(np.isnan(x) | np.isnan(y))
                if mask.sum() < 100:
                    continue
                ic = _spearman(x[mask], y[mask])
                if not np.isnan(ic):
                    ic_details[f][n].append((rg, ic))
    print(f"  IC 明细完成，共 {len(df['date'].unique())} 个日期", flush=True)

    ic_summary = {}
    for f in factor_cols + sig_cols:
        ic_summary[f] = {}
        for n in FWD:
            by_regime = defaultdict(list)
            for rg, ic in ic_details[f][n]:
                by_regime[rg].append(ic)
            row = {}
            for rg, ics in by_regime.items():
                stats = _ic_stats(np.array(ics), overlap=n)
                stats["pos_pct"] = round(100 * float(np.mean(np.array(ics) > 0)), 1)
                row[rg] = stats
            ic_summary[f][str(n)] = row

    # ══════════════════════════
    # 分析 2：五分位收益（跨期池化，按因子 rank 分 5 组）
    # ══════════════════════════
    print("计算五分位收益...", flush=True)
    quantile_summary = {}
    for f in factor_cols:
        tmp = df[["date", f, "fwd5", "fwd10", "fwd20", "regime"]].dropna(subset=[f]).copy()
        if len(tmp) < 1000:
            continue
        tmp["q"] = tmp.groupby("date")[f].transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False))
        row = {}
        for n in FWD:
            q = {}
            for qi in range(5):
                a = tmp[tmp["q"] == qi][f"fwd{n}"]
                q[f"q{qi+1}"] = round(float(a.mean()) * 100, 2)
            q["spread"] = round(q.get("q5", 0) - q.get("q1", 0), 2)
            row[str(n)] = q
        quantile_summary[f] = row

    # ══════════════════════════
    # 分析 3：信号触发后收益（vs 同日全市场平均）
    # ══════════════════════════
    print("计算信号触发后收益...", flush=True)
    sig_summary = {}
    for s in sig_cols:
        tmp = df[["date", s, "fwd5", "fwd10", "fwd20", "regime"]].dropna(subset=[s]).copy()
        triggered = tmp[tmp[s] == 1]
        if len(triggered) < 100:
            sig_summary[s] = {"n_signals": int(len(triggered))}
            continue
        # 同日全市场平均（基准）
        base = tmp.groupby("date")[["fwd5", "fwd10", "fwd20"]].mean()
        tr = triggered.merge(base.rename(columns={"fwd5": "b5", "fwd10": "b10", "fwd20": "b20"}), on="date", how="left")
        row = {"n_signals": int(len(tr)), "by_regime": {}}
        for n in FWD:
            col = f"fwd{n}"
            bcol = f"b{n}"
            row[f"ret_{n}d"] = round(float(tr[col].mean()) * 100, 2)
            row[f"excess_{n}d"] = round(float((tr[col] - tr[bcol]).mean()) * 100, 2)
            row[f"win_{n}d"] = round(100 * float((tr[col] > 0).mean()), 1)
        for rg, g2 in tr.groupby("regime"):
            if len(g2) < 50:
                continue
            row["by_regime"][rg] = {
                "n": int(len(g2)),
                "ret_20d": round(float(g2["fwd20"].mean()) * 100, 2),
                "excess_20d": round(float((g2["fwd20"] - g2["b20"]).mean()) * 100, 2),
                "win_20d": round(100 * float((g2["fwd20"] > 0).mean()), 1),
            }
        sig_summary[s] = row

    # ══════════════════════════
    # 汇总：最佳因子排名（20日IC，按总体 mean_ic）
    # ══════════════════════════
    rank = []
    for f in factor_cols + sig_cols:
        r = ic_summary[f].get("20", {})
        all_ics = []
        for rg in ("bull", "range", "bear"):
            if rg in r and r[rg].get("mean_ic") is not None:
                all_ics.append(r[rg]["mean_ic"])
        if all_ics:
            rank.append({"factor": f, "mean_ic_20d": round(float(np.mean(all_ics)), 4)})
    rank.sort(key=lambda x: -x["mean_ic_20d"])

    output = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "method_note": ("ic_summary[*]['ir'] 是朴素 mean/std，仅作展示；"
                        "重叠前瞻窗口下显著性必须看 t_nw 与 n_eff，"
                        "不要用 ir 判断显著"),
        "config": {"start": START, "bench": bench_sym, "lookback": LOOKBACK, "fwd": FWD},
        "data": {"symbols": df["symbol"].nunique(), "rows": len(df), "dates": int(df["date"].nunique())},
        "factor_ic": ic_summary,
        "quantile_returns": quantile_summary,
        "signal_returns": sig_summary,
        "factor_ranking": rank,
        "market_regime_days": {k: int(v) for k, v in defaultdict(int, df.groupby("regime")["date"].nunique()).items()},
    }
    with open("output/factor_validation.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=1)
    print(f"\n已完成 → output/factor_validation.json")

    # 终端摘要
    print("\n===== 因子 20日IC 排名（分市场状态；t_nw 为重叠窗口自相关修正后 t 值）=====")
    for item in rank:
        f = item["factor"]
        r20 = ic_summary[f].get("20", {})
        parts = []
        for rg in ("bull", "range", "bear"):
            if rg in r20:
                stats = r20[rg]
                t_nw = stats.get("t_nw")
                t_txt = "n/a" if t_nw is None else f"{t_nw:+.2f}"
                parts.append(f"{rg}:{stats['mean_ic']:+.3f}(t_nw={t_txt},n_eff={stats['n_eff']})")
        print(f"  {f:14s} IC20={'None' if item['mean_ic_20d'] is None else f'{item['mean_ic_20d']:+.4f}'}  {'  '.join(parts)}")
    print("\n===== 信号触发后 20日 收益（超额 vs 全市场）=====")
    for s in sig_cols:
        row = sig_summary.get(s, {})
        if "ret_20d" in row:
            print(f"  {s:16s} n={row['n_signals']:6d} 收益={row['ret_20d']:+.2f}% 超额={row['excess_20d']:+.2f}% 胜率={row['win_20d']}%")


if __name__ == "__main__":
    main()
