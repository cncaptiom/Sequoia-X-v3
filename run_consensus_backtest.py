"""多策略共识回测：运行全部6策略，用ConsensusEngine融合评分，只交易高分股票。

对比单策略 vs 多策略共识的表现。

账本口径（2026-11-09 修复，见审查 #1）：
  · cash 只记「可用现金」。开仓时扣 cost，平仓时加回成交净额。
  · 每日权益 = cash + 持仓市值，不再出现成本与市值重复计入。
  · 行情数据与股票池统一按 reference_date 截断，未预加载的股票也会截断。

⚠️ 本脚本仍是「信号当日收盘成交」的简化口径（无 T+1、无涨跌停约束），
   结论只能用于策略间相对比较，不能当作实盘收益预期。
"""
import os, sys, time, json
os.environ.setdefault('DB_PATH', 'data/sequoia_v2.db')

import sqlite3, pandas as pd, numpy as np
from datetime import date, timedelta

# ---- 回测参数 ----
BACKTEST_START = "2026-04-01"
BACKTEST_END = "2026-06-01"
DATA_START = "2025-01-01"      # 含前置回看（策略最长需要 60 根K线）
INITIAL_CAPITAL = 1_000_000
MAX_POSITIONS = 10
POSITION_PCT = 0.10
COMMISSION = 0.0003
STAMP_TAX = 0.0005             # 卖出印花税（2023-08 起 0.05%）
SLIPPAGE = 0.001
STOP_LOSS = -0.08
TAKE_PROFIT = 0.20
MAX_HOLD_DAYS = 20
MIN_BARS = 30


# ---- 工具函数 ----

def get_dates(db_path, start, end):
    """获取回测区间的交易日列表。

    必须取自全市场 DISTINCT date——从单只股票推日历会被该股停牌带偏。
    """
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT date FROM stock_daily WHERE date BETWEEN ? AND ? ORDER BY date",
            (start, end),
        ).fetchall()
    return [r[0] for r in rows]


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


def _cut(df, current_date):
    """按回测日截断（防未来数据）。"""
    if df is None:
        return None
    return df[df["date"] <= current_date]


def _close_price(df, current_date):
    df_cut = _cut(df, current_date)
    if df_cut is None or df_cut.empty:
        return None
    return float(df_cut["close"].iloc[-1])


def _position_market_value(positions, cache, current_date):
    mv = 0.0
    for sym, pos in positions.items():
        price = _close_price(cache.get(sym), current_date)
        if price is not None:
            mv += price * pos["quantity"]
    return mv


def _open_position(positions, sym, price_raw, cash, equity, current_date):
    """按单票预算开仓，返回新的现金余额；预算不足一手则跳过。

    预算按「总权益 × 单票上限」计算，不足一手时不强行买 100 股（见审查 #4）。
    """
    entry_price = price_raw * (1 + SLIPPAGE)
    budget = equity * POSITION_PCT
    qty = int(budget / entry_price / 100) * 100
    # 现金约束：最多买到现金允许的整手数
    max_qty = int(cash / (entry_price * (1 + COMMISSION)) / 100) * 100
    qty = min(qty, max_qty)
    if qty < 100:
        return cash
    cost = entry_price * qty * (1 + COMMISSION)
    positions[sym] = {
        "entry_date": current_date,
        "entry_price": entry_price,
        "quantity": qty,
        "entry_cost": cost,
    }
    return cash - cost


def _close_position(positions, sym, exit_price_raw, current_date, cash, reason, trades):
    """平仓：把成交净额加回现金，返回新的现金余额。"""
    pos = positions.pop(sym)
    ex_price = exit_price_raw * (1 - SLIPPAGE)
    proceeds = ex_price * pos["quantity"] * (1 - COMMISSION - STAMP_TAX)
    pnl = proceeds - pos["entry_cost"]
    pnl_pct = (ex_price - pos["entry_price"]) / pos["entry_price"] * 100
    trades.append({
        "symbol": sym, "entry": pos["entry_date"], "exit": current_date,
        "entry_price": pos["entry_price"], "exit_price": ex_price,
        "quantity": pos["quantity"], "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2), "reason": reason,
    })
    return cash + proceeds


