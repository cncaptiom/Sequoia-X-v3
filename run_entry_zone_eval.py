"""标定买入区间半宽 k：逐日重放历史 Top15，比较「挂单等回调」与「追买」的期望收益。

只读评估脚本，不改生产代码。门槛与口径见 REVIEW-2026-11-03 S1/S2：
  · 区间：A ± k*ATR，A = 现价下方、距现价不超过 3*ATR 的最近支撑
  · 成交：区间内次日开盘买；区间外从次日起在 U 挂限价，5 个交易日内触及才成交
  · 未成交按 0 收益（空仓）计入期望，避免只统计「已经回落」的样本
  · 两条腿用同一套止损/目标与退出规则，按交易日做配对比较

⚠️ 本脚本评估的是「等回调」策略。生产最终采用「区间只作风险参考、可买的按现价
买」（方案 A）。目标与准入都锚在信号日收盘价（与生产一致），不再锚区间上轨 U。
可执行口径下等回调跑输、过滤口径接近无差别（见 CHANGELOG 二十九）。

输出 output/entry_zone_eval.json。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time

import numpy as np
import pandas as pd

from run_rec_eval import (
    BUCKET3,
    CATS,
    DB_PATH,
    RATING_ORDER,
    MEMBER_KEYS,
    _calc_rating,
    _winsorize_z,
    load_regime,
    load_stock_syms,
    load_symbol_df,
    weights_for,
)
from sequoia_x.eval.replay_scores import compute_tech_series
from sequoia_x.risk import compute_atr
from sequoia_x.signal.pricing import (
    build_entry_zone,
    compute_risk_reward,
    entry_reference,
    original_stop_price,
    pick_target,
    plan_risk_ok,
    stop_price,
)

TOP_N = 15          # 与生产 DISPLAY_TOP_N 一致
FILL_WINDOW = 5     # 挂单有效期（交易日）
HOLD_DAYS = 20      # 最长持有（交易日）
FIXED_DAYS = 10     # 固定持有对照


def _pct(a, q):
    return float(np.percentile(a, q)) if len(a) else float("nan")


def _levels(df: pd.DataFrame, i: int) -> dict:
    """按第 i 根K线（含）计算当日区间所需的价位。"""
    w = df.iloc[: i + 1]
    close_s = w["close"].astype(float)
    high_s = w["high"].astype(float)
    low_s = w["low"].astype(float)
    price = float(close_s.iloc[-1])
    ma20 = float(close_s.rolling(20).mean().iloc[-1])
    ma5 = float(close_s.rolling(5).mean().iloc[-1])
    std20 = float(close_s.rolling(20).std().iloc[-1])
    ma60 = float(close_s.rolling(60).mean().iloc[-1]) if len(close_s) >= 60 else None
    low10 = float(low_s.tail(10).min())
    low20 = float(low_s.tail(20).min())
    high10 = float(high_s.tail(10).max())
    high20 = float(high_s.tail(20).max())
    atr = compute_atr(high_s, low_s, close_s)

    supports = {
        "布林下轨": ma20 - 2 * std20,
        "MA20": ma20,
        "近10日低点": low10,
        "近20日低点": low20,
    }
    if ma60 is not None:
        supports["MA60"] = ma60

    # 与 generator 共用同一份「原止损」定义
    original_stop = original_stop_price(price, atr, ma20, low10)

    return {
        "price": price, "atr": atr, "ma20": ma20, "ma5": ma5,
        "supports": supports, "resistances": [high10, high20],
        "original_stop": original_stop,
    }


def _simulate_exit(df: pd.DataFrame, fill: int, entry: float,
                   stop: float, target: float, hold: int) -> float | None:
    """从 fill 日起按（止损优先）顺序模拟退出，返回相对成交价的收益。"""
    if fill >= len(df) or entry <= 0:
        return None
    last = min(fill + hold, len(df) - 1)
    for j in range(fill, last + 1):
        o = float(df["open"].iloc[j])
        h = float(df["high"].iloc[j])
        l = float(df["low"].iloc[j])
        if o <= stop:
            return o / entry - 1.0
        if l <= stop:
            return stop / entry - 1.0
        if o >= target:
            return o / entry - 1.0
        if h >= target:
            return target / entry - 1.0
    return float(df["close"].iloc[last]) / entry - 1.0


def _fixed_return(df: pd.DataFrame, fill: int, entry: float, days: int) -> float | None:
    if fill >= len(df) or entry <= 0:
        return None
    j = min(fill + days, len(df) - 1)
    return float(df["close"].iloc[j]) / entry - 1.0


def _simulate_plan(df: pd.DataFrame, i: int, zone, stop: float, target: float) -> dict:
    """返回成交结果与两条腿的收益。"""
    out = {"filled": False, "fill_idx": None, "fill_price": None, "days_to_fill": None}
    if i + 1 >= len(df):
        return out

    if zone.status == "inside":
        f = i + 1
        px = float(df["open"].iloc[f])
        out.update(filled=True, fill_idx=f, fill_price=px, days_to_fill=0)
    elif zone.status == "outside":
        for f in range(i + 1, min(i + 1 + FILL_WINDOW, len(df))):
            o = float(df["open"].iloc[f])
            l = float(df["low"].iloc[f])
            if o <= zone.high:
                out.update(filled=True, fill_idx=f, fill_price=o, days_to_fill=f - i)
                break
            if l <= zone.high:
                out.update(filled=True, fill_idx=f, fill_price=zone.high, days_to_fill=f - i)
                break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-07-01")
    ap.add_argument("--end", default="2026-08-21")
    ap.add_argument("--k-list", default="0.25,0.5,0.75,1.0")
    ap.add_argument("--d-cap", type=float, default=3.0)
    ap.add_argument("--anchor", choices=["support", "ma5"], default="support")
    ap.add_argument("--limit", type=int, default=0, help="仅处理前N只股票（调试）")
    ap.add_argument("--min-stocks", type=int, default=200)
    ap.add_argument("--out", default="output/entry_zone_eval.json")
    args = ap.parse_args()
    ks = [float(x) for x in args.k_list.split(",")]

    reg = load_regime(args.start, args.end)
    eval_dates = sorted(reg)
    if not eval_dates:
        raise SystemExit("区间内没有 regime 数据")
    eval_set = set(eval_dates)
    print(f"评估区间 {eval_dates[0]}~{eval_dates[-1]}（{len(eval_dates)} 天）", flush=True)

    syms = load_stock_syms()
    if args.limit:
        syms = syms[: args.limit]
    print(f"股票池 {len(syms)} 只", flush=True)

    # ── Pass 1：逐日重建 Top15（与 run_rec_eval 的打分口径一致） ──
    t0 = time.time()
    flat: list[dict] = []
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    for idx, sym in enumerate(syms):
        df = load_symbol_df(conn, sym)
        if df.empty or len(df) < 60:
            continue
        tech = compute_tech_series(df)
        keep = (
            df["date"].isin(eval_set).values
            & (tech["bar_idx"].values >= 59)
            & (~tech["gap_excl"].values)
        )
        if not keep.any():
            continue
        rows = df.loc[keep, ["date"]].copy()
        rows["symbol"] = sym
        rows["trend"] = tech.loc[keep, "trend_ok"].astype(int).values
        for k in MEMBER_KEYS:
            rows[k] = tech.loc[keep, f"score_{k}"].astype(float).values
        flat.append(rows)
        if (idx + 1) % 800 == 0:
            print(f"  评分 {idx+1}/{len(syms)} 耗时 {time.time()-t0:.0f}s", flush=True)
    conn.close()
    if not flat:
        raise SystemExit("没有可用的评分数据")
    big = pd.concat(flat, ignore_index=True).sort_values("date").reset_index(drop=True)
    print(f"评分行 {len(big)}，构建耗时 {time.time()-t0:.0f}s", flush=True)

    reg_weights = {label: weights_for(label) for label in set(reg.values())}
    daily_w = {d: reg_weights[reg[d]] for d in eval_dates}

    signals: list[dict] = []
    for date_str, blk in big.groupby("date", sort=False):
        if date_str not in eval_set or len(blk) < args.min_stocks:
            continue
        mat = blk[MEMBER_KEYS].to_numpy(dtype=float)
        z = _winsorize_z(mat)
        w = daily_w[date_str]
        catw, totalw = {}, 0.0
        for cat, members in CATS.items():
            cw = sum(w[m] for m in members)
            if cw > 0:
                catw[cat] = cw
                totalw += cw
        if totalw <= 0:
            totalw, catw = 1.0, {c: 1.0 for c in CATS}
        total = np.zeros(len(blk))
        for cat, cw in catw.items():
            col_idx = [MEMBER_KEYS.index(m) for m in CATS[cat]]
            total += z[:, col_idx].mean(axis=1) * (cw / totalw)
        trend = blk["trend"].to_numpy() > 0
        rating = np.array([_calc_rating(bool(trend[i]), total[i]) for i in range(len(total))])
        order = np.array([RATING_ORDER.get(r, 5) for r in rating])
        sort_v = np.lexsort((-np.round(total, 1), order))
        for rank, i in enumerate(sort_v[:TOP_N], 1):
            signals.append({
                "date": date_str,
                "symbol": blk["symbol"].iloc[i],
                "rank": rank,
                "rating": rating[i],
                "regime": reg[date_str],
            })
    print(f"信号 {len(signals)} 条（{len(eval_dates)} 天 × Top{TOP_N}）", flush=True)

    # ── Pass 2：按 k 模拟 ──
    by_symbol: dict[str, list[dict]] = {}
    for s in signals:
        by_symbol.setdefault(s["symbol"], []).append(s)

    df_cache: dict[str, pd.DataFrame] = {}
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    for sym in by_symbol:
        df_cache[sym] = load_symbol_df(conn, sym)
    conn.close()

    results = {}
    for k in ks:
        recs = []
        t1 = time.time()
        for sym, rows in by_symbol.items():
            df = df_cache[sym]
            date_pos = {d: i for i, d in enumerate(df["date"].tolist())}
            for s in rows:
                i = date_pos.get(s["date"])
                if i is None or i < 60:
                    continue
                lv = _levels(df, i)
                supports = (
                    {"MA5": lv["ma5"]} if args.anchor == "ma5" else lv["supports"]
                )
                zone = build_entry_zone(
                    price=lv["price"], atr=lv["atr"], supports=supports,
                    k=k, distance_cap=args.d_cap,
                )
                rec = {"date": s["date"], "symbol": sym, "regime": s["regime"],
                       "status": zone.status, "reason": zone.reason}
                if zone.status == "none":
                    # 不交易按 0 收益计；同时记录「若照常追买」的同期收益，
                    # 用来回答「空集只观察」这条规则本身划不划算
                    j_cal = i + 1 + FIXED_DAYS
                    if i + 1 < len(df) and j_cal < len(df):
                        chase_cal = (float(df["close"].iloc[j_cal])
                                     / float(df["open"].iloc[i + 1]) - 1.0)
                        rec["delta_cal10"] = -chase_cal
                        rec["chase_cal10"] = chase_cal
                        rec["plan_cal10"] = 0.0
                    rec.update(tradable=False, rr=None)
                    recs.append(rec)
                    continue
                stop = stop_price(zone.low, lv["atr"], lv["original_stop"])
                # 目标与准入锚信号日收盘价（生产 A 口径；等待腿的成交价仍是 U）
                if not plan_risk_ok(lv["price"], stop):
                    j_cal = i + 1 + FIXED_DAYS
                    if i + 1 < len(df) and j_cal < len(df):
                        chase_cal = (float(df["close"].iloc[j_cal])
                                     / float(df["open"].iloc[i + 1]) - 1.0)
                        rec["delta_cal10"] = -chase_cal
                        rec["chase_cal10"] = chase_cal
                        rec["plan_cal10"] = 0.0
                    rec.update(status="none", reason="止损距离不足", tradable=False, rr=None)
                    recs.append(rec)
                    continue
                target = pick_target(lv["resistances"], lv["price"], lv["atr"]).value
                entry = entry_reference(zone, lv["price"])
                rr = compute_risk_reward(entry, stop, target)
                plan = _simulate_plan(df, i, zone, stop, target)
                chase_ret = fixed10 = plan_ret = chase_fixed10 = None
                if plan["filled"]:
                    plan_ret = _simulate_exit(
                        df, plan["fill_idx"], plan["fill_price"], stop, target, HOLD_DAYS)
                    fixed10 = _fixed_return(
                        df, plan["fill_idx"], plan["fill_price"], FIXED_DAYS)
                if i + 1 < len(df):
                    chase_entry = float(df["open"].iloc[i + 1])
                    chase_ret = _simulate_exit(df, i + 1, chase_entry, stop, target, HOLD_DAYS)
                    chase_fixed10 = _fixed_return(df, i + 1, chase_entry, FIXED_DAYS)
                # 同一到期日对照：两条腿都在 T+1+FIXED_DAYS 收盘结算，
                # 消除「等回调导致持有窗口后移」带来的市场漂移偏差
                j_cal = i + 1 + FIXED_DAYS
                cal10 = chase_cal10 = plan_cal10 = None
                if j_cal < len(df):
                    cal_close = float(df["close"].iloc[j_cal])
                    chase_cal = cal_close / float(df["open"].iloc[i + 1]) - 1.0
                    plan_cal = (cal_close / plan["fill_price"] - 1.0) if plan["filled"] else 0.0
                    cal10 = plan_cal - chase_cal
                    chase_cal10 = chase_cal
                    plan_cal10 = plan_cal
                rec.update(
                    tradable=True,
                    filled=plan["filled"],
                    days_to_fill=plan["days_to_fill"],
                    rr=rr.value, rr_capped=rr.capped, rr_reason=rr.reason,
                    plan_ret=plan_ret, fixed10=fixed10,
                    chase_ret=chase_ret, chase_fixed10=chase_fixed10,
                    delta=((plan_ret or 0.0) - chase_ret) if chase_ret is not None else None,
                    delta10=((fixed10 or 0.0) - chase_fixed10) if chase_fixed10 is not None else None,
                    delta_cal10=cal10,
                    chase_cal10=chase_cal10, plan_cal10=plan_cal10,
                )
                recs.append(rec)
        results[k] = recs
        print(f"k={k}: {len(recs)} 条模拟完成（{time.time()-t1:.0f}s）", flush=True)

    # ── 汇总 ──
    def _leg_stats(recs: list[dict]) -> dict:
        pair = [r for r in recs if r.get("delta") is not None]
        pair10 = [r for r in recs if r.get("delta10") is not None]
        filled = [r for r in recs if r.get("filled")]
        daily = {}
        for r in pair:
            daily.setdefault(r["date"], []).append(r["delta"])
        d_means = np.array([np.mean(v) for v in daily.values()]) if daily else np.array([])
        t_stat = (
            float(d_means.mean() / (d_means.std(ddof=1) / np.sqrt(len(d_means))))
            if len(d_means) >= 3 and d_means.std(ddof=1) > 0 else float("nan")
        )
        daily10 = {}
        for r in pair10:
            daily10.setdefault(r["date"], []).append(r["delta10"])
        d10_means = np.array([np.mean(v) for v in daily10.values()]) if daily10 else np.array([])
        t10 = (
            float(d10_means.mean() / (d10_means.std(ddof=1) / np.sqrt(len(d10_means))))
            if len(d10_means) >= 3 and d10_means.std(ddof=1) > 0 else float("nan")
        )
        pair_cal = [r for r in recs if r.get("delta_cal10") is not None]
        daily_cal = {}
        for r in pair_cal:
            daily_cal.setdefault(r["date"], []).append(r["delta_cal10"])
        dcal = np.array([np.mean(v) for v in daily_cal.values()]) if daily_cal else np.array([])
        tcal = (
            float(dcal.mean() / (dcal.std(ddof=1) / np.sqrt(len(dcal))))
            if len(dcal) >= 3 and dcal.std(ddof=1) > 0 else float("nan")
        )
        # 再分配口径：总仓位只分给实际成交的股票，未成交资金不闲置
        by_date_filled, by_date_all = {}, {}
        for r in recs:
            if r.get("chase_cal10") is not None:
                by_date_all.setdefault(r["date"], []).append(r["chase_cal10"])
            if r.get("filled") and r.get("plan_cal10") is not None:
                by_date_filled.setdefault(r["date"], []).append(r["plan_cal10"])
        realloc = []
        fill_counts = []
        for d, allv in by_date_all.items():
            filled_v = by_date_filled.get(d)
            fill_counts.append(len(filled_v) if filled_v else 0)
            cond = float(np.mean(filled_v)) if filled_v else 0.0
            realloc.append(cond - float(np.mean(allv)))
        realloc = np.array(realloc)
        trealloc = (
            float(realloc.mean() / (realloc.std(ddof=1) / np.sqrt(len(realloc))))
            if len(realloc) >= 3 and realloc.std(ddof=1) > 0 else float("nan")
        )
        # 可执行过滤口径：有区间（inside/outside）就按次日开盘买，空集不买；
        # 资金在可买标的间均分。这是实时可执行的，不需要预知谁会成交。
        act_by_date, all_by_date = {}, {}
        for r in recs:
            ch = r.get("chase_cal10")
            if ch is None:
                continue
            all_by_date.setdefault(r["date"], []).append(ch)
            if r["status"] in ("inside", "outside"):
                act_by_date.setdefault(r["date"], []).append(ch)
        filter_d = []
        for d, allv in all_by_date.items():
            av = act_by_date.get(d)
            cond = float(np.mean(av)) if av else 0.0
            filter_d.append(cond - float(np.mean(allv)))
        filter_d = np.array(filter_d)
        tfilter = (
            float(filter_d.mean() / (filter_d.std(ddof=1) / np.sqrt(len(filter_d))))
            if len(filter_d) >= 3 and filter_d.std(ddof=1) > 0 else float("nan")
        )
        return {
            "n": len(recs),
            "fill_rate": len(filled) / len(recs) if recs else float("nan"),
            "cond_plan_ret": float(np.mean([r["plan_ret"] for r in filled])) if filled else float("nan"),
            "expected_plan_ret": float(np.mean([r["plan_ret"] or 0.0 for r in pair])) if pair else float("nan"),
            "chase_ret": float(np.mean([r["chase_ret"] for r in pair])) if pair else float("nan"),
            "delta": float(d_means.mean()) if len(d_means) else float("nan"),
            "delta_t": t_stat,
            "cond_fixed10": float(np.mean([r["fixed10"] for r in filled if r.get("fixed10") is not None]))
            if filled else float("nan"),
            "expected_fixed10": float(np.mean([r["fixed10"] or 0.0 for r in pair10])) if pair10 else float("nan"),
            "chase_fixed10": float(np.mean([r["chase_fixed10"] for r in pair10])) if pair10 else float("nan"),
            "delta10": float(d10_means.mean()) if len(d10_means) else float("nan"),
            "delta10_t": t10,
            "delta_cal10": float(dcal.mean()) if len(dcal) else float("nan"),
            "delta_cal10_t": tcal,
            "policy_ret": float(np.mean([r["plan_cal10"] for r in pair_cal])) if pair_cal else float("nan"),
            "chase_policy_ret": float(np.mean([r["chase_cal10"] for r in pair_cal])) if pair_cal else float("nan"),
            "realloc_delta": float(realloc.mean()) if len(realloc) else float("nan"),
            "realloc_t": trealloc,
            "realloc_median": float(np.median(realloc)) if len(realloc) else float("nan"),
            "realloc_p10": _pct(realloc, 10),
            "realloc_p90": _pct(realloc, 90),
            "fills_per_day_mean": float(np.mean(fill_counts)) if fill_counts else float("nan"),
            "days_no_fill": int(sum(1 for c in fill_counts if c == 0)),
            "filter_delta": float(filter_d.mean()) if len(filter_d) else float("nan"),
            "filter_t": tfilter,
        }

    def summarize(recs: list[dict]) -> dict:
        status, reasons = {}, {}
        for r in recs:
            status[r["status"]] = status.get(r["status"], 0) + 1
            if r.get("reason"):
                reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
        rrs = [r["rr"] for r in recs if r.get("rr") is not None]
        out = {
            "n": len(recs), "status": status, "reasons": reasons,
            "rr_p25": _pct(rrs, 25), "rr_median": _pct(rrs, 50), "rr_p75": _pct(rrs, 75),
            "rr_none_rate": (1 - len(rrs) / len(recs)) if recs else float("nan"),
        }
        out.update(_leg_stats(recs))
        # 区间内可直接买，与追买同价；真正考验「等回调」的是区间外的信号
        outside = [r for r in recs if r["status"] == "outside"]
        inside = [r for r in recs if r["status"] == "inside"]
        out["outside"] = _leg_stats(outside)
        out["inside"] = _leg_stats(inside)
        return out

    report = {"meta": vars(args), "by_k": {}}
    for k, recs in results.items():
        bucket = {}
        for b in ("bull", "range", "bear"):
            sub = [r for r in recs if BUCKET3.get(r["regime"]) == b]
            bucket[b] = summarize(sub) if sub else None
        report["by_k"][str(k)] = {"all": summarize(recs), "by_bucket": bucket}

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # ── 打印 ──
    print("\n" + "=" * 100)
    print("全部信号（区间内与追买同价，会稀释差值；核心看下一张表）")
    print(f"{'k':>5} {'n':>6} {'成交率':>8} {'条件收益':>9} {'期望收益':>9} "
          f"{'追买收益':>9} {'差值':>8} {'t':>7} {'RR中位':>7}")
    for k, r in report["by_k"].items():
        a = r["all"]
        print(f"{k:>5} {a['n']:>6} {a['fill_rate']:>8.1%} "
              f"{a['cond_plan_ret']:>9.2%} {a['expected_plan_ret']:>9.2%} {a['chase_ret']:>9.2%} "
              f"{a['delta']:>8.2%} {a['delta_t']:>7.2f} {a['rr_median']:>7.2f}")
    print("\n区间外信号（真正在考验「等回调」的那些）")
    print(f"{'k':>5} {'n':>6} {'5日内成交':>10} {'条件收益':>9} {'期望收益':>9} "
          f"{'追买收益':>9} {'差值':>8} {'t':>7}")
    for k, r in report["by_k"].items():
        o = r["all"]["outside"]
        print(f"{k:>5} {o['n']:>6} {o['fill_rate']:>10.1%} "
              f"{o['cond_plan_ret']:>9.2%} {o['expected_plan_ret']:>9.2%} {o['chase_ret']:>9.2%} "
              f"{o['delta']:>8.2%} {o['delta_t']:>7.2f}")
    print("\n区间外信号 · 10 日固定持有口径（未成交记 0）")
    print(f"{'k':>5} {'n':>6} {'期望收益':>9} {'追买收益':>9} {'差值':>8} {'t':>7}")
    for k, r in report["by_k"].items():
        o = r["all"]["outside"]
        print(f"{k:>5} {o['n']:>6} {o['expected_fixed10']:>9.2%} {o['chase_fixed10']:>9.2%} "
              f"{o['delta10']:>8.2%} {o['delta10_t']:>7.2f}")
    print("\n区间外信号 · 同一到期日口径（T+11 收盘结算，未成交记 0）")
    print(f"{'k':>5} {'n':>6} {'对齐差值':>9} {'t':>7}")
    for k, r in report["by_k"].items():
        o = r["all"]["outside"]
        print(f"{k:>5} {o['n']:>6} {o['delta_cal10']:>9.2%} {o['delta_cal10_t']:>7.2f}")
    print("\n全名单策略口径（含空集/跌破按空仓，T+11 对齐）")
    print(f"{'k':>5} {'n':>6} {'策略收益':>9} {'全部追买':>9} {'差值':>8} {'t':>7}")
    for k, r in report["by_k"].items():
        a = r["all"]
        print(f"{k:>5} {a['n']:>6} {a['policy_ret']:>9.2%} {a['chase_policy_ret']:>9.2%} "
              f"{a['delta_cal10']:>8.2%} {a['delta_cal10_t']:>7.2f}")
    print("\n全名单 · 再分配口径（资金只分给已成交股票，未成交不占仓位）")
    print(f"{'k':>5} {'均值':>8} {'t':>6} {'中位':>8} {'P10':>8} {'P90':>8} {'日均成交':>8} {'零成交日':>8}")
    for k, r in report["by_k"].items():
        a = r["all"]
        print(f"{k:>5} {a['realloc_delta']:>8.2%} {a['realloc_t']:>6.2f} "
              f"{a['realloc_median']:>8.2%} {a['realloc_p10']:>8.2%} {a['realloc_p90']:>8.2%} "
              f"{a['fills_per_day_mean']:>8.1f} {a['days_no_fill']:>8}")
    print("\n可执行过滤口径（有区间→次日开盘买，空集→空仓；资金在可买标的间均分）")
    print(f"{'k':>5} {'差值':>9} {'t':>7}")
    for k, r in report["by_k"].items():
        a = r["all"]
        print(f"{k:>5} {a['filter_delta']:>9.2%} {a['filter_t']:>7.2f}")
    print("\n区间外信号 · 分市场状态（20日口径差值 / t）")
    print(f"{'k':>5} {'牛市':>20} {'震荡':>20} {'熊市':>20}")
    for k, r in report["by_k"].items():
        cells = []
        for b in ("bull", "range", "bear"):
            s = r["by_bucket"].get(b)
            if not s or not s["outside"]["n"]:
                cells.append(f"{'—':>20}")
                continue
            o = s["outside"]
            cells.append(f"{o['delta']:>+8.2%} t={o['delta_t']:>5.2f} n={o['n']:<4}")
        print(f"{k:>5} " + " ".join(cells))
    print("\n状态分布（none 的原因即「只观察」的构成）：")
    for k, r in report["by_k"].items():
        print(f"  k={k}: {r['all']['status']} | {r['all']['reasons']}")
    print(f"\n结果已写入 {args.out}")


if __name__ == "__main__":
    main()
