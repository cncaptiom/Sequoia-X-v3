import pandas as pd
from datetime import date, timedelta

from sequoia_x.strategy.base import BaseStrategy
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)


class RpsBreakoutStrategy(BaseStrategy):
    """RPS 极强动量突破策略（欧奈尔相对强度法）。

    改进：不再全表扫描，改为只加载最近 180 个交易日数据，
    大幅减少内存占用和查询时间。
    """

    webhook_key: str = "rps"
    rps_period: int = 120
    rps_threshold: int = 90

    # 加载数据的天数缓冲（120 + 60 天冗余）
    DATA_BUFFER_DAYS: int = 180

    def run(self) -> list[str]:
        try:
            latest = self.engine.get_latest_trading_date()
            if not latest:
                logger.error("数据库无数据")
                return []

            # 计算起始日期：只加载最近 DATA_BUFFER_DAYS 天的数据
            from datetime import datetime
            latest_dt = datetime.strptime(latest, "%Y-%m-%d")
            start_dt = latest_dt - timedelta(days=self.DATA_BUFFER_DAYS)
            start_date = start_dt.strftime("%Y-%m-%d")

            bulk = self.engine.get_bulk_ohlcv_date_range(
                start_date=start_date, end_date=latest
            )
            if not bulk:
                logger.warning("无数据")
                return []

        except Exception as exc:
            logger.error(f"读取数据库失败: {exc}")
            return []

        # 合并所有股票数据，只取需要字段
        records = []
        for symbol, df in bulk.items():
            if df.empty:
                continue
            for _, row in df.iterrows():
                records.append({
                    "symbol": symbol,
                    "date": row["date"],
                    "close": float(row["close"]),
                    "high": float(row["high"]),
                })

        if not records:
            return []

        df = pd.DataFrame(records)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values(['symbol', 'date'])

        # 纵向计算涨幅
        df['close_shift'] = df.groupby('symbol')['close'].shift(self.rps_period)
        df['pct_change'] = (df['close'] - df['close_shift']) / df['close_shift']

        latest_date = df['date'].max()
        latest_df = df[df['date'] == latest_date].copy()
        latest_df = latest_df.dropna(subset=['pct_change'])

        if latest_df.empty:
            logger.warning("无最新交易日数据")
            return []

        # 横向排位 (RPS)
        latest_df['rps'] = latest_df['pct_change'].rank(pct=True) * 100
        strong_stocks = latest_df[latest_df['rps'] >= self.rps_threshold].copy()

        # 计算滚动最高价
        roll_high = df.groupby('symbol')['high'].rolling(
            window=self.rps_period, min_periods=self.rps_period // 2
        ).max().reset_index(level=0, drop=True)
        df['roll_high'] = roll_high

        latest_roll_high = df[df['date'] == latest_date][['symbol', 'roll_high']]
        strong_stocks = strong_stocks.merge(latest_roll_high, on='symbol')

        # 突破判定
        breakout_condition = strong_stocks['close'] >= strong_stocks['roll_high'] * 0.90
        selected = strong_stocks[breakout_condition]

        logger.info(f"RpsBreakoutStrategy 选出 {len(selected)} 只 (基于最近{self.DATA_BUFFER_DAYS}天数据)")
        return selected['symbol'].tolist()