def _run_one(cache, all_dates, strategies, entry_filter):
    """按给定共识准入条件跑一遍回测，返回 (trades, equity_curve)。"""
    cash = float(INITIAL_CAPITAL)
    positions = {}
    trades = []
    equity_curve = []

    for i in range(MIN_BARS, len(all_dates)):
        current_date = all_dates[i]

        # 运行全部策略（reference_date 由策略基类 get_data() 自动切片）
        results = run_strategies(strategies, cache, current_date)
        scored = consensus_score(results)
        selected = [sym for sym, cnt, score in scored if entry_filter((sym, cnt, score))]

        # 先平仓（未实现盈亏落袋，现金回笼后才能开新仓）
        for sym in list(positions.keys()):
            pos = positions[sym]
            price = _close_price(cache.get(sym), current_date)
            if price is None:
                continue
            pnl_pct = (price - pos["entry_price"]) / pos["entry_price"]
            hold = (pd.Timestamp(current_date) - pd.Timestamp(pos["entry_date"])).days

            reason = None
            if pnl_pct <= STOP_LOSS: reason = "stop_loss"
            elif pnl_pct >= TAKE_PROFIT: reason = "take_profit"
            elif hold >= MAX_HOLD_DAYS: reason = "time_exit"

            if reason:
                cash = _close_position(positions, sym, price, current_date,
                                       cash, reason, trades)

        # 再开新仓：预算按当前总权益计算，现金不足整手则跳过
        equity = cash + _position_market_value(positions, cache, current_date)
        for sym in selected:
            if len(positions) >= MAX_POSITIONS:
                break
            if sym in positions:
                continue
            price = _close_price(cache.get(sym), current_date)
            if price is None:
                continue
            cash = _open_position(positions, sym, price, cash, equity, current_date)

        # 记录权益：现金 + 持仓市值
        equity_curve.append({
            "date": current_date,
            "equity": cash + _position_market_value(positions, cache, current_date),
        })

    return trades, equity_curve


def _report(name, trades, equity_curve):
    df_eq = pd.DataFrame(equity_curve)
    total_ret = ((df_eq["equity"].iloc[-1] - df_eq["equity"].iloc[0]) /
                 df_eq["equity"].iloc[0]) if len(df_eq) > 1 else 0
    wins = sum(1 for t in trades if t["pnl"] > 0)
    losses = sum(1 for t in trades if t["pnl"] < 0)
    wr = wins / len(trades) if trades else 0
    print(f"\n{name}:")
    print(f"  交易: {len(trades)}笔 | 胜率: {wr:.1%} | 收益: {total_ret:.1%} | 资金: {df_eq['equity'].iloc[-1]:,.0f}")
    if trades:
        print(f"  盈利: {wins}笔 | 亏损: {losses}笔 | "
              f"平均盈利: {np.mean([t['pnl'] for t in trades if t['pnl'] > 0] or [0]):.0f} | "
              f"平均亏损: {np.mean([t['pnl'] for t in trades if t['pnl'] < 0] or [0]):.0f}")
    return total_ret


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

    # 股票池 = 策略实际遍历的范围（get_local_symbols），
    # 只预加载前 200 只会让其余股票走 SQL 分支，既不截断也慢。
    symbols = engine.get_local_symbols()
    all_dates = get_dates(engine.db_path, BACKTEST_START, BACKTEST_END)
    print(f"回测: {len(symbols)}只, {len(all_dates)}个交易日")

    # 只预加载回测窗口 + 前置回看，避免全历史占内存
    engine.preload_all(symbols, start_date=DATA_START, end_date=BACKTEST_END)
    cache = engine._cache

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
        s.preload(cache)

    configs = [
        ("多策略共识-策略数>=2", lambda s: s[1] >= 2),
        ("多策略共识-策略数>=3", lambda s: s[1] >= 3),
        ("多策略共识-分数>=45", lambda s: s[2] >= 45),
    ]
    for config_name, entry_filter in configs:
        trades, equity_curve = _run_one(cache, all_dates, strategies, entry_filter)
        _report(config_name, trades, equity_curve)

    # 对照：单策略MaVolume
    print("\n--- 对照：单策略 ---")
    for name, strategy in strategies[:1]:
        # 单策略复用同一套账本：用该策略自己的选股结果替代共识结果
        cash = float(INITIAL_CAPITAL)
        positions = {}
        trades = []
        equity_curve = []
        for i in range(MIN_BARS, len(all_dates)):
            current_date = all_dates[i]
            strategy.reference_date = current_date
            try:
                selected = strategy.run() or []
            except Exception:
                selected = []

            for sym in list(positions.keys()):
                pos = positions[sym]
                price = _close_price(cache.get(sym), current_date)
                if price is None:
                    continue
                pnl_pct = (price - pos["entry_price"]) / pos["entry_price"]
                hold = (pd.Timestamp(current_date) - pd.Timestamp(pos["entry_date"])).days
                reason = None
                if pnl_pct <= STOP_LOSS: reason = "stop_loss"
                elif pnl_pct >= TAKE_PROFIT: reason = "take_profit"
                elif hold >= MAX_HOLD_DAYS: reason = "time_exit"
                if reason:
                    cash = _close_position(positions, sym, price, current_date,
                                           cash, reason, trades)

            equity = cash + _position_market_value(positions, cache, current_date)
            for sym in selected:
                if len(positions) >= MAX_POSITIONS:
                    break
                if sym in positions:
                    continue
                price = _close_price(cache.get(sym), current_date)
                if price is None:
                    continue
                cash = _open_position(positions, sym, price, cash, equity, current_date)

            equity_curve.append({
                "date": current_date,
                "equity": cash + _position_market_value(positions, cache, current_date),
            })

        _report(f"{name}(单策略)", trades, equity_curve)


if __name__ == "__main__":
    multi_strategy_backtest()
