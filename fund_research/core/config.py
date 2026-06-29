"""全局配置。"""

from dataclasses import dataclass
from typing import Optional
import os


@dataclass
class Settings:
    """基金分析系统配置。"""

    # 数据库
    db_path: str = os.getenv("FUND_DB_PATH", "data/funds.db")

    # 数据源
    fund_source: str = os.getenv("FUND_SOURCE", "eastmoney")  # eastmoney / akshare

    # 回测
    initial_capital: float = 1_000_000
    commission: float = 0.001  # ETF 万十
    max_positions: int = 5

    # 分析
    risk_free_rate: float = 0.025
    benchmark: str = "000300"  # 沪深300


def get_settings() -> Settings:
    """获取全局配置单例。"""
    return Settings()
