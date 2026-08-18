"""策略信号质量回测：IC追踪 + 五分位数分析 + 信号触发收益。

输出 JSON 到 output/strategy_backtest.json（含 IC 分析、五分位数、衰减曲线）。
"""

import json, os, time
from datetime import date, timedelta

os.environ.setdefault("DB_PATH", "data/sequoia_v2.db")

import numpy as np
import pandas as pd

from sequoia_x.core.config import get_settings
from sequoia_x.data.engine import DataEngine

from sequoia_x.rating.strategies import (
    ma_volume_strategy, turtle_trade_strategy, mean_reversion_strategy,
    volume_price_strategy, atr_channel_strategy, macd_rsi_strategy,
    ma_cross_strategy, kdj_strategy,
)

STRATEGIES = [
    ("maVolume", ma_volume_strategy, {}),
    ("turtleTrade", turtle_trade_strategy, {}),
    ("meanReversion", mean_reversion_strategy, {}),
    ("volumePrice", volume_price_strategy, {}),
    ("atrChannel", atr_channel_strategy, {}),
    ("macdRsi", macd_rsi_strategy, {}),
    ("maCross", ma_cross_strategy, {}),
    ("kdj", kdj_strategy, {}),
]

META = {
    "maVolume":      {"label": "MaVolume 均线金叉放量",   "category": "trend"},
    "turtleTrade":   {"label": "TurtleTrade 趋势突破",      "category": "trend"},
    "meanReversion": {"label": "均值回归 超卖反转",         "category": "reversal"},
    "volumePrice":   {"label": "量价关系 确认信号",          "category": "reversal"},
    "atrChannel":    {"label": "ATR通道突破 进攻型",        "category": "trend"},
    "macdRsi":       {"label": "MACD+RSI共振 顺势确认",     "category": "momentum"},
    "maCross":       {"label": "MA20/60金叉死叉 趋势判断",  "category": "momentum"},
    "kdj":           {"label": "KDJ 金叉/超卖",             "category": "reversal"},
}

SCORE_THRESHOLDS = {
    "maVolume": 2.0, "turtleTrade": 2.0, "meanReversion": 1.0,
    "volumePrice": 1.0, "atrChannel": 1.0, "macdRsi": 1.0,
    "maCross": 2.0, "kdj": 1.0,
}


def _rank_ic(scores: list[float], returns: list[float]) -> tuple[float, float]:
    """Spearman 秩相关。优先用 scipy，没有就用纯 numpy 近似（无 p-value）。"""
    try:
        from scipy.stats import spearmanr
        return spearmanr(scores, returns)
    except ImportError:
        pass
    n = len(scores)
    if n < 3:
        return 0.0, 1.0
    x = np.array(scores)
    y = np.array(returns)
    x_rank = np.argsort(np.argsort(x)).astype(float)
    y_rank = np.argsort(np.argsort(y)).astype(float)
    d = x_rank - y_rank
    rho = 1.0 - (6.0 * np.sum(d ** 2)) / (n * (n ** 2 - 1.0))
    return rho, 0.0


def _calc_quantile_returns(scores: list[float], returns: list[float],
                           n_quantiles: int = 5) -> dict:
    """计算五分位数收益（用 rank 打破平局，避免 pd.qcut 边界问题）。"""
    arr = np.column_stack([scores, returns])
    arr = arr[~np.isnan(arr[:, 1])]
    if len(arr) < n_quantiles:
        return {}
    # 按 rank 排序后分五组（用 argsort 替代 qcut，处理并列）
    idx = np.argsort(arr[:, 0])
    arr = arr[idx]
    k = len(arr) // n_quantiles
    quantiles = {}
    for qi in range(n_quantiles):
        start = qi * k
        end = (qi + 1) * k if qi < n_quantiles - 1 else len(arr)
        group = arr[start:end]
        quantiles[f"q{qi+1}"] = float(np.mean(group[:, 1]))
    quantiles["spread"] = quantiles.get("q5", 0) - quantiles.get("q1", 0)
    return quantiles


