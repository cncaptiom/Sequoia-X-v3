"""回测引擎：滑动窗口执行策略，模拟交易并记录绩效。

设计参考:
  - AQR 的样本外回测方法论: 严格时间序列划分，避免未来信息泄漏
  - 海龟交易回测: 含滑点、手续费、止损模拟
"""

from dataclasses import dataclass, field
from typing import Callable, Optional, Any
from datetime import date, timedelta

import pandas as pd
import numpy as np

from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)


# ── 配置 ──

@dataclass
class BacktestConfig:
    """回测配置参数。"""
    initial_capital: float = 1_000_000.0       # 初始资金
    max_positions: int = 10                     # 同时最大持仓数
    position_size_pct: float = 0.10             # 单票仓位上限（占资金%）
    commission: float = 0.0003                  # 手续费率（万三）
    stamp_tax: float = 0.0005                   # 卖出印花税（2023-08 起 0.05%）
    slippage: float = 0.001                     # 滑点（千一）
    stop_loss_pct: float = -0.08                # 止损线（-8%）
    take_profit_pct: float = 0.20               # 止盈线（+20%）
    max_hold_days: int = 20                     # 最长持仓【交易日】数（与 signal/models.py 一致）
    min_bars: int = 30                          # 最少需要的历史K线
    benchmark_symbol: str = "sh.000300"         # 基准：沪深300
    # 成交时点：
    #   "next_open"  = 信号次日开盘价成交（**默认**）。信号是拿当日收盘算出来的，
    #                  次日开盘才可执行。
    #   "same_close" = 信号当日收盘价成交（2026-10-15 之前的旧行为）。A股没有 MOC
    #                  指令，当天以同一根K线的收盘价成交做不到——见 H3 未来函数。
    # 实测（TurtleTrade / 全市场 / 近一年）：same_close 把年化虚高约 5.4pp、
    # 回撤美化 5.2pp。比这更早的回测数字都带着这个偏差。
    fill_mode: str = "next_open"


# ── 交易记录 ──

@dataclass
class Trade:
    """单笔交易记录。"""
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    exit_reason: str           # "stop_loss" | "take_profit" | "time_exit" | "signal_exit"
    hold_days: int


# ── 回测结果 ──

@dataclass
class BacktestResult:
    """回测完整结果。"""
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    benchmark_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    config: BacktestConfig = field(default_factory=BacktestConfig)

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.pnl > 0)
        return wins / len(self.trades)

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t.pnl for t in self.trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self.trades if t.pnl < 0))
        return gross_profit / gross_loss if gross_loss > 0 else float("inf")

    @property
    def total_return(self) -> float:
        if self.equity_curve.empty:
            return 0.0
        start = self.equity_curve["equity"].iloc[0]
        end = self.equity_curve["equity"].iloc[-1]
        return (end - start) / start

    @property
    def max_drawdown(self) -> float:
        if self.equity_curve.empty:
            return 0.0
        equity = self.equity_curve["equity"].values
        peak = np.maximum.accumulate(equity)
        dd = (equity - peak) / peak
        return float(dd.min())

    @property
    def sharpe_ratio(self) -> float:
        """年化夏普比率（假设无风险利率=0.03）。"""
        if self.equity_curve.empty or len(self.equity_curve) < 2:
            return 0.0
        returns = self.equity_curve["equity"].pct_change().dropna()
        if len(returns) < 2 or returns.std() == 0:
            return 0.0
        rf_daily = 0.03 / 252
        excess = returns - rf_daily
        return float(excess.mean() / excess.std() * np.sqrt(252))

    @property
    def annual_return(self) -> float:
        """年化收益率。"""
        if self.equity_curve.empty or len(self.equity_curve) < 2:
            return 0.0
        total = self.total_return
        days = len(self.equity_curve)
        years = days / 252
        if years == 0:
            return 0.0
        return float((1 + total) ** (1 / years) - 1)


# ── 回测引擎 ──

