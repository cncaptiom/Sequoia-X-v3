"""策略参数网格搜索：对每个策略的最优参数进行搜索。

用法:
    python run_grid_search.py                    # 搜索所有策略
    python run_grid_search.py --strategy maVolume # 搜索指定策略

输出 JSON 到 output/grid_search_results.json（含最优参数和评分）。
"""

import argparse
import itertools
import json
import os
import time
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

# ── 参数网格定义 ──

PARAM_GRIDS = {
    "maVolume": {
        "ma_short": [3, 5, 10, 15],
        "ma_long": [10, 20, 30, 60],
        "volume_threshold": [1.2, 1.5, 2.0],
    },
    "turtleTrade": {
        "break_period": [10, 20, 30, 40],
        "new_high_period": [40, 60, 90],
    },
    "meanReversion": {
        "rsi_period": [10, 14, 20],
        "rsi_oversold": [20, 25, 30],
        "rsi_overbought": [70, 75, 80],
    },
    "volumePrice": {
        "volume_threshold": [1.3, 1.5, 2.0],
        "volume_surge_threshold": [2.5, 3.0, 4.0],
    },
    "atrChannel": {
        "atr_period": [20, 30, 40],
        "channel_window": [15, 20, 30],
    },
    "macdRsi": {},  # 无参数
    "maCross": {},  # 无参数
    "kdj": {
        "k_period": [5, 9, 14],
        "d_period": [2, 3, 5],
    },
}

STRATEGY_MAP = {
    "maVolume": ma_volume_strategy,
    "turtleTrade": turtle_trade_strategy,
    "meanReversion": mean_reversion_strategy,
    "volumePrice": volume_price_strategy,
    "atrChannel": atr_channel_strategy,
    "macdRsi": macd_rsi_strategy,
    "maCross": ma_cross_strategy,
    "kdj": kdj_strategy,
}

# 各策略的信号触发阈值（原始分值）
SCORE_THRESHOLDS = {
    "maVolume": 2.0, "turtleTrade": 2.0, "meanReversion": 1.0,
    "volumePrice": 1.0, "atrChannel": 1.0, "macdRsi": 1.0,
    "maCross": 2.0, "kdj": 1.0,
}


def _eval_params(strategy_fn, df, params: dict, threshold: float) -> dict:
    """用给定参数运行策略，返回信号评估指标。"""
    try:
        # 滑动窗口扫描信号
        rets_5d, rets_10d, rets_20d = [], [], []
        date_to_idx = {d: i for i, d in enumerate(df["date"].tolist())}
        all_dates = df["date"].tolist()

        for current_date in all_dates:
            ci = date_to_idx[current_date]
            if ci < 30:
                continue
            hist = df.iloc[max(0, ci - 120):ci + 1]
            res = strategy_fn(hist, **params)
            if res.score < threshold:
                continue

            for fwd, ret_list in [(5, rets_5d), (10, rets_10d), (20, rets_20d)]:
                fi = ci + fwd
                if fi < len(df):
                    entry_p = float(df.iloc[ci]["close"])
                    exit_p = float(df.iloc[fi]["close"])
                    if entry_p > 0:
                        ret_list.append((exit_p - entry_p) / entry_p)

        n_signal = len(rets_20d)
        result = {"signal_count": n_signal}

        for period, rets in [(5, rets_5d), (10, rets_10d), (20, rets_20d)]:
            if len(rets) < 3:
                result[f"return_{period}d"] = 0.0
                result[f"win_rate_{period}d"] = 0.0
                result[f"sharpe_{period}d"] = 0.0
            else:
                arr = np.array(rets)
                avg_ret = float(np.mean(arr))
                win = float(np.sum(arr > 0) / len(arr))
                std_ret = float(np.std(arr, ddof=1))
                sharpe = avg_ret / std_ret * np.sqrt(250 / period) if std_ret > 0 else 0.0
                result[f"return_{period}d"] = round(avg_ret * 100, 2)
                result[f"win_rate_{period}d"] = round(win * 100, 1)
                result[f"sharpe_{period}d"] = round(sharpe, 3)

        return result
    except Exception as exc:
        return {"signal_count": 0, "error": str(exc)}


def _composite_score(result: dict) -> float:
    """计算综合评分：20日收益率 * 胜率 * 信号数^(1/3) * (Sharpe+1)。"""
    if result.get("signal_count", 0) < 3:
        return -9999
    ret = result.get("return_20d", 0)
    win = result.get("win_rate_20d", 0) / 100.0
    sharpe = result.get("sharpe_20d", 0)
    n = result.get("signal_count", 0)

    # 收益率要正，胜率要高，Sharpe 要大，信号要够
    if ret <= 0:
        return -9998
    score = ret * win * (sharpe + 1) * np.log(n + 1)
    return score


