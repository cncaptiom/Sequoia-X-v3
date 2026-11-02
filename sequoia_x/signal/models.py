"""交易信号数据模型：定义结构化买卖建议的数据结构。"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TradeSignal:
    """单只股票的完整交易信号。

    包含买入区间、止损止盈、仓位建议、持仓周期等
    可直接用于生成用户可执行的交易建议。
    """

    # 基础信息
    symbol: str                           # 股票代码，如 "600519"
    name: str = ""                        # 股票名称
    strategies: list[str] = field(default_factory=list)  # 触发的策略列表

    # 当前行情
    current_price: float = 0.0            # 最新收盘价
    last_date: str = ""                   # 数据日期

    # 买入建议
    entry_zone_low: float = 0.0           # 买入区间下限
    entry_zone_high: float = 0.0          # 买入区间上限
    entry_condition: str = ""             # 触发买入的条件描述

    # 卖出/止损建议
    stop_loss: float = 0.0                # 止损价
    take_profit_1: float = 0.0            # 第一目标价
    take_profit_2: Optional[float] = None # 第二目标价（可选）
    max_hold_days: int = 20               # 最长持有天数（交易日）

    # 仓位管理
    suggested_position_pct: float = 0.0   # 建议仓位占比（占资金比例，如 0.08 = 8%）
    atr_value: Optional[float] = None     # 当前 ATR 值（用于评估波动风险）

    # 评级（选股依据；--daily 路径不产生评级，留空）
    rating: str = ""                      # strong_buy/buy/watch/attention/avoid
    rating_label: str = ""                # 展示用标签（S级/A级/B级/C级/回避）
    rating_stars: str = ""                # ★~★★★★★
    total_score: float = 0.0              # 截面 Z 合成总分

    # 形态分（解释性指标，不参与选股排序）
    confidence: float = 0.0               # 综合形态分 0~1
    score_details: dict[str, float] = field(default_factory=dict)  # 各维度细分得分

    # 市场环境
    market_regime: str = ""               # 当前市场状态（强牛/弱牛/震荡/弱熊/强熊）

    # 选中理由（用户可读）
    rationale: str = ""                   # 选中理由概述

    def summary(self) -> str:
        """生成一行摘要。"""
        parts = [
            f"{self.name}({self.symbol})",
            f"策略:{','.join(self.strategies)}",
            f"买入:{self.entry_zone_low:.2f}~{self.entry_zone_high:.2f}",
            f"止损:{self.stop_loss:.2f}",
            f"目标1:{self.take_profit_1:.2f}",
        ]
        if self.take_profit_2:
            parts.append(f"目标2:{self.take_profit_2:.2f}")
        parts.append(f"仓位:{self.suggested_position_pct:.0%}")
        if self.rating_label:
            parts.append(f"评级:{self.rating_label}")
        parts.append(f"形态分:{self.confidence:.0%}")
        parts.append(self.rationale[:30])
        return " | ".join(parts)

    def risk_reward_ratio(self) -> float:
        """计算盈亏比 = (目标价 - 买入价) / (买入价 - 止损价)。"""
        entry_mid = (self.entry_zone_low + self.entry_zone_high) / 2
        if entry_mid <= 0:
            return 0.0
        reward = self.take_profit_1 - entry_mid
        risk = entry_mid - self.stop_loss
        if risk <= 0:
            return 99.0
        return round(reward / risk, 2)


@dataclass
class SignalResult:
    """每日推荐报告：包含所有推荐股票和元信息。"""

    signals: list[TradeSignal] = field(default_factory=list)
    total_scanned: int = 0                # 扫描股票总数
    market_regime: str = ""               # 市场状态
    suggested_total_exposure: float = 0.0 # 建议总仓位
    generated_at: str = ""                # 生成时间
    rating_summary: dict[str, int] = field(default_factory=dict)  # 评级分布

    @property
    def top_signals(self) -> list[TradeSignal]:
        """按形态分降序排列。"""
        return sorted(self.signals, key=lambda x: x.confidence, reverse=True)

    def top_n(self, n: int = 10) -> list[TradeSignal]:
        """获取形态分最高的前 N 个信号。"""
        return self.top_signals[:n]