class BacktestEngine:
    """滑动窗口回测引擎。

    对每个交易日：
      1. 运行选股策略获取信号
      2. 对已有持仓检查止损/止盈/超时
      3. 对新信号分配仓位，考虑资金和仓位限制
      4. 记录权益曲线
    """

    def __init__(self, data_engine, config: Optional[BacktestConfig] = None):
        self._real_engine = data_engine
        self.engine = data_engine
        self.config = config or BacktestConfig()

    def run(
        self,
        strategy_class: type,
        symbols: list[str],
        start_date: str,
        end_date: str,
        strategy_kwargs: dict | None = None,
    ) -> BacktestResult:
        """执行回测。

        Args:
            strategy_class: 策略类（非实例），需实现 .run() → list[str]。
            symbols: 股票池。
            start_date: 回测起始日 "2024-01-01"。
            end_date: 回测结束日 "2025-01-01"。
            strategy_kwargs: 传递给策略构造函数的额外参数。

        Returns:
            BacktestResult 含全部交易记录和权益曲线。
        """
        cfg = self.config

        # ---- 预加载全市场数据到内存 (加速回测) ----
        self._real_engine.preload_all(symbols)
        all_data = getattr(self._real_engine, '_cache', {})
        if not all_data:
            logger.warning("预加载数据为空，回退到逐只SQL查询")
        logger.info(f"预加载完成: {len(all_data)} 只股票, 开始滑动窗口回测...")

        trades: list[Trade] = []
        positions: dict[str, dict] = {}
        # 待成交订单（next_open 模式）：T 日收盘只生成订单，T+1 开盘才扣款建仓。
        # 旧实现在 T 日循环里就直接扣款建仓、再用 T 日收盘估值，等于把还没发生的
        # T+1 成交提前计入 T 日权益（见审查 #3）。
        pending_buys: dict[str, int] = {}     # symbol -> 已顺延的交易日数
        pending_sells: dict[str, dict] = {}   # symbol -> {"reason": str, "waited": int}
        MAX_PENDING_DAYS = 3                  # 停牌/一字板最多顺延 3 个交易日后放弃

        # ---- 获取交易日历 ----
        all_dates = self._get_trading_dates(symbols[0], start_date, end_date)
        if len(all_dates) < cfg.min_bars:
            logger.warning(f"交易日不足: {len(all_dates)} < {cfg.min_bars}")
            return BacktestResult(config=cfg)

        # ---- 获取基准指数数据 ----
        benchmark_history: dict[str, float] = {}
        try:
            bench_symbol = cfg.benchmark_symbol.split('.')[-1]
            bench_df = self._real_engine.get_ohlcv(bench_symbol)
            if not bench_df.empty:
                bench_dict = dict(zip(bench_df["date"], bench_df["close"]))
                benchmark_history = bench_dict
                logger.info(f"基准 {cfg.benchmark_symbol} 数据加载完成: {len(benchmark_history)} 个交易日")
            else:
                logger.warning(f"基准 {cfg.benchmark_symbol} 无数据，回退到常数基准")
        except Exception as e:
            logger.warning(f"基准数据加载失败: {e}")

        # ---- 滑动窗口回测 ----
        capital = cfg.initial_capital
        equity_records = []
        benchmark_records = []

        # 创建策略实例
        kwargs = dict(strategy_kwargs) if strategy_kwargs else {}
        if "engine" not in kwargs:
            kwargs["engine"] = self._real_engine
        if "settings" not in kwargs:
            from sequoia_x.core.config import get_settings
            kwargs["settings"] = get_settings()
        strategy = strategy_class(**kwargs)
        strategy.preload(all_data)

        date_idx = {d: k for k, d in enumerate(all_dates)}

        for i in range(cfg.min_bars, len(all_dates)):
            current_date = all_dates[i]
            is_next_open = cfg.fill_mode == "next_open"

            # -- 0. next_open：执行上一交易日收盘生成的待成交订单 --
            if is_next_open:
                for sym in list(pending_sells.keys()):
                    order = pending_sells[sym]
                    pos = positions.get(sym)
                    df_sym = all_data.get(sym)
                    bar = self._bar_on(df_sym, current_date)
                    if pos is None:
                        pending_sells.pop(sym, None)
                        continue
                    if bar is None:
                        order["waited"] += 1
                        if order["waited"] > MAX_PENDING_DAYS:
                            pending_sells.pop(sym, None)
                        continue
                    _, limit_down = self._limit_prices(df_sym, current_date, sym)
                    open_px = float(bar["open"])
                    # 开盘一字跌停卖不出，顺延
                    if limit_down is not None and open_px <= limit_down + 0.001:
                        order["waited"] += 1
                        if order["waited"] > MAX_PENDING_DAYS:
                            pending_sells.pop(sym, None)
                        continue
                    pending_sells.pop(sym, None)
                    capital = self._book_exit(
                        sym, positions, open_px, current_date, capital, order["reason"],
                        i - pos["entry_idx"], cfg, trades)

                for sym in list(pending_buys.keys()):
                    waited = pending_buys.pop(sym)
                    if sym in positions or len(positions) >= cfg.max_positions:
                        continue
                    df_sym = all_data.get(sym)
                    bar = self._bar_on(df_sym, current_date)
                    if bar is None:
                        # 该股当日无行情（停牌）：继续等待，超时则放弃
                        if waited + 1 <= MAX_PENDING_DAYS:
                            pending_buys[sym] = waited + 1
                        continue
                    limit_up, _ = self._limit_prices(df_sym, current_date, sym)
                    open_px = float(bar["open"])
                    # 开盘一字涨停买不进，放弃本次订单
                    if limit_up is not None and open_px >= limit_up - 0.001:
                        continue
                    equity_now = capital + self._market_value(
                        positions, all_data, current_date, inclusive=False)
                    new_capital = self._book_entry(
                        sym, open_px, capital, equity_now, current_date,
                        date_idx, i, cfg, positions)
                    if new_capital is not None:
                        capital = new_capital

            # -- 1. 运行选股策略（失败只阻止新增订单，不影响下面的持仓风控） --
            selected: list[str] = []
            try:
                # 设置参考日期（替代 date.today() 的新鲜度检查）
                if hasattr(strategy, 'reference_date'):
                    strategy.reference_date = current_date
                selected = strategy.run()
                if not isinstance(selected, list):
                    selected = []
            except Exception as exc:
                logger.warning(f"[{current_date}] 策略运行失败，跳过新增订单: {exc}")

            # -- 2. 检查现有持仓的退出条件（每个交易日都必须执行） --
            for sym in list(positions.keys()):
                pos = positions[sym]
                try:
                    if sym not in all_data:
                        continue
                    df_sym = all_data[sym]
                    df_cut = df_sym[df_sym["date"] <= current_date]
                    if df_cut.empty:
                        continue
                    bar = df_cut.iloc[-1]
                    open_px = float(bar["open"])
                    low_px = float(bar["low"])
                    high_px = float(bar["high"])
                    # 持仓天数按**交易日**算。此前用自然日：`max_hold_days=20` 实际只有
                    # 约 14 个交易日，比 signal/models.py 声明的「20 交易日」短约 30%。
                    hold_days = i - pos["entry_idx"]

                    # 止损/止盈按**盘中**（当日 low/high）判定，并在触发价成交。
                    # 这两类是可以预挂的条件单，盘中触发即可成交；此前只在收盘比一次，
                    # 盘中跌破止损线但收盘拉回就不会触发，回测偏乐观（见台账 M4）。
                    # 跳空越过触发价时以当日开盘价成交（取更差的一边）。
                    stop_px = pos["entry_price"] * (1 + cfg.stop_loss_pct)
                    take_px = pos["entry_price"] * (1 + cfg.take_profit_pct)
                    exit_reason = None
                    trigger_px = None
                    if low_px <= stop_px:
                        exit_reason, trigger_px = "stop_loss", min(open_px, stop_px)
                    elif high_px >= take_px:
                        exit_reason, trigger_px = "take_profit", max(open_px, take_px)
                    elif hold_days >= cfg.max_hold_days:
                        exit_reason = "time_exit"

                    if exit_reason:
                        if trigger_px is not None:
                            # A股执行约束：成交价一字跌停则卖不出，持仓顺延到下一交易日
                            _, limit_down = self._limit_prices(df_sym, current_date, sym)
                            if limit_down is not None and trigger_px <= limit_down + 0.001:
                                continue
                            capital = self._book_exit(
                                sym, positions, trigger_px, current_date, capital,
                                exit_reason, hold_days, cfg, trades)
                        elif is_next_open:
                            # 到期退出不是条件单：按 next_open 口径排到 T+1 开盘成交
                            if sym not in pending_sells:
                                pending_sells[sym] = {"reason": exit_reason, "waited": 0}
                        else:
                            capital = self._book_exit(
                                sym, positions, float(bar["close"]), current_date, capital,
                                exit_reason, hold_days, cfg, trades)
                except Exception as exc:
                    logger.warning(f"[{current_date}] {sym} 持仓处理失败: {exc}")
                    continue

            # -- 3. 生成新订单（成交前不计入持仓/权益） --
            available_slots = cfg.max_positions - len(positions) - len(pending_buys)
            for sym in selected:
                if available_slots <= 0:
                    break
                if sym in positions or sym in pending_buys:
                    continue
                if sym not in all_data:
                    continue
                if is_next_open:
                    pending_buys[sym] = 0
                    available_slots -= 1
                    continue
                bar = self._bar_on(all_data[sym], current_date)
                if bar is None:
                    continue
                # A股执行约束：收盘价一字涨停买不进（与 next_open 分支同口径）
                limit_up, _ = self._limit_prices(all_data[sym], current_date, sym)
                if limit_up is not None and float(bar["close"]) >= limit_up - 0.001:
                    continue
                equity_now = capital + self._market_value(
                    positions, all_data, current_date, inclusive=False)
                new_capital = self._book_entry(
                    sym, float(bar["close"]), capital, equity_now, current_date,
                    date_idx, i, cfg, positions)
                if new_capital is not None:
                    capital = new_capital
                    available_slots -= 1

            # -- 4. 记录权益（只含已成交持仓） --
            market_value = self._market_value(positions, all_data, current_date)
            total_equity = capital + market_value
            equity_records.append({"date": current_date, "equity": round(total_equity, 2)})

            # 基准记录：用真实基准指数价格归一化到初始资金
            if benchmark_history and current_date in benchmark_history:
                bench_price = benchmark_history[current_date]
                first_bench = None
                for d in all_dates:
                    if d in benchmark_history:
                        first_bench = benchmark_history[d]
                        break
                if first_bench and first_bench > 0:
                    bench_equity = cfg.initial_capital * bench_price / first_bench
                else:
                    bench_equity = cfg.initial_capital
            else:
                bench_equity = cfg.initial_capital
            benchmark_records.append({"date": current_date, "benchmark": round(bench_equity, 2)})

            if (i - cfg.min_bars) % 50 == 0:
                logger.info(
                    f"[回测] {current_date} | 持仓{len(positions)} | "
                    f"累计交易{len(trades)}笔 | 权益{total_equity:,.0f}"
                )

        result = BacktestResult(
            trades=trades,
            equity_curve=pd.DataFrame(equity_records),
            benchmark_curve=pd.DataFrame(benchmark_records),
            config=cfg,
        )
        logger.info(
            f"回测完成: {len(result.trades)}笔交易 | "
            f"胜率{result.win_rate:.1%} | "
            f"夏普{result.sharpe_ratio:.2f} | "
            f"最大回撤{result.max_drawdown:.1%}"
        )
        return result

    # ── 成交与记账辅助 ──

    @staticmethod
    def _bar_on(df_sym: pd.DataFrame | None, d: str):
        """取某只股票在 d 日的 K 线；停牌/无数据返回 None。"""
        if df_sym is None:
            return None
        rows = df_sym[df_sym["date"] == d]
        return None if rows.empty else rows.iloc[0]

    @staticmethod
    def _market_value(positions: dict, all_data: dict, current_date: str,
                      inclusive: bool = True) -> float:
        """持仓市值。

        inclusive=False 时只用 current_date **之前**的收盘价——给当日开盘成交
        定价时，不能用当天收盘价估值（那是当天还不知道的信息）。
        """
        mv = 0.0
        for sym, pos in positions.items():
            df_sym = all_data.get(sym)
            if df_sym is None:
                continue
            if inclusive:
                df_cut = df_sym[df_sym["date"] <= current_date]
            else:
                df_cut = df_sym[df_sym["date"] < current_date]
            if not df_cut.empty:
                mv += float(df_cut["close"].iloc[-1]) * pos["quantity"]
        return mv

    def _limit_prices(self, df_sym: pd.DataFrame, d: str,
                      symbol: str) -> tuple[float | None, float | None]:
        """d 日的涨停价/跌停价（按前一交易日收盘价 × 板块涨跌幅）。"""
        prev = df_sym[df_sym["date"] < d]
        if prev.empty:
            return None, None
        prev_close = float(prev["close"].iloc[-1])
        pct = self._limit_pct(symbol)
        return round(prev_close * (1 + pct), 2), round(prev_close * (1 - pct), 2)

    def _book_entry(self, sym: str, fill_price: float, capital: float, equity: float,
                    fill_date: str, date_idx: dict, i: int, cfg: BacktestConfig,
                    positions: dict) -> float | None:
        """按单票预算建仓并扣减现金；预算不足一手时跳过（不强行买 100 股）。

        返回新现金余额；跳过则返回 None。
        """
        entry_price = fill_price * (1 + cfg.slippage)
        if entry_price <= 0:
            return None
        budget = min(equity * cfg.position_size_pct, capital)
        quantity = int(budget / entry_price / 100) * 100
        if quantity < 100:
            return None
        cost = entry_price * quantity * (1 + cfg.commission)
        if cost > capital:
            return None
        positions[sym] = {
            "entry_date": fill_date,
            "entry_idx": date_idx.get(fill_date, i),
            "entry_price": entry_price,
            "quantity": quantity,
        }
        return capital - cost

    def _book_exit(self, sym: str, positions: dict, fill_price: float,
                   exit_date: str, capital: float, reason: str, hold_days: int,
                   cfg: BacktestConfig, trades: list[Trade]) -> float:
        """平仓并记账，返回新现金余额（成交净额已加回）。"""
        pos = positions.pop(sym)
        exit_price = fill_price * (1 - cfg.slippage)
        entry_cost = pos["entry_price"] * pos["quantity"] * (1 + cfg.commission)
        proceeds = exit_price * pos["quantity"]
        exit_commission = proceeds * cfg.commission
        exit_tax = proceeds * cfg.stamp_tax
        pnl = proceeds - exit_commission - exit_tax - entry_cost
        trades.append(Trade(
            symbol=sym,
            entry_date=pos["entry_date"],
            exit_date=exit_date,
            entry_price=pos["entry_price"],
            exit_price=exit_price,
            quantity=pos["quantity"],
            pnl=round(pnl, 2),
            pnl_pct=round((pnl / entry_cost) * 100, 2) if entry_cost > 0 else 0.0,
            exit_reason=reason,
            hold_days=hold_days,
        ))
        return capital + proceeds - exit_commission - exit_tax

    @staticmethod
    def _limit_pct(symbol: str) -> float:
        """A股涨跌停幅度（按板块）：主板±10%、创业板/科创板±20%、北交所±30%。

        注：ST 股为 ±5%，此处未区分（回测池未过滤 ST 时的近似）。
        """
        code = symbol.strip().split('.')[-1].zfill(6)
        if code.startswith(("688", "300", "301")):
            return 0.20
        if code.startswith(("8", "4", "92")):
            return 0.30
        return 0.10

    def _get_trading_dates(self, sample_symbol: str, start: str, end: str) -> list[str]:
        """获取回测区间内的交易日列表（从数据库全局查询）。"""
        import sqlite3
        db_path = getattr(self._real_engine, "db_path", "data/sequoia_v2.db")
        try:
            with sqlite3.connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT DISTINCT date FROM stock_daily WHERE date BETWEEN ? AND ? ORDER BY date",
                    (start, end),
                ).fetchall()
            result = [r[0] for r in rows]
            return result
        except Exception as e:
            return []

    def _get_active_symbols(self, symbols: list[str], current_date: str) -> list[str]:
        """过滤出在 current_date 有数据的股票。"""
        return symbols
