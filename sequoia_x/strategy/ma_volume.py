"""均线+成交量选股策略：5日均线上穿20日均线且成交量放大。"""

from datetime import date

import pandas as pd

from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy

logger = get_logger(__name__)


class MaVolumeStrategy(BaseStrategy):
    """均线+成交量选股策略。

    选股条件（全部向量化，严禁 iterrows）：
    1. 5日收盘均线上穿20日收盘均线（金叉）
    2. 当日成交量 > 20日均量的 1.5 倍（放量确认）

    Attributes:
        webhook_key: 路由到 'ma_volume' 专属飞书机器人。
    """

    webhook_key: str = "ma_volume"

    def run(self) -> list[str]:
        """
        遍历全市场，返回满足均线金叉+放量条件的股票代码列表。

        Returns:
            满足条件的股票代码列表。
        """
        symbols = self.engine.get_local_symbols()
        selected: list[str] = []

        for symbol in symbols:
            try:
                df = self.get_data(symbol)
                if len(df) < 20:
                    continue

                # 数据新鲜度：跳过超过5个自然日未更新的股票
                last_date_str = str(df["date"].iloc[-1])
                if (self._today - date.fromisoformat(last_date_str)).days > 5:
                    continue

                # 向量化计算均线和成交量均值
                short = self.settings.ma_volume_short
                long = self.settings.ma_volume_long
                threshold = self.settings.ma_volume_threshold
                df["ma5"] = df["close"].rolling(short).mean()
                df["ma20"] = df["close"].rolling(long).mean()
                df["vol_ma20"] = df["volume"].rolling(long).mean()

                # 取最后两行判断金叉（昨日 ma5 < ma20，今日 ma5 > ma20）
                last = df.iloc[-1]
                prev = df.iloc[-2]

                golden_cross = (
                    prev["ma5"] < prev["ma20"]
                    and last["ma5"] > last["ma20"]
                )
                volume_surge = last["volume"] > last["vol_ma20"] * threshold

                if golden_cross and volume_surge:
                    selected.append(symbol)

            except Exception as exc:
                logger.warning(f"[{symbol}] 策略计算失败：{exc}")
                continue

        logger.info(f"MaVolumeStrategy 选出 {len(selected)} 只股票")
        return selected
