"""绩效归因模块：计算夏普比率、卡玛比率、最大回撤、月度收益分布等。

基于专业机构常用的绩效评估框架:
  - 夏普比率 (Sharpe, 1966)
  - 索提诺比率 (Sortino & Price, 1994)
  - 卡玛比率 (Calmar Ratio)
  - 最大回撤和回撤持续时间
  - 月度/年度收益分解
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class PerformanceMetrics:
    """综合绩效指标。"""
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_days: int = 0
    volatility: float = 0.0          # 年化波动率
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    total_trades: int = 0
    avg_hold_days: float = 0.0
    monthly_returns: pd.DataFrame = field(default_factory=pd.DataFrame)

    # 覆盖默认 field 以防止 dataclass 字段顺序问题
    def __init__(
        self,
        total_return: float = 0.0,
        annual_return: float = 0.0,
        sharpe_ratio: float = 0.0,
        sortino_ratio: float = 0.0,
        calmar_ratio: float = 0.0,
        max_drawdown: float = 0.0,
        max_drawdown_days: int = 0,
        volatility: float = 0.0,
        win_rate: float = 0.0,
        profit_factor: float = 0.0,
        avg_win: float = 0.0,
        avg_loss: float = 0.0,
        total_trades: int = 0,
        avg_hold_days: float = 0.0,
        monthly_returns: Optional[pd.DataFrame] = None,
    ):
        self.total_return = total_return
        self.annual_return = annual_return
        self.sharpe_ratio = sharpe_ratio
        self.sortino_ratio = sortino_ratio
        self.calmar_ratio = calmar_ratio
        self.max_drawdown = max_drawdown
        self.max_drawdown_days = max_drawdown_days
        self.volatility = volatility
        self.win_rate = win_rate
        self.profit_factor = profit_factor
        self.avg_win = avg_win
        self.avg_loss = avg_loss
        self.total_trades = total_trades
        self.avg_hold_days = avg_hold_days
        self.monthly_returns = monthly_returns if monthly_returns is not None else pd.DataFrame()

    @classmethod
    def from_trades(cls, trades: list, equity_curve: Optional[pd.DataFrame] = None) -> "PerformanceMetrics":
        """从交易记录列表计算所有绩效指标。

        Args:
            trades: Trade 对象列表。
            equity_curve: 可选，权益曲线 DataFrame（含 date, equity 列）。

        Returns:
            PerformanceMetrics 实例。
        """
        if not trades:
            return cls(total_trades=0)

        wins = [t.pnl for t in trades if t.pnl > 0]
        losses = [t.pnl for t in trades if t.pnl < 0]

        total_pnl = sum(t.pnl for t in trades)
        win_rate = len(wins) / len(trades) if trades else 0.0
        avg_win = np.mean(wins) if wins else 0.0
        avg_loss = abs(np.mean(losses)) if losses else 0.0

        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        hold_days = [t.hold_days for t in trades]
        avg_hold = np.mean(hold_days) if hold_days else 0.0

        # 从权益曲线计算风险指标
        sharpe = 0.0
        sortino = 0.0
        calmar = 0.0
        annual_ret = 0.0
        total_ret = 0.0
        max_dd = 0.0
        max_dd_days = 0
        volatility = 0.0
        monthly_df = pd.DataFrame()

        if equity_curve is not None and not equity_curve.empty and len(equity_curve) > 1:
            returns = equity_curve["equity"].pct_change().dropna()
            if len(returns) > 1 and returns.std() > 0:
                rf_daily = 0.03 / 252
                excess = returns - rf_daily
                sharpe = float(excess.mean() / returns.std() * np.sqrt(252))

                # Sortino: 只惩罚下行波动
                downside = returns[returns < 0]
                downside_std = downside.std() if len(downside) > 1 else returns.std()
                sortino = float(excess.mean() / downside_std * np.sqrt(252)) if downside_std > 0 else 0.0

                volatility = float(returns.std() * np.sqrt(252))

            # 最大回撤
            equity = equity_curve["equity"].values
            peak = np.maximum.accumulate(equity)
            dd = (equity - peak) / peak
            max_dd = float(dd.min())

            # 回撤持续时间
            dd_start = None
            for i, d in enumerate(dd):
                if d < 0 and dd_start is None:
                    dd_start = i
                elif d >= 0 and dd_start is not None:
                    max_dd_days = max(max_dd_days, i - dd_start)
                    dd_start = None

            # 年化收益（仅当回测期>=120个交易日时计算年化，否则直接返回区间收益）
            if "date" in equity_curve.columns:
                dates = pd.to_datetime(equity_curve["date"])
                days = (dates.iloc[-1] - dates.iloc[0]).days
            else:
                days = len(equity_curve)
            if days >= 120 and days > 0:
                years = days / 252
                total_ret = (equity_curve["equity"].iloc[-1] - equity_curve["equity"].iloc[0]) / equity_curve["equity"].iloc[0]
                annual_ret = float((1 + total_ret) ** (1 / years) - 1)
            else:
                # 短周期直接显示区间总收益
                total_ret = (equity_curve["equity"].iloc[-1] - equity_curve["equity"].iloc[0]) / equity_curve["equity"].iloc[0]
                annual_ret = total_ret  # 短周期不年化

            # 卡玛比率
            calmar = annual_ret / abs(max_dd) if max_dd != 0 else 0.0

            # 月度收益
            if "date" in equity_curve.columns:
                eq = equity_curve.copy()
                eq["date"] = pd.to_datetime(eq["date"])
                eq.set_index("date", inplace=True)
                monthly = eq["equity"].resample("ME").last().pct_change().dropna()
                monthly_df = monthly.to_frame("return")

        return cls(
            total_return=total_ret,
            annual_return=annual_ret,
            sharpe_ratio=round(sharpe, 3),
            sortino_ratio=round(sortino, 3),
            calmar_ratio=round(calmar, 3),
            max_drawdown=round(max_dd, 4),
            max_drawdown_days=max_dd_days,
            volatility=round(volatility, 4),
            win_rate=round(win_rate, 4),
            profit_factor=round(profit_factor, 2) if profit_factor != float("inf") else 999.0,
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            total_trades=len(trades),
            avg_hold_days=round(avg_hold, 1),
            monthly_returns=monthly_df,
        )

    def summary(self) -> str:
        """生成单行摘要字符串。"""
        if self.total_trades == 0:
            return "无交易记录"

        parts = [
            f"交易{self.total_trades}笔",
            f"胜率{self.win_rate:.1%}",
            f"盈亏比{self.profit_factor:.1f}",
            f"夏普{self.sharpe_ratio:.2f}",
            f"最大回撤{self.max_drawdown:.1%}",
            f"年化收益{self.annual_return:.1%}",
        ]
        return " | ".join(parts)

    def to_dict(self) -> dict:
        """转为字典，便于 JSON 序列化。"""
        return {
            "total_return": self.total_return,
            "annual_return": self.annual_return,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "calmar_ratio": self.calmar_ratio,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_days": self.max_drawdown_days,
            "volatility": self.volatility,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "total_trades": self.total_trades,
            "avg_hold_days": self.avg_hold_days,
        }

    def comparison_table(self, benchmark_metrics: "PerformanceMetrics") -> str:
        """生成与基准的对比表（文本格式）。"""
        header = f"{'指标':<16} {'策略':>12} {'基准':>12} {'差异':>12}"
        sep = "-" * 52
        rows = [header, sep]

        comparisons = [
            ("年化收益率", self.annual_return, benchmark_metrics.annual_return, ".1%"),
            ("夏普比率", self.sharpe_ratio, benchmark_metrics.sharpe_ratio, ".2f"),
            ("最大回撤", self.max_drawdown, benchmark_metrics.max_drawdown, ".1%"),
            ("波动率", self.volatility, benchmark_metrics.volatility, ".1%"),
            ("胜率", self.win_rate, benchmark_metrics.win_rate, ".1%"),
            ("盈亏比", self.profit_factor, benchmark_metrics.profit_factor, ".1f"),
            ("交易笔数", float(self.total_trades), float(benchmark_metrics.total_trades), ".0f"),
        ]

        for name, s, b, fmt in comparisons:
            diff = s - b
            s_str = f"{s:{fmt}}"
            b_str = f"{b:{fmt}}" if not (isinstance(b, float) and b == float("inf")) else "N/A"
            d_str = f"{diff:{fmt}}" if not (isinstance(diff, float) and (diff == float("inf") or diff == -float("inf"))) else "N/A"
            rows.append(f"{name:<16} {s_str:>12} {b_str:>12} {d_str:>12}")

        return "\n".join(rows)
