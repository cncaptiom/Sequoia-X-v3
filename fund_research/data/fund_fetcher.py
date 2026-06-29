"""基金数据获取：净值、持仓、基本信息。

数据源:
  - 天天基金/东方财富: 公募基金净值、持仓、规模
  - akshare: 备用数据源
"""

import sqlite3
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import requests

from fund_research.core.logger import get_logger
from fund_research.core.config import get_settings

logger = get_logger(__name__)


class FundFetcher:
    """基金数据获取器。"""

    EASTMONEY_FUND_LIST = (
        "http://fund.eastmoney.com/js/fundcode_search.js"
    )

    def __init__(self):
        self.settings = get_settings()
        self._init_db()

    def _init_db(self):
        import os
        os.makedirs(os.path.dirname(self.settings.db_path) or ".", exist_ok=True)
        with sqlite3.connect(self.settings.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS fund_list (
                    code TEXT PRIMARY KEY,
                    name TEXT,
                    type TEXT,
                    established_date TEXT
                );
                CREATE TABLE IF NOT EXISTS fund_nav (
                    code TEXT,
                    date TEXT,
                    nav REAL,
                    acc_nav REAL,
                    PRIMARY KEY (code, date)
                );
                CREATE TABLE IF NOT EXISTS fund_position (
                    code TEXT,
                    date TEXT,
                    stock_code TEXT,
                    stock_name TEXT,
                    ratio REAL,
                    PRIMARY KEY (code, date, stock_code)
                );
            """)
        logger.info(f"数据库初始化: {self.settings.db_path}")

    def fetch_all(self):
        """拉取全市场基金列表。"""
        logger.info("拉取基金列表...")
        # TODO: 从天天基金获取全市场基金列表
        pass

    def get_fund_list(self) -> list[dict]:
        """获取已存储的基金列表。"""
        with sqlite3.connect(self.settings.db_path) as conn:
            rows = conn.execute(
                "SELECT code, name, type FROM fund_list ORDER BY code"
            ).fetchall()
        return [{"code": r[0], "name": r[1], "type": r[2]} for r in rows]

    def fetch_nav(self, code: str, force: bool = False) -> int:
        """拉取单只基金净值。"""
        logger.info(f"拉取 {code} 净值...")
        # TODO: 从天天基金 API 获取净值数据
        return 0

    def fetch_position(self, code: str) -> int:
        """拉取单只基金持仓。"""
        logger.info(f"拉取 {code} 持仓...")
        # TODO: 从天天基金获取季度持仓
        return 0
