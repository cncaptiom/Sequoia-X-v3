"""风险管理模块：ATR 动态仓位、组合风险预算、VaR 估算。

参考:
  - 海龟交易 ATR 仓位管理 (Dennis & Eckhardt)
  - Kelly 公式 (Kelly, 1956) 的分数版本
  - 风险平价 (Bridgewater 全天候框架, Dalio)
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)


# ── ATR 计算 ──

def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    """计算 Average True Range。

    ATR 是海龟交易仓位管理的核心参数：
      仓位大小 = 账户风险预算 / (ATR × 合约乘数)

    Args:
        high, low, close: 价格序列。
        period: ATR 周期，默认 14。
    """
    if len(close) < period + 1:
        return float(close.iloc[-1] * 0.03)

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    return float(tr.tail(period).mean())


# ── 仓位计算 ──

@dataclass
class PositionSize:
    """单票仓位计算结果。"""
    symbol: str
    atr: float                       # 当前 ATR 值
    unit_size: int                   # 建议股数（取整到 100 股）
    position_pct: float              # 占资金百分比
    stop_loss_price: float           # 基于 ATR 的止损价
    risk_amount: float               # 本笔交易风险金额


class PositionSizer:
    """ATR 动态仓位管理器。

    核心公式:
      止损距离 = 2 × ATR
      仓位大小 = 账户资金 × 单笔风险% / 止损距离
    """

    def __init__(
        self,
        total_capital: float,
        risk_per_trade: float = 0.02,   # 单笔最大亏损占资金 2%
        atr_multiplier: float = 2.0,    # 止损 = ATR × 倍数
        max_position_pct: float = 0.15, # 单票仓位上限 15%
    ):
        self.total_capital = total_capital
        self.risk_per_trade = risk_per_trade
        self.atr_multiplier = atr_multiplier
        self.max_position_pct = max_position_pct

    def calculate(
        self,
        symbol: str,
        df: pd.DataFrame,
        entry_price: Optional[float] = None,
    ) -> PositionSize:
        """基于 ATR 计算单票合理仓位。

        Args:
            symbol: 股票代码。
            df: OHLCV DataFrame。
            entry_price: 入场价（默认取最新收盘价）。

        Returns:
            PositionSize 含建议股数和止损价。
        """
        if entry_price is None:
            entry_price = float(df["close"].iloc[-1])

        atr = compute_atr(df["high"], df["low"], df["close"])

        if atr <= 0 or entry_price <= 0:
            atr = entry_price * 0.03

        # 止损价格
        stop_loss_price = entry_price - self.atr_multiplier * atr

        # 每股风险 = entry - stop_loss
        risk_per_share = entry_price - stop_loss_price
        if risk_per_share <= 0:
            risk_per_share = entry_price * 0.01

        # 总风险金额
        max_risk_amount = self.total_capital * self.risk_per_trade

        # 理论股数
        raw_shares = max_risk_amount / risk_per_share

        # 上限约束：不超过 max_position_pct 资金
        max_shares_by_pct = self.total_capital * self.max_position_pct / entry_price
        shares = min(raw_shares, max_shares_by_pct)

        # 取整到 100 股（A 股最小交易单位）
        unit_size = max(int(shares / 100) * 100, 100)

        position_value = unit_size * entry_price
        position_pct = position_value / self.total_capital

        return PositionSize(
            symbol=symbol,
            atr=round(atr, 2),
            unit_size=unit_size,
            position_pct=round(position_pct, 4),
            stop_loss_price=round(stop_loss_price, 2),
            risk_amount=round(unit_size * risk_per_share, 2),
        )


# ── Kelly 公式 ──

def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float, fraction: float = 0.5) -> float:
    """分数 Kelly 仓位计算。

    Kelly 公式: f* = (p * b - q) / b
      其中 p = 胜率, q = 1-p, b = 平均盈利/平均亏损

    分数 Kelly (0.5 = 半凯利) 降低波动，更保守。

    Returns:
        建议仓位比例 (0~1)。
    """
    if avg_loss == 0 or win_rate <= 0:
        return 0.0

    b = avg_win / avg_loss
    q = 1 - win_rate

    kelly = (win_rate * b - q) / b
    kelly = max(0.0, kelly)

    return kelly * fraction


# ── 组合风险约束 ──

@dataclass
class PortfolioRisk:
    """组合风险指标。"""
    total_exposure: float = 0.0               # 总仓位
    var_95: float = 0.0                       # 95% VaR
    cvar_95: float = 0.0                      # 95% CVaR (Expected Shortfall)
    max_single_position: float = 0.0          # 最大单票仓位
    max_sector_exposure: float = 0.0          # 最大行业暴露
    correlation_avg: float = 0.0              # 持仓间平均相关性


class PortfolioRiskManager:
    """组合层面风险管理。

    约束条件（机构标准）:
      - 单票仓位 ≤ 15%
      - 单行业仓位 ≤ 30%
      - 总仓位根据市场状态动态调整 (50%~100%)
      - VaR 限额
    """

    def __init__(
        self,
        max_single_position: float = 0.15,
        max_sector_exposure: float = 0.30,
        max_total_exposure: float = 1.0,
    ):
        self.max_single_position = max_single_position
        self.max_sector_exposure = max_sector_exposure
        self.max_total_exposure = max_total_exposure

    def check_position(
        self,
        current_positions: dict[str, float],
        new_symbol: str,
        new_pct: float,
    ) -> tuple[bool, str]:
        """检查新增仓位是否违反组合约束。

        Returns:
            (是否通过, 拒绝原因)。
        """
        total = sum(current_positions.values()) + new_pct
        if total > self.max_total_exposure:
            return False, f"总仓位{total:.1%}超限({self.max_total_exposure:.0%})"

        if new_pct > self.max_single_position:
            return False, f"单票仓位{new_pct:.1%}超限({self.max_single_position:.0%})"

        return True, "通过"

    @staticmethod
    def compute_var(returns: np.ndarray, confidence: float = 0.95) -> tuple[float, float]:
        """历史模拟法计算 VaR 和 CVaR。

        Args:
            returns: 日收益率序列。
            confidence: 置信水平。

        Returns:
            (VaR, CVaR)。
        """
        if len(returns) < 20:
            return 0.0, 0.0
        sorted_returns = np.sort(returns)
        var_idx = int(len(sorted_returns) * (1 - confidence))
        var_val = abs(sorted_returns[var_idx])
        cvar_val = abs(sorted_returns[:var_idx].mean()) if var_idx > 0 else var_val
        return float(var_val), float(cvar_val)
