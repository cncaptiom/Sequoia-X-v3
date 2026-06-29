"""ETF 轮动回测引擎。

月频调仓，模拟真实的交易执行：
  - 双边佣金万0.5（ETF费率通常为万0.5至万1）
  - 按收盘价成交
  - 等权配置
  - 考虑涨停/跌停无法买入的情况
"""

from dataclasses import dataclass, field
from typing import Optional, Callable
from datetime import date, timedelta

import numpy as np
import pandas as pd

from fund_research.core.logger import get_logger
from fund_research.data.etf_list import get_etf_map

logger = get_logger(__name__)


@dataclass
class BacktestConfig:
    """回测配置。"""
    initial_capital: float = 1_000_000
    commission: float = 0.0005          # 双边万五
    slippage: float = 0.0005            # 滑点万五
    top_n: int = 5                       # 持仓数量
    hold_days: int = 21                  # 调仓间隔
    benchmark_code: str = "510300"       # 基准：沪深300ETF


@dataclass
class TradeRecord:
    """单笔交易记录。"""
    date: str
    code: str
    name: str
    action: str            # "buy" / "sell"
    price: float
    shares: int
    value: float
    commission: float


@dataclass
class BacktestResult:
    """回测结果。"""
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    trades: list = field(default_factory=list)
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    benchmark_return: float = 0.0
    benchmark_annual: float = 0.0

    def summary(self) -> str:
        lines = [
            "=" * 50,
            "ETF轮动回测结果",
            "=" * 50,
            f"累计收益:  策略 {self.total_return:.1%}  |  基准 {self.benchmark_return:.1%}",
            f"年化收益:  策略 {self.annual_return:.1%}  |  基准 {self.benchmark_annual:.1%}",
            f"夏普比率:  {self.sharpe_ratio:.2f}",
            f"最大回撤:  {self.max_drawdown:.1%}",
            f"交易次数:  {self.total_trades} 笔",
            f"胜率:      {self.win_rate:.1%}",
        ]
        return "\n".join(lines)


