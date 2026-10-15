"""多策略共识回测：运行全部6策略，用ConsensusEngine融合评分，只交易高分股票。

对比单策略 vs 多策略共识的表现。
"""
import os, sys, time, json
os.environ.setdefault('DB_PATH', 'data/sequoia_v2.db')

import sqlite3, pandas as pd, numpy as np
from datetime import date, timedelta

# ---- 工具函数 ----

def get_dates(engine, start, end):
    """获取交易日列表。"""
    symbols = engine.get_local_symbols()
    df = engine.get_ohlcv(symbols[0])
    dates = sorted(df[(df["date"] >= start) & (df["date"] <= end)]["date"].tolist())
    return dates

def run_strategies(strategies, data_map, current_date):
    """运行所有策略，返回 {strategy_name: [selected_symbols]}。"""
    results = {}
    for name, strategy in strategies:
        strategy.reference_date = current_date
        try:
            selected = strategy.run()
            results[name] = selected if isinstance(selected, list) else []
        except Exception as e:
            results[name] = []
    return results

def consensus_score(strategy_results):
    """对策略结果进行共识评分（与ConsensusEngine逻辑一致）。"""
    # 统计每只股票被多少策略选中
    symbol_counts = {}
    symbol_strategies = {}
    for strat_name, symbols in strategy_results.items():
        for sym in set(symbols):
            symbol_counts[sym] = symbol_counts.get(sym, 0) + 1
            if sym not in symbol_strategies:
                symbol_strategies[sym] = []
            symbol_strategies[sym].append(strat_name)

    # 计算共识分：策略数量*15 + 多样性加分
    scored = []
    for sym, count in symbol_counts.items():
        base = min(count, 5) * 15
        strategies = symbol_strategies.get(sym, [])
        # 战术分类
        trend_strats = {"MaVolumeStrategy", "TurtleTradeStrategy", "RpsBreakoutStrategy"}
        reversal_strats = {"HighTightFlagStrategy", "LimitUpShakeoutStrategy", "UptrendLimitDownStrategy"}
        has_trend = any(s in strategies for s in trend_strats)
        has_reversal = any(s in strategies for s in reversal_strats)
        diversity = 15 if (has_trend and has_reversal) else 5 if (has_trend or has_reversal) else 0
        final = min(base + diversity, 100)
        scored.append((sym, count, final))

    # 按分数降序排列
    scored.sort(key=lambda x: (-x[2], -x[1]))
    return scored  # [(symbol, strategy_count, score), ...]

