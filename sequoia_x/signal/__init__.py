"""交易信号模块：定义结构化买卖信号和推荐报告生成。"""

from sequoia_x.signal.models import TradeSignal, SignalResult
from sequoia_x.signal.generator import SignalGenerator

__all__ = [
    "TradeSignal",
    "SignalResult",
    "SignalGenerator",
]