class EtfRotationBacktest:
    """ETF 轮动回测引擎。"""

    def __init__(self, config: Optional[BacktestConfig] = None):
        self.config = config or BacktestConfig()
        self.etf_map = get_etf_map()

    def run(
        self,
        data: dict[str, pd.DataFrame],
        strategy: Callable,
        start_date: str,
        end_date: str,
    ) -> BacktestResult:
        """执行回测。

        Args:
            data: {code: DataFrame(ohlcv)} 全量ETF数据。
            strategy: 轮动策略函数，签名 fn(data, date) → list[code]。
            start_date: 回测起始日。
            end_date: 回测结束日。
        """
        cfg = self.config

        # ── 构建交易日历 ──
        all_dates = self._build_trading_dates(data, start_date, end_date)
        rebalance_dates = all_dates[::cfg.hold_days]
        logger.info(f"交易日: {len(all_dates)}天, 调仓日: {len(rebalance_dates)}次")

        # ── 逐期模拟 ──
        capital = cfg.initial_capital
        positions: dict[str, dict] = {}  # code → {shares, entry_date}
        equity_records: list[dict] = []
        all_trades: list[TradeRecord] = []
        buy_sell_pnls: list[float] = []

        for i, current_date in enumerate(all_dates):
            date_str = str(current_date)

            # 构建到当前日期为止的数据视图
            data_view: dict[str, pd.DataFrame] = {}
            for code, df in data.items():
                mask = df["date"] <= date_str
                if mask.any():
                    data_view[code] = df[mask].copy()

            # ── 调仓日：执行轮动 ──
            if date_str in [str(d) for d in rebalance_dates]:
                selected = strategy(data_view, date_str)

                # 卖出不在新组合中的持仓
                for code in list(positions.keys()):
                    if code not in selected:
                        pos = positions.pop(code)
                        price = self._get_price(data_view, code, date_str)
                        if price is None or price <= 0:
                            continue
                        value = price * pos["shares"]
                        comm = value * cfg.commission
                        capital += value - comm
                        pnl = value - pos["cost"] - comm
                        buy_sell_pnls.append(pnl)
                        all_trades.append(TradeRecord(
                            date=date_str, code=code,
                            name=self.etf_map.get(code, EtfInfo("","","")).name if hasattr(self.etf_map.get(code), "name") else code,
                            action="sell", price=price,
                            shares=pos["shares"], value=value, commission=comm,
                        ))

                # 买入新选中的ETF（等权配置）
                if selected:
                    cash_per_etf = capital / min(len(selected), cfg.top_n) * 0.98
                    for code in selected[:cfg.top_n]:
                        if code in positions:
                            continue
                        price = self._get_price(data_view, code, date_str)
                        if price is None or price <= 0:
                            continue
                        buy_value = min(cash_per_etf, capital * 0.99)
                        shares = int(buy_value / price / 100) * 100
                        if shares < 100:
                            continue
                        cost = price * shares * (1 + cfg.commission)
                        if cost > capital:
                            continue
                        capital -= cost
                        positions[code] = {
                            "shares": shares,
                            "cost": cost,
                            "entry_date": date_str,
                        }
                        all_trades.append(TradeRecord(
                            date=date_str, code=code,
                            name=self.etf_map.get(code, EtfInfo("","","")).name if hasattr(self.etf_map.get(code), "name") else code,
                            action="buy", price=price,
                            shares=shares, value=cost, commission=cfg.commission * price * shares,
                        ))

            # ── 每个交易日记录权益 ──
            market_value = 0.0
            for code, pos in list(positions.items()):
                price = self._get_price(data_view, code, date_str)
                if price:
                    market_value += price * pos["shares"]
            total_equity = capital + market_value
            equity_records.append({"date": date_str, "equity": round(total_equity, 2)})

        # ── 计算绩效指标 ──
        result = self._calc_metrics(equity_records, data, start_date, end_date)
        result.trades = all_trades
        result.total_trades = len(all_trades)

        # 胜率
        if buy_sell_pnls:
            wins = sum(1 for p in buy_sell_pnls if p > 0)
            result.win_rate = wins / len(buy_sell_pnls)

        logger.info(result.summary())
        return result

    def _get_price(self, data: dict, code: str, date_str: str) -> Optional[float]:
        if code not in data:
            return None
        df = data[code]
        row = df[df["date"] == date_str]
        if row.empty:
            # 取最近一个交易日的收盘价
            close_df = df[df["date"] <= date_str]
            if close_df.empty:
                return None
            return float(close_df["close"].iloc[-1])
        return float(row["close"].iloc[0])

    def _build_trading_dates(
        self, data: dict, start: str, end: str
    ) -> list[str]:
        """从数据中提取交易日列表。"""
        all_dates: set[str] = set()
        for code, df in data.items():
            dates = df[(df["date"] >= start) & (df["date"] <= end)]["date"].tolist()
            all_dates.update(dates)
        result = sorted(all_dates)
        return result

    def _calc_metrics(
        self, equity_records: list[dict],
        data: dict, start_date: str, end_date: str
    ) -> BacktestResult:
        eq_df = pd.DataFrame(equity_records)
        result = BacktestResult(equity_curve=eq_df)

        if eq_df.empty or len(eq_df) < 2:
            return result

        # 策略收益
        start_eq = eq_df["equity"].iloc[0]
        end_eq = eq_df["equity"].iloc[-1]
        result.total_return = (end_eq - start_eq) / start_eq

        days = len(eq_df)
        if days >= 120:
            years = days / 252
            result.annual_return = (1 + result.total_return) ** (1 / years) - 1
        else:
            result.annual_return = result.total_return

        # 夏普
        returns = eq_df["equity"].pct_change().dropna()
        if len(returns) > 1 and returns.std() > 0:
            rf_daily = 0.025 / 252
            excess = returns - rf_daily
            result.sharpe_ratio = float(excess.mean() / returns.std() * np.sqrt(252))

        # 最大回撤
        equity = eq_df["equity"].values
        peak = np.maximum.accumulate(equity)
        dd = (equity - peak) / peak
        result.max_drawdown = float(dd.min())

        # 基准收益
        if self.config.benchmark_code in data:
            bench = data[self.config.benchmark_code]
            bench_slice = bench[
                (bench["date"] >= start_date) & (bench["date"] <= end_date)
            ]
            if not bench_slice.empty:
                bench_start = float(bench_slice["close"].iloc[0])
                bench_end = float(bench_slice["close"].iloc[-1])
                if bench_start > 0:
                    result.benchmark_return = (bench_end - bench_start) / bench_start
                    if days >= 120:
                        result.benchmark_annual = (
                            (1 + result.benchmark_return) ** (1 / (days / 252)) - 1
                        )
                    else:
                        result.benchmark_annual = result.benchmark_return

        return result