META = {
    "maVolume": {"label": "MaVolume 均线金叉放量"},
    "turtleTrade": {"label": "TurtleTrade 趋势突破"},
    "meanReversion": {"label": "均值回归 超卖反转"},
    "volumePrice": {"label": "量价关系 确认信号"},
    "atrChannel": {"label": "ATR通道突破 进攻型"},
    "macdRsi": {"label": "MACD+RSI共振 顺势确认"},
    "maCross": {"label": "MA20/60金叉死叉 趋势判断"},
    "kdj": {"label": "KDJ 金叉/超卖"},
}


def main():
    parser = argparse.ArgumentParser(description="策略参数网格搜索")
    parser.add_argument("--strategy", type=str, default=None,
                        help="搜索指定策略（默认搜索所有）")
    parser.add_argument("--sample", type=int, default=100,
                        help="采样股票数（默认100）")
    args = parser.parse_args()

    settings = get_settings()
    engine = DataEngine(settings)

    all_symbols = engine.get_local_symbols()
    print(f"可用股票: {len(all_symbols)}")

    rng = np.random.default_rng(42)
    sampled = list(rng.choice(all_symbols, size=min(args.sample, len(all_symbols)),
                              replace=False))
    print(f"采样 {len(sampled)} 只股票", flush=True)

    # 只用最近 6 个月数据加速
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=180)).isoformat()
    print(f"数据区间: {start} ~ {end}", flush=True)

    engine.preload_all(sampled)

    # 确定要搜索的策略
    strategies_to_search = [args.strategy] if args.strategy else list(PARAM_GRIDS.keys())

    all_results: dict[str, list] = {}

    for name in strategies_to_search:
        grid = PARAM_GRIDS.get(name, {})
        strategy_fn = STRATEGY_MAP[name]
        threshold = SCORE_THRESHOLDS.get(name, 1.0)

        # 生成参数组合
        if not grid:
            print(f"\n  {name}: 无参数（固定策略）")
            continue

        keys = list(grid.keys())
        values = list(grid.values())
        combos = list(itertools.product(*values))
        print(f"\n  {name}: {len(combos)} 种参数组合", flush=True)

        # 对每种组合，逐只股票评估
        combo_results = []
        for ci, combo in enumerate(combos):
            params = dict(zip(keys, combo))
            all_ret_20d = []

            for si, sym in enumerate(sampled):
                df = engine.get_ohlcv(sym)
                if df is None or len(df) < 60:
                    continue
                df = df.reset_index(drop=True)
                df = df[df["date"] >= start].reset_index(drop=True)

                if ci == 0 and si == 0:
                    n_dates = len(df)
                    total_calcs = len(sampled) * len(combos)
                    print(f"    每只股票 ~{n_dates} 个交易日，总共 ~{total_calcs} 次计算", flush=True)

                result = _eval_params(strategy_fn, df, params, threshold)
                if result["signal_count"] >= 3:
                    all_ret_20d.append(result)

            # 聚合
            if all_ret_20d:
                avg_ret = float(np.mean([r["return_20d"] for r in all_ret_20d]))
                avg_win = float(np.mean([r["win_rate_20d"] for r in all_ret_20d]))
                avg_sharpe = float(np.mean([r["sharpe_20d"] for r in all_ret_20d]))
                total_signals = sum(r["signal_count"] for r in all_ret_20d)
                n_stocks_with_signal = sum(1 for r in all_ret_20d if r["signal_count"] >= 3)
            else:
                avg_ret = 0
                avg_win = 0
                avg_sharpe = 0
                total_signals = 0
                n_stocks_with_signal = 0

            result_entry = {
                "params": params,
                "avg_return_20d": round(avg_ret, 2),
                "avg_win_rate": round(avg_win, 1),
                "avg_sharpe_20d": round(avg_sharpe, 3),
                "total_signals": total_signals,
                "stocks_with_signal": n_stocks_with_signal,
            }
            result_entry["composite_score"] = round(
                _composite_score(result_entry), 2
            )
            combo_results.append(result_entry)

            if (ci + 1) % 10 == 0 or ci == len(combos) - 1:
                print(f"    进度: {ci+1}/{len(combos)}", flush=True)

        # 排序并保留 top 结果
        combo_results.sort(key=lambda x: -x["composite_score"])
        all_results[name] = {
            "top_combos": combo_results[:10],
            "total_combos": len(combos),
            "best_params": combo_results[0]["params"] if combo_results else {},
            "best_score": combo_results[0]["composite_score"] if combo_results else -9999,
            "default_score": None,
        }

        # 输出
        print(f"  ── {name} 最优参数 ──")
        for rank, r in enumerate(combo_results[:5], 1):
            print(f"    {rank}. {r['params']}  "
                  f"收益={r['avg_return_20d']:+.2f}%  "
                  f"胜率={r['avg_win_rate']:.1f}%  "
                  f"Sharpe={r['avg_sharpe_20d']:.3f}  "
                  f"信号={r['total_signals']}  "
                  f"评分={r['composite_score']:.2f}")

    # 保存
    output = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config": {
            "sample_size": len(sampled),
            "date_range": f"{start}~{end}",
        },
        "results": all_results,
        "meta": META,
    }

    out_path = "output/grid_search_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
