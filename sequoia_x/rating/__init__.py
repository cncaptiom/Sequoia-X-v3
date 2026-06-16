"""Sequoia-X 评级模块：全市场 A 股策略评分与评级系统。

提供 7 大策略的向量化实现和完整的评级引擎，
将 Flutter 版分析引擎移植到 pandas/numpy 生态。
"""

from sequoia_x.rating.engine import (
    AnalysisResult,
    Rating,
    RatingConfig,
    RatingEngine,
)
from sequoia_x.rating.strategies import (
    atr_channel_strategy,
    ma_cross_strategy,
    ma_volume_strategy,
    macd_rsi_strategy,
    mean_reversion_strategy,
    turtle_trade_strategy,
    volume_price_strategy,
)

__all__ = [
    "AnalysisResult",
    "Rating",
    "RatingConfig",
    "RatingEngine",
    "ma_volume_strategy",
    "turtle_trade_strategy",
    "mean_reversion_strategy",
    "volume_price_strategy",
    "atr_channel_strategy",
    "macd_rsi_strategy",
    "ma_cross_strategy",
]