def multi_strategy_backtest():
    from sequoia_x.core.config import get_settings
    from sequoia_x.data.engine import DataEngine
    from sequoia_x.strategy.ma_volume import MaVolumeStrategy
    from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy
    from sequoia_x.strategy.high_tight_flag import HighTightFlagStrategy
    from sequoia_x.strategy.limit_up_shakeout import LimitUpShakeoutStrategy
    from sequoia_x.strategy.uptrend_limit_down import UptrendLimitDownStrategy
    from sequoia_x.strategy.rps_breakout import RpsBreakoutStrategy

    settings = get_settings()
    engine = DataEngine(settings)

    # 获取股票列表和交易日
    symbols = engine.get_local_symbols()[:200]
    all_dates = get_dates(engine, "2026-04-01", "2026-06-01")
    print(f"回测: {len(symbols)}只, {len(all_dates)}个交易日")

    # 预加载数据
    engine.preload_all(symbols)

    # 创建策略实例
    strategies = [
        ("MaVolume", MaVolumeStrategy(engine, settings)),
        ("TurtleTrade", TurtleTradeStrategy(engine, settings)),
        ("HighTightFlag", HighTightFlagStrategy(engine, settings)),
        ("LimitUpShakeout", LimitUpShakeoutStrategy(engine, settings)),
        ("UptrendLimitDown", UptrendLimitDownStrategy(engine, settings)),
        ("RpsBreakout", RpsBreakoutStrategy(engine, settings)),
    ]
    for _, s in strategies:
        s.preload(engine._cache)

    # 回测参数
    capital = 1_000_000
    max_positions = 10
    commission = 0.0003
    stamp_tax = 0.0005   # 卖出印花税（2023-08 起 0.05%）——此前整个脚本漏了这一项
    slippage = 0.001
    stop_loss = -0.08
    take_profit = 0.20
    max_hold = 20

    trades = []
    positions = {}  # {symbol: {entry_date, entry_price, quantity}}
    equity_curve = []
    min_bars = 30

    # 测试3种共识阈值
    configs = [
        ("多策略共识-策略数>=2", lambda s: s[1] >= 2),
        ("多策略共识-策略数>=3", lambda s: s[1] >= 3),
        ("多策略共识-分数>=45", lambda s: s[2] >= 45),
    ]

    for config_name, entry_filter in configs:
        capital = 1_000_000
        positions = {}
        trades = []
        equity_curve = []

        for i in range(min_bars, len(all_dates)):
            current_date = all_dates[i]

            # 运行全部策略（reference_date 由策略基类 get_data() 自动切片）
            results = run_strategies(strategies, engine._cache, current_date)

            # 共识评分
            scored = consensus_score(results)
            selected = [sym for sym, cnt, score in scored if entry_filter((sym, cnt, score))]

            # 退出现有持仓
            for sym in list(positions.keys()):
                pos = positions[sym]
                df_sym = engine._cache.get(sym)
                if df_sym is None: continue
                df_cut = df_sym[df_sym["date"] <= current_date]
                if df_cut.empty: continue
                price = float(df_cut["close"].iloc[-1])
                pnl_pct = (price - pos["entry_price"]) / pos["entry_price"]
                hold = (pd.Timestamp(current_date) - pd.Timestamp(pos["entry_date"])).days

                reason = None
                if pnl_pct <= stop_loss: reason = "stop_loss"
                elif pnl_pct >= take_profit: reason = "take_profit"
                elif hold >= max_hold: reason = "time_exit"

                if reason:
                    ex_price = price * (1 - slippage)
                    pnl = (ex_price - pos["entry_price"]) * pos["quantity"]
                    pnl -= pos["entry_price"] * pos["quantity"] * commission
                    pnl -= ex_price * pos["quantity"] * commission
                    pnl -= ex_price * pos["quantity"] * stamp_tax
                    trades.append({"symbol": sym, "entry": pos["entry_date"], "exit": current_date,
                                   "entry_price": pos["entry_price"], "exit_price": ex_price,
                                   "quantity": pos["quantity"], "pnl": round(pnl, 2),
                                   "pnl_pct": round(pnl_pct * 100, 2), "reason": reason})
                    capital += pnl
                    del positions[sym]

            # 开新仓
            available = max_positions - len(positions)
            for sym in selected[:available]:
                if sym in positions: continue
                df_sym = engine._cache.get(sym)
                if df_sym is None: continue
                df_cut = df_sym[df_sym["date"] <= current_date]
                if df_cut.empty: continue
                price = float(df_cut["close"].iloc[-1]) * (1 + slippage)
                pos_val = capital * 0.10
                qty = max(int(pos_val / price / 100) * 100, 100)
                cost = price * qty * (1 + commission)
                if cost > 0:
                    positions[sym] = {"entry_date": current_date, "entry_price": price, "quantity": qty}

            # 记录权益
            mv = 0.0
            for sym, pos in positions.items():
                df_sym = engine._cache.get(sym)
                if df_sym is None: continue
                df_cut = df_sym[df_sym["date"] <= current_date]
                if not df_cut.empty:
                    mv += float(df_cut["close"].iloc[-1]) * pos["quantity"]
            equity_curve.append({"date": current_date, "equity": capital + mv})

        # 计算结果
        df_eq = pd.DataFrame(equity_curve)
        total_ret = (df_eq["equity"].iloc[-1] - df_eq["equity"].iloc[0]) / df_eq["equity"].iloc[0] if len(df_eq) > 1 else 0
        wins = sum(1 for t in trades if t["pnl"] > 0)
        losses = sum(1 for t in trades if t["pnl"] < 0)
        wr = wins / len(trades) if trades else 0
        print(f"\n{config_name}:")
        print(f"  交易: {len(trades)}笔 | 胜率: {wr:.1%} | 收益: {total_ret:.1%} | 资金: {df_eq['equity'].iloc[-1]:,.0f}")
        if trades:
            print(f"  盈利: {wins}笔 | 亏损: {losses}笔 | 平均盈利: {np.mean([t['pnl'] for t in trades if t['pnl']>0] or [0]):.0f} | 平均亏损: {np.mean([t['pnl'] for t in trades if t['pnl']<0] or [0]):.0f}")

    # 对照：单策略MaVolume
    print("\n--- 对照：单策略 ---")
    for name, strategy in strategies[:1]:  # 只跑 MaVolume
        capital = 1_000_000
        positions = {}
        trades = []
        equity_curve = []
        for i in range(min_bars, len(all_dates)):
            current_date = all_dates[i]
            strategy.reference_date = current_date

            selected = strategy.run() or []

            for sym in list(positions.keys()):
                pos = positions[sym]
                df_sym = engine._cache.get(sym)
                if df_sym is None: continue
                df_cut = df_sym[df_sym["date"] <= current_date]
                if df_cut.empty: continue
                price = float(df_cut["close"].iloc[-1])
                pnl_pct = (price - pos["entry_price"]) / pos["entry_price"]
                hold = (pd.Timestamp(current_date) - pd.Timestamp(pos["entry_date"])).days
                reason = None
                if pnl_pct <= -0.08: reason = "stop_loss"
                elif pnl_pct >= 0.20: reason = "take_profit"
                elif hold >= 20: reason = "time_exit"
                if reason:
                    ex_price = price * (1 - slippage)
                    pnl = (ex_price - pos["entry_price"]) * pos["quantity"]
                    pnl -= pos["entry_price"] * pos["quantity"] * commission
                    pnl -= ex_price * pos["quantity"] * commission
                    pnl -= ex_price * pos["quantity"] * stamp_tax
                    trades.append({"symbol": sym, "entry": pos["entry_date"], "exit": current_date,
                                   "entry_price": pos["entry_price"], "exit_price": ex_price,
                                   "quantity": pos["quantity"], "pnl": round(pnl, 2),
                                   "pnl_pct": round(pnl_pct*100,2), "reason": reason})
                    capital += pnl
                    del positions[sym]

            available = 10 - len(positions)
            for sym in selected[:available]:
                if sym in positions: continue
                df_sym = engine._cache.get(sym)
                if df_sym is None: continue
                df_cut = df_sym[df_sym["date"] <= current_date]
                if df_cut.empty: continue
                price = float(df_cut["close"].iloc[-1]) * (1 + slippage)
                pos_val = capital * 0.10
                qty = max(int(pos_val / price / 100) * 100, 100)
                if qty >= 100:
                    positions[sym] = {"entry_date": current_date, "entry_price": price, "quantity": qty}

            mv = 0.0
            for sym, pos in positions.items():
                df_sym = engine._cache.get(sym)
                if df_sym is None: continue
                df_cut = df_sym[df_sym["date"] <= current_date]
                if not df_cut.empty:
                    mv += float(df_cut["close"].iloc[-1]) * pos["quantity"]
            equity_curve.append({"date": current_date, "equity": capital + mv})

        df_eq = pd.DataFrame(equity_curve)
        total_ret = (df_eq["equity"].iloc[-1] - df_eq["equity"].iloc[0]) / df_eq["equity"].iloc[0] if len(df_eq) > 1 else 0
        wins = sum(1 for t in trades if t["pnl"] > 0)
        wr = wins / len(trades) if trades else 0
        print(f"{name}:")
        print(f"  交易: {len(trades)}笔 | 胜率: {wr:.1%} | 收益: {total_ret:.1%} | 资金: {df_eq['equity'].iloc[-1]:,.0f}")

if __name__ == "__main__":
    multi_strategy_backtest()
