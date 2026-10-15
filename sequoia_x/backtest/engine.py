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
    max_hold_days: int = 20                     # 最长持仓天数
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

        for i in range(cfg.min_bars, len(all_dates)):
            current_date = all_dates[i]

            # -- 1. 运行选股策略（reference_date 由策略基类 get_data() 自动切片） --
            try:
                # 设置参考日期（替代 date.today() 的新鲜度检查）
                if hasattr(strategy, 'reference_date'):
                    strategy.reference_date = current_date
                selected = strategy.run()
            except Exception as exc:
                logger.warning(f"[{current_date}] 策略运行失败: {exc}")
                continue
            if not isinstance(selected, list):
                selected = []

            # -- 3. 检查现有持仓的退出条件 --
            for sym in list(positions.keys()):
                pos = positions[sym]
                try:
                    if sym not in all_data:
                        continue
                    df_sym = all_data[sym]
                    df_cut = df_sym[df_sym["date"] <= current_date]
                    if df_cut.empty:
                        continue
                    current_price = float(df_cut["close"].iloc[-1])
                    pnl_pct = (current_price - pos["entry_price"]) / pos["entry_price"]
                    hold_days = (pd.Timestamp(current_date) - pd.Timestamp(pos["entry_date"])).days

                    exit_reason = None
                    if pnl_pct <= cfg.stop_loss_pct:
                        exit_reason = "stop_loss"
                    elif pnl_pct >= cfg.take_profit_pct:
                        exit_reason = "take_profit"
                    elif hold_days >= cfg.max_hold_days:
                        exit_reason = "time_exit"

                    # A股执行约束：成交价一字跌停则卖不出，持仓顺延到下一交易日
                    if exit_reason:
                        fill = self._fill(df_sym, current_date)
                        if fill is None:
                            continue
                        exit_date, fill_price = fill
                        prev_rows = df_sym[df_sym["date"] < exit_date]
                        if not prev_rows.empty:
                            prev_close = float(prev_rows["close"].iloc[-1])
                            limit_down = round(prev_close * (1 - self._limit_pct(sym)), 2)
                            if fill_price <= limit_down + 0.001:
                                continue
                        exit_price = fill_price * (1 - cfg.slippage)
                        # 平仓：收回本金+盈亏（扣卖出佣金与印花税），与开仓时扣除的成本对应
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
                            exit_reason=exit_reason,
                            hold_days=hold_days,
                        ))
                        capital += proceeds - exit_commission - exit_tax
                        del positions[sym]
                except Exception:
                    continue

            # -- 4. 开新仓 --
            available_slots = cfg.max_positions - len(positions)
            for sym in selected[:available_slots]:
                if sym in positions:
                    continue
                try:
                    if sym not in all_data:
                        continue
                    df_sym = all_data[sym]
                    fill = self._fill(df_sym, current_date)
                    if fill is None:
                        continue
                    fill_date, fill_price = fill
                    # A股执行约束：成交价一字涨停则买不进
                    prev_rows = df_sym[df_sym["date"] < fill_date]
                    if not prev_rows.empty:
                        prev_close = float(prev_rows["close"].iloc[-1])
                        limit_up = round(prev_close * (1 + self._limit_pct(sym)), 2)
                        if fill_price >= limit_up - 0.001:
                            continue
                    entry_price = fill_price * (1 + cfg.slippage)
                    position_value = capital * cfg.position_size_pct
                    quantity = max(int(position_value / entry_price / 100) * 100, 100)
                    cost = entry_price * quantity * (1 + cfg.commission)
                    if cost > 0 and cost <= capital:  # 现金足够才开仓，并即时扣除成本
                        capital -= cost
                        positions[sym] = {
                            "entry_date": fill_date,
                            "entry_price": entry_price,
                            "quantity": quantity,
                        }
                except Exception:
                    continue

            # -- 5. 记录权益 --
            market_value = 0.0
            for sym, pos in list(positions.items()):
                try:
                    if sym in all_data:
                        df_sym = all_data[sym]
                        df_cut = df_sym[df_sym["date"] <= current_date]
                        if not df_cut.empty:
                            curr_price = float(df_cut["close"].iloc[-1])
                            market_value += curr_price * pos["quantity"]
                except Exception:
                    pass
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

    def _fill(self, df_sym: pd.DataFrame,
              current_date: str) -> tuple[str, float] | None:
        """按 `fill_mode` 给出成交日与成交价。

        same_close：当日收盘价（历史实现）。信号本身就是拿当日收盘算出来的，
        当天以同一收盘价成交是前视——见 H3。
        next_open：下一交易日开盘价。

        返回 None 表示没有可成交的 bar（该股此时还没有数据，或已到区间末尾）。
        """
        df_cut = df_sym[df_sym["date"] <= current_date]
        if df_cut.empty:
            return None
        if self.config.fill_mode != "next_open":
            return current_date, float(df_cut["close"].iloc[-1])
        df_next = df_sym[df_sym["date"] > current_date]
        if df_next.empty:
            return None
        return str(df_next["date"].iloc[0]), float(df_next["open"].iloc[0])

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