def _compute_turnover(current_quintiles: dict, prev_quintiles: dict) -> float:
    """计算换手率：与前一期相比，改变五分位数的股票比例。"""
    if not prev_quintiles:
        return 0.0
    common = set(current_quintiles.keys()) & set(prev_quintiles.keys())
    if not common:
        return 1.0
    changed = sum(1 for s in common if current_quintiles[s] != prev_quintiles[s])
    return changed / len(common)


def main():
    settings = get_settings()
    engine = DataEngine(settings)

    all_symbols = engine.get_local_symbols()
    print(f"可用股票: {len(all_symbols)}", flush=True)

    end = date.today().isoformat()
    start = (date.today() - timedelta(days=365)).isoformat()
    print(f"回测区间: {start} ~ {end}", flush=True)

    rng = np.random.default_rng(42)
    sampled = list(rng.choice(all_symbols, size=min(200, len(all_symbols)), replace=False))
    print(f"采样 {len(sampled)} 只股票", flush=True)

    engine.preload_all(sampled)

    first = engine.get_ohlcv(sampled[0])
    all_dates = sorted(first[(first["date"] >= start) & (first["date"] <= end)]["date"].tolist())
    print(f"交易日期: {all_dates[0]} ~ {all_dates[-1]} ({len(all_dates)} 天)", flush=True)

    # ══════════════════════════════════════════
    # 分析 1：信号触发收益（原有的二元阈值法）
    # ══════════════════════════════════════════
    sig_results: dict[str, dict] = {}
    for name, _, _ in STRATEGIES:
        sig_results[name] = {f"ret_{d}d": [] for d in [5, 10, 20]}

    for si, sym in enumerate(sampled):
        if (si + 1) % 50 == 0:
            print(f"  信号分析进度: {si+1}/{len(sampled)}", flush=True)
        df = engine.get_ohlcv(sym)
        if df is None or len(df) < 60:
            continue
        df = df.reset_index(drop=True)
        date_to_idx = {d: i for i, d in enumerate(df["date"].tolist())}

        for current_date in all_dates:
            ci = date_to_idx.get(current_date)
            if ci is None or ci < 30:
                continue
            hist = df.iloc[max(0, ci - 120):ci + 1]

            for name, strat_fn, params in STRATEGIES:
                try:
                    res = strat_fn(hist, **params)
                    if res.score < SCORE_THRESHOLDS.get(name, 1.0):
                        continue
                    for fwd in [5, 10, 20]:
                        fi = ci + fwd
                        if fi < len(df):
                            entry_p = float(df.iloc[ci]["close"])
                            exit_p = float(df.iloc[fi]["close"])
                            sig_results[name][f"ret_{fwd}d"].append((exit_p - entry_p) / entry_p)
                except Exception:
                    continue

    # ══════════════════════════════════════════
    # 分析 2：IC + 五分位数（日期截面分析）
    # ══════════════════════════════════════════
    # 先构建所有股票在各日期的评分矩阵（一次遍历）
    print("  构建评分矩阵...", flush=True)
    # date_str → {symbol → {strategy → score}}
    date_scores: dict[str, dict[str, dict[str, float]]] = {}
    # date_str → {symbol → price_at_date}
    date_prices: dict[str, dict[str, float]] = {}
    for si, sym in enumerate(sampled):
        if (si + 1) % 50 == 0:
            print(f"  评分矩阵进度: {si+1}/{len(sampled)}", flush=True)
        df = engine.get_ohlcv(sym)
        if df is None or len(df) < 60:
            continue
        df = df.reset_index(drop=True)
        date_to_idx = {d: i for i, d in enumerate(df["date"].tolist())}

        for current_date in all_dates:
            ci = date_to_idx.get(current_date)
            if ci is None or ci < 30:
                continue
            hist = df.iloc[max(0, ci - 120):ci + 1]

            # 记录价格
            if current_date not in date_prices:
                date_prices[current_date] = {}
            date_prices[current_date][sym] = float(df.iloc[ci]["close"])

            for name, strat_fn, params in STRATEGIES:
                try:
                    res = strat_fn(hist, **params)
                    if current_date not in date_scores:
                        date_scores[current_date] = {}
                    if sym not in date_scores[current_date]:
                        date_scores[current_date][sym] = {}
                    date_scores[current_date][sym][name] = res.score
                except Exception:
                    continue

    print("  评分矩阵完成", flush=True)

    # 计算 IC
    ic_data: dict[str, dict] = {}
    for name, _, _ in STRATEGIES:
        ic_data[name] = {"ic_series": [], "p_series": [], "quantile_series": [],
                         "sample_sizes": [], "turnover_series": [],
                         "decay_ic": {d: [] for d in [1, 5, 10, 20]}}

    print("  计算 IC/五分位数...", flush=True)
    prev_quintiles: dict[str, dict] = {}
    date_list_sorted = sorted(date_scores.keys())

    for di, current_date in enumerate(date_list_sorted):
        if di >= len(date_list_sorted) - 20:
            continue

        scores_xs: dict[str, dict[str, float]] = {s[0]: {} for s in STRATEGIES}
        prices_today = date_prices.get(current_date, {})

        for sym, sym_scores in date_scores[current_date].items():
            for name, _, _ in STRATEGIES:
                score = sym_scores.get(name)
                if score is not None:
                    scores_xs[name][sym] = score

        # 计算 N 日收益率
        returns_1: dict[str, float] = {}
        returns_5: dict[str, float] = {}
        returns_10: dict[str, float] = {}
        returns_20: dict[str, float] = {}
        for offset, ret_dict in [(1, returns_1), (5, returns_5),
                                  (10, returns_10), (20, returns_20)]:
            future_date = date_list_sorted[di + offset] if di + offset < len(date_list_sorted) else None
            if future_date:
                prices_future = date_prices.get(future_date, {})
                for sym, price_t in prices_today.items():
                    price_f = prices_future.get(sym)
                    if price_t and price_f and price_t > 0:
                        ret_dict[sym] = (price_f - price_t) / price_t

        # 对每个策略计算 IC 和五分位数
        current_quintiles: dict[str, dict] = {}
        for name, _, _ in STRATEGIES:
            score_dict = scores_xs[name]
            ret_dict = returns_20
            common = [s for s in score_dict if s in ret_dict]
            if len(common) < 10:
                continue

            score_list = [score_dict[s] for s in common]
            ret_list = [ret_dict[s] for s in common]

            ic, p_val = _rank_ic(score_list, ret_list)
            ic_data[name]["ic_series"].append(ic)
            ic_data[name]["p_series"].append(p_val)
            ic_data[name]["sample_sizes"].append(len(common))

            # 五分位数
            qret = _calc_quantile_returns(score_list, ret_list, 5)
            ic_data[name]["quantile_series"].append(qret)

            # 计算换手率（基于五分位数归属）
            sorted_syms = [s for s in common]
            sorted_scores = [score_dict[s] for s in common]
            # 用 rank(method='first') 打破评分平局，避免 pd.qcut 的 duplicate edges 问题
            score_arr = np.array(sorted_scores)
            rank_arr = pd.Series(score_arr).rank(method='first').values
            qcut = pd.qcut(rank_arr, 5, labels=range(5))
            if qcut is not None and len(qcut) == len(sorted_syms):
                current_quintiles[name] = {sorted_syms[i]: int(qcut[i])
                                            for i in range(len(sorted_syms))}
                if name in prev_quintiles:
                    to = _compute_turnover(current_quintiles[name], prev_quintiles[name])
                    ic_data[name]["turnover_series"].append(to)

            # 衰减曲线：不同 forward 天的 IC
            for fwd, ret_dict in [(1, returns_1), (5, returns_5),
                                   (10, returns_10), (20, returns_20)]:
                fwd_common = [s for s in score_dict if s in ret_dict]
                if len(fwd_common) >= 10:
                    ic_fwd, _ = _rank_ic(
                        [score_dict[s] for s in fwd_common],
                        [ret_dict[s] for s in fwd_common],
                    )
                    ic_data[name]["decay_ic"][fwd].append(ic_fwd)

        prev_quintiles = current_quintiles

    # ══════════════════════════════════════════
    # 聚合结果
    # ══════════════════════════════════════════
    summary = {}
    for name, _, _ in STRATEGIES:
        stats = {}

        # ── 原有信号分析 ──
        sd = sig_results[name]
        n_signals = len(sd["ret_20d"])
        stats["signal_count"] = n_signals
        if n_signals >= 3:
            stats["insufficient_data"] = False
            for period in [5, 10, 20]:
                arr = np.array(sd[f"ret_{period}d"])
                mean_ret = float(np.mean(arr))
                win_rate = float(np.sum(arr > 0) / len(arr))
                std_ret = float(np.std(arr, ddof=1))
                sharpe = mean_ret / std_ret * np.sqrt(250 / period) if std_ret > 0 else 0.0
                stats[f"return_{period}d"] = round(mean_ret * 100, 2)
                stats[f"win_rate_{period}d"] = round(win_rate * 100, 1)
                stats[f"sharpe_{period}d"] = round(sharpe, 3)
        else:
            stats["insufficient_data"] = True

        # ── IC 分析 ──
        icd = ic_data[name]
        ic_series = icd["ic_series"]
        if ic_series:
            stats["ic_analysis"] = {
                "mean_ic": round(float(np.mean(ic_series)), 4),
                "ic_std": round(float(np.std(ic_series)), 4),
                "ic_ir": round(float(np.mean(ic_series)) / max(float(np.std(ic_series)), 1e-6), 4),
                "ic_positive_pct": round(float(np.sum(np.array(ic_series) > 0)) / len(ic_series) * 100, 1),
                "ic_samples": len(ic_series),
            }
            # 衰减曲线
            decay = {}
            for fwd in [1, 5, 10, 20]:
                fwd_ics = icd["decay_ic"][fwd]
                if fwd_ics:
                    decay[f"{fwd}d"] = round(float(np.mean(fwd_ics)), 4)
            stats["ic_analysis"]["decay_curve"] = decay

            # 五分位数平均收益
            q_series = icd["quantile_series"]
            if q_series:
                q_avg = {}
                for qk in ["q1", "q2", "q3", "q4", "q5", "spread"]:
                    vals = [q.get(qk, 0) for q in q_series]
                    q_avg[qk] = round(float(np.mean(vals)) * 100, 2)
                stats["ic_analysis"]["avg_quantile_returns"] = q_avg

                # 单调性得分：检查 q1→q5 是否严格单调递增（正向因子）
                spread_vals = [q.get("spread", 0) for q in q_series]
                pos_ratio = sum(1 for v in spread_vals if v > 0) / len(spread_vals) if spread_vals else 0
                # 统计相邻组顺序正确的比例
                mono_ok = 0
                mono_total = 0
                for q in q_series:
                    qs = [q.get(f"q{i}", 0) for i in range(1, 6)]
                    for i in range(4):
                        if qs[i] != qs[i+1]:
                            mono_ok += 1 if qs[i] < qs[i+1] else 0
                            mono_total += 1
                mono_score = mono_ok / mono_total if mono_total > 0 else 0.0
                # 多空组合夏普比率（基于 spread 时间序列）
                spread_arr = np.array(spread_vals)
                spread_sharpe = (
                    float(np.mean(spread_arr) / np.std(spread_arr, ddof=1)) * np.sqrt(250 / 20)
                    if np.std(spread_arr, ddof=1) > 1e-10 else 0.0
                )

                stats["ic_analysis"]["monotonicity_score"] = round(mono_score, 4)
                stats["ic_analysis"]["spread_positive_pct"] = round(pos_ratio * 100, 1)
                stats["ic_analysis"]["long_short_sharpe"] = round(spread_sharpe, 4)

            # 平均换手率
            to_series = icd["turnover_series"]
            if to_series:
                stats["ic_analysis"]["avg_turnover"] = round(float(np.mean(to_series)) * 100, 1)

        summary[name] = stats

        # 打印
        sig_str = (f"  信号{n_signals:>4}次  5日{stats.get('return_5d',0):>6.2f}%  "
                   f"20日{stats.get('return_20d',0):>6.2f}%") if n_signals >= 3 else "  信号不足"
        ic_str = ""
        if "ic_analysis" in stats:
            ica = stats["ic_analysis"]
            ic_str = f"  IC={ica.get('mean_ic',0):+.4f}  IR={ica.get('ic_ir',0):.2f}  "
            ic_str += f"单调性={ica.get('monotonicity_score',0):.2f}  "
            ic_str += f"多空Sharpe={ica.get('long_short_sharpe',0):.2f}  "
            ic_str += f"价差={ica.get('avg_quantile_returns',{}).get('spread',0):+.2f}%"
        print(f"  {name:<15}{sig_str}{ic_str}", flush=True)

    # ── 综合健康度评分（基于排名百分位） ──
    print("  计算综合健康度评分...", flush=True)
    metrics_pool = ["mean_ic", "ic_ir", "monotonicity_score",
                    "long_short_sharpe", "avg_turnover"]
    # 收集数据
    rank_data: dict[str, dict[str, float]] = {}
    for name, _, _ in STRATEGIES:
        ica = summary.get(name, {}).get("ic_analysis", {})
        if ica:
            rank_data[name] = {
                "mean_ic": ica.get("mean_ic", 0),
                "ic_ir": ica.get("ic_ir", 0),
                "monotonicity_score": ica.get("monotonicity_score", 0),
                "long_short_sharpe": ica.get("long_short_sharpe", 0),
                "avg_turnover": ica.get("avg_turnover", 100),
            }
    if rank_data:
        names = list(rank_data.keys())
        n = len(names)
        for metric in metrics_pool:
            reverse_sort = metric != "avg_turnover"
            sorted_names = sorted(names,
                                  key=lambda nm: rank_data[nm].get(metric, 0),
                                  reverse=reverse_sort)
            for rank, nm in enumerate(sorted_names):
                rank_pct = (n - rank) / n if n > 0 else 0
                ica = summary[nm].setdefault("ic_analysis", {})
                ica[f"rank_{metric}"] = round(rank_pct, 4)

        # 综合健康度 = 各指标排名百分位的加权平均
        health_weights = {
            "rank_mean_ic": 0.25,
            "rank_ic_ir": 0.25,
            "rank_monotonicity_score": 0.20,
            "rank_long_short_sharpe": 0.20,
            "rank_avg_turnover": 0.10,
        }
        for name in names:
            ica = summary[name].get("ic_analysis", {})
            health = sum(
                ica.get(metric, 0) * weight
                for metric, weight in health_weights.items()
            )
            summary[name]["health_score"] = round(health, 4)

        # 按健康度排序并打印
        sorted_by_health = sorted(
            [(name, summary[name].get("health_score", 0)) for name in names],
            key=lambda x: -x[1],
        )
        print(f"\n  ── 策略综合健康度排名 ──")
        for rank, (name, score) in enumerate(sorted_by_health, 1):
            stars = "█" * max(1, int(score * 10))
            print(f"  {rank}. {name:<15} {score:.3f} {stars}")

    output = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config": {"sample_size": len(sampled), "date_range": f"{start}~{end}"},
        "strategies": summary,
        "meta": META,
    }

    out_path = "output/strategy_backtest.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
