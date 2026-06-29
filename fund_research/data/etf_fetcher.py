"""ETF行情数据获取。

数据源：复用 Sequoia-X 的 mootdx（通达信TCP协议）获取日K线。
ETF在交易所和股票同一通道交易，K线获取方式完全相同。
"""

import sqlite3
import os
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from fund_research.core.logger import get_logger
from fund_research.core.config import get_settings
from fund_research.data.etf_list import ETF_POOL, get_etf_codes, get_etf_map

logger = get_logger(__name__)

# ── SQLite 表结构 ──

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS etf_daily (
    code TEXT,
    date TEXT,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    turnover REAL,
    PRIMARY KEY (code, date)
);
"""


class EtfDataFetcher:
    """ETF 行情获取与存储。"""

    def __init__(self):
        self.settings = get_settings()
        self._init_db()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.settings.db_path) or ".", exist_ok=True)
        with sqlite3.connect(self.settings.db_path) as conn:
            conn.executescript(SCHEMA_SQL)
        logger.info(f"ETF数据库: {self.settings.db_path}")

    # ── 数据获取 ──

    def fetch_all(self, max_workers: int = 4) -> int:
        """全量拉取所有ETF的历史日K线。"""
        etf_map = get_etf_map()
        codes = get_etf_codes()
        total = 0

        for i, code in enumerate(codes):
            info = etf_map[code]
            try:
                count = self._fetch_single(code)
                total += count
                if (i + 1) % 5 == 0:
                    logger.info(f"[{i+1}/{len(codes)}] {info.name}({code}) 写入{count}条")
            except Exception as exc:
                logger.warning(f"[{code}] {info.name} 获取失败: {exc}")

        logger.info(f"全量拉取完成: {len(codes)}只ETF, 共{total}行")
        return total

    def fetch_recent(self, days: int = 5) -> int:
        """增量拉取最近N天数据。"""
        etf_map = get_etf_map()
        codes = get_etf_codes()
        total = 0

        for code in codes:
            last = self._get_last_date(code)
            start = date.today() - timedelta(days=days)
            if last and last >= str(start):
                continue
            try:
                count = self._fetch_single(code)
                total += count
            except Exception:
                continue

        logger.info(f"增量拉取完成: {total}条")
        return total

    def _fetch_single(self, code: str) -> int:
        """拉取单只ETF日K线（复用 mootdx）。"""
        try:
            from mootdx.quotes import Quotes
            client = Quotes.factory(market='std')
            df = client.bars(symbol=code, frequency=9, start=0, count=800)
            if df is None or df.empty:
                return 0
        except Exception:
            # mootdx 失败时尝试 baostock
            return self._fetch_from_baostock(code)

        df["date"] = pd.to_datetime(df["datetime"]).dt.date.astype(str)
        df = df.dropna(subset=["date"])

        rows = []
        for _, row in df.iterrows():
            rows.append([
                code, str(row["date"]),
                float(row.get("open", 0)),
                float(row.get("high", 0)),
                float(row.get("low", 0)),
                float(row.get("close", 0)),
                float(row.get("volume", 0)) * 100,  # 手→股
                float(row.get("amount", 0)),
            ])

        if not rows:
            return 0

        batch = pd.DataFrame(rows, columns=[
            "code", "date", "open", "high", "low", "close", "volume", "turnover"
        ])
        for col in ["open", "high", "low", "close", "volume", "turnover"]:
            batch[col] = pd.to_numeric(batch[col], errors="coerce")
        batch = batch.dropna(subset=["close"])
        batch = batch[batch["volume"] > 0]

        self._write_batch(batch)
        return len(batch)

    def _fetch_from_baostock(self, code: str) -> int:
        """从 baostock 获取ETF K线（备选）。"""
        try:
            import baostock as bs
            bs.login()
            rs = bs.query_history_k_data_plus(
                code, "date,open,high,low,close,volume,amount",
                start_date="2010-01-01",
                end_date=date.today().strftime("%Y-%m-%d"),
                frequency="d", adjustflag="2"  # 前复权
            )
            rows = []
            while rs.next():
                row = rs.get_row_data()
                rows.append(row)
            bs.logout()

            if not rows:
                return 0

            df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "turnover"])
            df["code"] = code
            for c in ["open", "high", "low", "close", "volume", "turnover"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df = df.dropna(subset=["close"])
            df = df[df["volume"] > 0]

            self._write_batch(df)
            return len(df)
        except Exception:
            return 0

    def _write_batch(self, df: pd.DataFrame):
        """批量写入数据库。"""
        with sqlite3.connect(self.settings.db_path) as conn:
            for d in df["date"].unique():
                codes = df.loc[df["date"] == d, "code"].tolist()
                if codes:
                    ph = ",".join(["?"] * len(codes))
                    conn.execute(
                        f"DELETE FROM etf_daily WHERE date = ? AND code IN ({ph})",
                        [d] + codes
                    )
            df.to_sql("etf_daily", conn, if_exists="append", index=False)
            conn.commit()

    # ── 数据读取 ──

    def _get_last_date(self, code: str) -> Optional[str]:
        with sqlite3.connect(self.settings.db_path) as conn:
            row = conn.execute(
                "SELECT MAX(date) FROM etf_daily WHERE code = ?", (code,)
            ).fetchone()
        return row[0] if row and row[0] else None

    def get_ohlcv(self, code: str) -> pd.DataFrame:
        """获取单只ETF的OHLCV数据。"""
        with sqlite3.connect(self.settings.db_path) as conn:
            df = pd.read_sql(
                "SELECT date, open, high, low, close, volume, turnover "
                "FROM etf_daily WHERE code = ? ORDER BY date", conn, params=(code,)
            )
        return df

    def get_all_ohlcv(self) -> dict[str, pd.DataFrame]:
        """获取所有ETF的OHLCV数据。"""
        etf_map = get_etf_map()
        result = {}
        for code in etf_map:
            df = self.get_ohlcv(code)
            if not df.empty:
                result[code] = df
        return result

    def get_latest_date(self) -> Optional[str]:
        """获取数据库中最新的交易日。"""
        with sqlite3.connect(self.settings.db_path) as conn:
            row = conn.execute("SELECT MAX(date) FROM etf_daily").fetchone()
        return row[0] if row else None

    def status(self) -> dict:
        """数据库状态报告。"""
        with sqlite3.connect(self.settings.db_path) as conn:
            total_etfs = conn.execute("SELECT COUNT(DISTINCT code) FROM etf_daily").fetchone()[0]
            total_rows = conn.execute("SELECT COUNT(*) FROM etf_daily").fetchone()[0]
            dr = conn.execute("SELECT MIN(date), MAX(date) FROM etf_daily").fetchone()
        return {
            "etf_count": total_etfs,
            "rows": total_rows,
            "date_range": f"{dr[0]}~{dr[1]}" if dr else "无数据",
        }
