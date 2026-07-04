"""策略信号质量回测：8个评级策略触发后的 N 日收益率统计。

使用纯函数策略（无需数据库查询），速度快。
输出 JSON 到 output/strategy_backtest.json，供 COS 上传和手机端消费。
"""

import json, os, time
from datetime import date, timedelta

os.environ.setdefault("DB_PATH", "data/sequoia_v2.db")

import numpy as np
import pandas as pd

from sequoia_x.core.config import get_settings
from sequoia_x.data.engine import DataEngine

from sequoia_x.rating.strategies import (
    ma_volume_strategy,
    turtle_trade_strategy,
    mean_reversion_strategy,
    volume_price_strategy,
    atr_channel_strategy,
    macd_rsi_strategy,
    ma_cross_strategy,
    kdj_strategy,
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

# 各策略正信号阈值（score >= 该值视为买入信号，来自实际评分分布观察）
SCORE_THRESHOLDS = {
    "maVolume": 2.0, "turtleTrade": 2.0, "meanReversion": 1.0,
    "volumePrice": 1.0, "atrChannel": 1.0, "macdRsi": 1.0,
    "maCross": 2.0, "kdj": 1.0,
}


def main():
    settings = get_settings()
    engine = DataEngine(settings)

    all_symbols = engine.get_local_symbols()
    print(f"可用股票: {len(all_symbols)}", flush=True)

    end = date.today().isoformat()
    start = (date.today() - timedelta(days=365)).isoformat()
    print(f"回测区间: {start} ~ {end}", flush=True)

    # 采样 200 只（8策略×200只×200天 ≈ 32万次调用，约5-10分钟）
    rng = np.random.default_rng(42)
    sampled = list(rng.choice(all_symbols, size=min(200, len(all_symbols)), replace=False))
    print(f"采样 {len(sampled)} 只股票", flush=True)

    engine.preload_all(sampled)

    # 获取交易日列表
    first = engine.get_ohlcv(sampled[0])
    all_dates = sorted(first[(first["date"] >= start) & (first["date"] <= end)]["date"].tolist())
    print(f"交易日期: {all_dates[0]} ~ {all_dates[-1]} ({len(all_dates)} 天)", flush=True)

    # 结果收集
    results: dict[str, dict] = {}
    for name, _, _ in STRATEGIES:
        results[name] = {f"ret_{d}d": [] for d in [5, 10, 20]}

    # 遍历股票
    for si, sym in enumerate(sampled):
        if (si + 1) % 50 == 0:
            print(f"  进度: {si+1}/{len(sampled)}", flush=True)

        df = engine.get_ohlcv(sym)
        if df is None or len(df) < 60:
            continue
        df = df.reset_index(drop=True)
        date_to_idx = {d: i for i, d in enumerate(df["date"].tolist())}

        # 对每个日期
        for current_date in all_dates:
            ci = date_to_idx.get(current_date)
            if ci is None or ci < 30:
                continue

            # 只传最近 120 根 K 线（大部分策略只需 20-60 根）
            hist = df.iloc[max(0, ci - 120):ci + 1]

            for name, strat_fn, params in STRATEGIES:
                try:
                    res = strat_fn(hist, **params)
                    threshold = SCORE_THRESHOLDS.get(name, 1.0)
                    if res.score < threshold:
                        continue

                    # 记录 N 日收益
                    for fwd in [5, 10, 20]:
                        fi = ci + fwd
                        if fi < len(df):
                            entry = float(df.iloc[ci]["close"])
                            exit_p = float(df.iloc[fi]["close"])
                            results[name][f"ret_{fwd}d"].append((exit_p - entry) / entry)
                except Exception:
                    continue

    # 聚合统计
    summary = {}
    for name, _, _ in STRATEGIES:
        data = results[name]
        n = len(data["ret_20d"])
        stats = {"signal_count": n}
        if n < 3:
            stats["insufficient_data"] = True
            summary[name] = stats
            continue

        stats["insufficient_data"] = False
        for period in [5, 10, 20]:
            arr = np.array(data[f"ret_{period}d"])
            mean_ret = float(np.mean(arr))
            win_rate = float(np.sum(arr > 0) / len(arr))
            std_ret = float(np.std(arr, ddof=1))
            sharpe = mean_ret / std_ret * np.sqrt(250 / period) if std_ret > 0 else 0.0

            stats[f"return_{period}d"] = round(mean_ret * 100, 2)
            stats[f"win_rate_{period}d"] = round(win_rate * 100, 1)
            stats[f"sharpe_{period}d"] = round(sharpe, 3)

        summary[name] = stats
        print(f"  {name:<15} 信号{n:>4}次  5日{stats['return_5d']:>6.2f}%  10日{stats['return_10d']:>6.2f}%  20日{stats['return_20d']:>6.2f}%  胜率{stats['win_rate_20d']}%")

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
