"""策略基类模块：定义所有选股策略的抽象接口。"""

from abc import ABC, abstractmethod
from typing import Optional
import pandas as pd

from sequoia_x.core.config import Settings
from sequoia_x.data.engine import DataEngine


class BaseStrategy(ABC):
    """选股策略抽象基类。

    所有具体策略必须继承此类并实现 run() 方法。

    Attributes:
        webhook_key: 策略对应的飞书 webhook 标识，用于路由到不同机器人。
            默认为 'default'，将使用 Settings.feishu_webhook_url。
            子类可覆盖此属性以路由到专属机器人，例如 'ma_volume'。
    """

    webhook_key: str = "default"

    def __init__(self, engine: DataEngine, settings: Settings) -> None:
        """
        初始化策略。

        Args:
            engine: DataEngine 实例，用于读取行情数据。
            settings: Settings 实例，用于读取配置。
        """
        self.engine = engine
        self.settings = settings
        self._preloaded: Optional[dict[str, pd.DataFrame]] = None
        # 回测参考日期：在回测模式下由 BacktestEngine 设置，
        # 用于新鲜度检查替代 date.today()。None 表示使用实际今天。
        self.reference_date: Optional[str] = None

    @property
    def _today(self):
        """返回参考日期（回测模式）或实际今天（日常模式）。"""
        from datetime import date
        if self.reference_date:
            return date.fromisoformat(self.reference_date)
        return date.today()

    def preload(self, data: dict[str, pd.DataFrame]) -> None:
        """注入预加载的全市场数据（回测加速）。向策略注入数据后，get_data() 优先走缓存。"""
        self._preloaded = data

    def get_data(self, symbol: str) -> pd.DataFrame:
        """获取股票数据：优先预加载缓存，回退到引擎。

        回测模式下（设置了 reference_date），自动将数据切片到该日期，
        防止未来信息泄漏。
        """
        if self._preloaded and symbol in self._preloaded:
            df = self._preloaded[symbol]
            if self.reference_date is not None:
                df = df[df["date"] <= self.reference_date]
            return df
        return self.engine.get_ohlcv(symbol)

    @abstractmethod
    def run(self) -> list[str]:
        """
        执行选股逻辑，返回选中的股票代码列表。

        Returns:
            满足策略条件的股票代码列表，如 ['000001', '600519']。
            无选股结果时返回空列表。
        """
        ...
