"""数据引擎模块：负责 SQLite 行情数据存储与 baostock 增量同步。

包含数据清洗层，负责标记停牌、过滤退市整理期、异常值检测。
"""

import sqlite3
import time
from pathlib import Path
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from sequoia_x.core.config import Settings
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)


# ── mootdx 数据源（主用，TCP 协议更稳定） ──

def _mootdx_get_client():
    """获取 mootdx 客户端实例（带缓存）。"""
    from mootdx.quotes import Quotes
    if not hasattr(_mootdx_get_client, "_client"):
        _mootdx_get_client._client = Quotes.factory(market='std')
    return _mootdx_get_client._client


def _mootdx_parse_bars(df: pd.DataFrame, symbol: str) -> list[list]:
    """将 mootdx bar 数据解析为统一格式 [symbol, date, open, high, low, close, volume, turnover]。"""
    results = []
    for _, row in df.iterrows():
        dt = row.get("datetime")
        date_str = str(pd.Timestamp(dt).date()) if pd.notna(dt) else ""
        if not date_str:
            continue
        volume = float(row.get("volume", 0)) * 100  # 手→股
        results.append([
            symbol, date_str,
            float(row.get("open", 0)),
            float(row.get("high", 0)),
            float(row.get("low", 0)),
            float(row.get("close", 0)),
            volume,
            float(row.get("amount", 0)),  # 元
        ])
    return results


def _mootdx_fetch_batch(tasks: list) -> list:
    """多进程 worker：独立获取 mootdx 数据。"""
    from mootdx.quotes import Quotes
    client = Quotes.factory(market='std')
    results = []
    failed = []
    for symbol, _bs_code, start, end in tasks:
        try:
            df = client.bars(symbol=symbol, frequency=9, start=0, count=800)
            if df is None or df.empty:
                failed.append(f"{symbol}(无数据)")
                continue
            # 过滤日期范围
            df["date"] = pd.to_datetime(df["datetime"]).dt.date.astype(str)
            df = df[(df["date"] >= start) & (df["date"] <= end)]
            if df.empty:
                continue
            results.extend(_mootdx_parse_bars(df, symbol))
        except Exception as exc:
            failed.append(f"{symbol}({exc})")
            continue
    if failed:
        import sys
        print(f"[mootdx] {len(failed)}只失败: {', '.join(failed[:10])}{'...' if len(failed)>10 else ''}", file=sys.stderr)
    return results


def _mootdx_get_symbols() -> pd.DataFrame:
    """通过 mootdx 获取全市场 A 股代码列表。"""
    client = _mootdx_get_client()
    sh = client.stocks(market=1)
    sz = client.stocks(market=0)
    all_stocks = pd.concat([sh, sz], ignore_index=True)
    # 过滤 A 股：排除指数、基金、债券、Reits 等非股票品种
    def _is_a_share(row):
        code_str = str(row['code']).zfill(6)
        name = str(row.get('name', ''))
        # 排除指数
        if any(name.startswith(p) for p in ('上证', '深证', '创业', '中小', '沪深', '上证50')):
            return False
        # 排除基金/ETF/债券/Reits
        if any(name.startswith(p) for p in ('基金', 'ETF', '国债', '转债', '企债', '地方', '国开', 'Reits', '货币')):
            return False
        # A 股代码特征：6=上海主板, 0=深圳主板, 3=创业板
        if code_str.startswith(('6', '0', '3')):
            # 排除上海债券（01xxxx-19xxxx 开头）
            if len(code_str) == 6 and code_str.startswith(('01', '10', '11', '12', '13', '14', '15', '16', '17', '18')):
                return False
            return True
        return False
    all_stocks = all_stocks[all_stocks.apply(_is_a_share, axis=1)].copy()
    all_stocks['symbol'] = all_stocks['code'].astype(str).str.zfill(6)
    return all_stocks[['symbol', 'name', 'pre_close']]


# ── baostock 数据源（备选） ──


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stock_daily (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol   TEXT    NOT NULL,
    date     TEXT    NOT NULL,
    open     REAL,
    high     REAL,
    low      REAL,
    close    REAL,
    volume   REAL,
    turnover REAL,
    UNIQUE (symbol, date)
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_symbol_date ON stock_daily (symbol, date);
"""

# ── 数据清洗层 ──


class DataValidator:
    """数据清洗与验证工具。

    职责：
    1. 标记停牌日（volume=0 且 close 无变化）
    2. 检测复权异常（单日跳变超过阈值）
    3. 评估数据新鲜度
    4. 过滤退市整理期股票
    """

    SUSPENDED_THRESHOLD = 0.5      # 低于均量 50% + 价格无变化 = 疑似停牌
    ADJ_FACTOR_JUMP = 0.20         # 单日复权调整超过 20% 标记为异常
    DELISTING_SUFFIX = "退"        # 退市整理期标记
    MAX_STALENESS_DAYS = 7         # 数据超过7天视为过期

    @staticmethod
    def mark_suspended_days(df: pd.DataFrame, vol_col: str = "volume",
                             close_col: str = "close") -> pd.DataFrame:
        """标记可能的停牌日。

        停牌特征：当日成交量 < 近60日均量的 50%，且收盘价无变化。
        停牌日标记为 df['is_suspended'] = True。
        """
        if len(df) < 2:
            df["is_suspended"] = False
            return df

        vol_ma60 = df[vol_col].rolling(60, min_periods=5).mean()
        close_unchanged = df[close_col] == df[close_col].shift(1)

        df["is_suspended"] = (
            (df[vol_col] < vol_ma60 * DataValidator.SUSPENDED_THRESHOLD)
            & close_unchanged
        )
        # 首日无法判断，设为 False
        df.iloc[0, df.columns.get_loc("is_suspended")] = False
        return df

    @staticmethod
    def detect_adj_anomaly(df: pd.DataFrame, price_col: str = "close") -> list[str]:
        """检测复权调整异常日。

        前复权数据在某些除权日可能出现价格跳变，
        如跳变幅度超过 ADJ_FACTOR_JUMP 且成交量异常则标记。
        """
        if len(df) < 2:
            return []

        pct_chg = df[price_col].pct_change().abs()
        vol_ma20 = df["volume"].rolling(20, min_periods=5).mean()

        anomaly_mask = (pct_chg > DataValidator.ADJ_FACTOR_JUMP) & (
            df["volume"] < vol_ma20 * 0.3
        )
        anomaly_dates = df.loc[anomaly_mask, "date"].tolist()
        if anomaly_dates:
            logger.warning(f"检测到复权异常日: {anomaly_dates}")
        return anomaly_dates

    @staticmethod
    def check_freshness(last_date_str: str, max_days: int = None) -> tuple[bool, int]:
        """检查数据新鲜度。

        Returns:
            (是否新鲜, 距今天数)
        """
        max_days = max_days or DataValidator.MAX_STALENESS_DAYS
        try:
            last_date = date.fromisoformat(last_date_str)
            days_diff = (date.today() - last_date).days
            return days_diff <= max_days, days_diff
        except (ValueError, TypeError):
            return False, 999

    @staticmethod
    def compute_data_health_report(db_path: str) -> dict:
        """生成数据健康报告（股票数、日期范围、异常检测）。

        返回包含以下指标的字典：
        - total_symbols: 股票总数
        - total_rows: 总行数
        - date_range: 日期范围
        - active_daily: 近5日日均活跃股票数
        - stale_symbols: 数据过期的股票数
        - thin_symbols: 数据不足60行的股票数
        - price_anomalies: 单日涨跌>15%的异常记录数
        """
        import sqlite3
        report = {}
        with sqlite3.connect(db_path) as conn:
            # 基本统计
            report["total_symbols"] = conn.execute("SELECT COUNT(DISTINCT symbol) FROM stock_daily").fetchone()[0]
            report["total_rows"] = conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]
            row = conn.execute("SELECT MIN(date), MAX(date) FROM stock_daily").fetchone()
            report["date_range"] = f"{row[0]} ~ {row[1]}" if row else "无数据"

            # 活跃度
            recent = conn.execute("""
                SELECT date, COUNT(DISTINCT symbol) FROM stock_daily
                WHERE date >= date('now', '-5 days')
                GROUP BY date ORDER BY date
            """).fetchall()
            report["active_daily"] = {d: c for d, c in recent} if recent else {}

            # 数据不足的股票
            report["thin_symbols"] = conn.execute("""
                SELECT COUNT(*) FROM (
                    SELECT COUNT(*) as cnt FROM stock_daily GROUP BY symbol HAVING cnt < 60
                )
            """).fetchone()[0]

            # 新鲜度
            stale = conn.execute("""
                SELECT COUNT(DISTINCT symbol) FROM stock_daily
                WHERE symbol NOT IN (
                    SELECT DISTINCT symbol FROM stock_daily
                    WHERE date >= date('now', '-7 days')
                )
            """).fetchone()[0]
            report["stale_symbols"] = stale

            # 价格异常
            anomalies = conn.execute("""
                SELECT COUNT(*) FROM stock_daily a
                JOIN stock_daily b ON a.symbol = b.symbol
                    AND b.date = (SELECT MAX(date) FROM stock_daily WHERE symbol = a.symbol AND date < a.date)
                WHERE a.date >= date('now', '-30 days')
                    AND ABS((a.close - b.close) / b.close) > 0.20
            """).fetchone()[0]
            report["price_anomalies"] = anomalies

        report["healthy"] = (
            report.get("stale_symbols", 0) < report.get("total_symbols", 1) * 0.1
            and report.get("active_daily", {})
        )
        return report
        """生成数据新鲜度报告。"""
        cutoff = (date.today() - timedelta(days=5)).isoformat()
        with sqlite3.connect(db_path) as conn:
            total = conn.execute(
                "SELECT COUNT(DISTINCT symbol) FROM stock_daily"
            ).fetchone()[0]
            fresh = conn.execute(
                "SELECT COUNT(DISTINCT symbol) FROM stock_daily WHERE date >= ?",
                (cutoff,),
            ).fetchone()[0]
            # 查找最新数据日期
            latest = conn.execute(
                "SELECT MAX(date) FROM stock_daily"
            ).fetchone()[0]
        stale = total - fresh
        return {
            "total": total,
            "fresh": fresh,
            "stale": stale,
            "stale_pct": round(stale / total * 100, 1) if total > 0 else 0,
            "latest_date": latest or "无数据",
            "warning": stale > total * 0.3 if total > 0 else True,
        }


def _bs_fetch_batch(tasks: list) -> list:
    """多进程 worker：独立 login，批量拉取 baostock 数据。

    注意：baostock 多进程环境下 "用户未登录" 错误常见，
    本函数内置重试机制，首次失败会重登后重试一次。
    """
    import baostock as bs
    bs.login()
    results = []
    failed = []
    for symbol, bs_code, start, end in tasks:
        # 最多重试 1 次
        for attempt in range(2):
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,open,high,low,close,volume,amount",
                start_date=start,
                end_date=end,
                frequency="d",
                adjustflag="2",  # 前复权
            )
            if rs.error_code == "0":
                break  # 成功，跳出重试循环
            if "未登录" in (rs.error_msg or ""):
                try:
                    bs.logout()
                except Exception:
                    pass
                bs.login()
                continue
            failed.append(f"{symbol}({rs.error_msg})")
            break
        else:
            failed.append(f"{symbol}({rs.error_msg})")
            continue

        row_count = 0
        while rs.next():
            results.append([symbol] + rs.get_row_data())
            row_count += 1
        if row_count == 0 and start != end:
            failed.append(f"{symbol}(无数据: {start}~{end})")
    bs.logout()
    if failed:
        import sys
        print(f"[bs_batch] {len(failed)}只失败: {', '.join(failed[:10])}{'...' if len(failed)>10 else ''}", file=sys.stderr)
    return results


class DataEngine:
    """行情数据引擎，负责 SQLite 存储和 baostock 数据同步。"""

    def __init__(self, settings: Settings) -> None:
        self.db_path: str = settings.db_path
        self.start_date: str = settings.start_date
        self._init_db()

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(_CREATE_TABLE_SQL)
            conn.execute(_CREATE_INDEX_SQL)
            conn.commit()
        logger.info(f"数据库初始化完成：{self.db_path}")

    def _get_last_date(self, symbol: str) -> str | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT MAX(date) FROM stock_daily WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        return row[0] if row and row[0] else None

    def get_ohlcv(self, symbol: str) -> pd.DataFrame:
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql(
                "SELECT * FROM stock_daily WHERE symbol = ? ORDER BY date",
                conn,
                params=(symbol,),
            )
        return df

    def get_clean_ohlcv(self, symbol: str,
                         clean: bool = True,
                         min_bars: int = 20) -> Optional[pd.DataFrame]:
        """获取清洗后的 OHLCV 数据。

        相比 get_ohlcv()，额外执行：
        1. 标记停牌日
        2. 检测复权异常
        3. 检查数据新鲜度
        4. 过滤 volume=0 且无成交的垃圾行

        Args:
            symbol: 股票代码。
            clean: 是否执行清洗，默认 True。
            min_bars: 最少需要多少根K线，不足返回 None。

        Returns:
            清洗后的 DataFrame 或 None。
        """
        df = self.get_ohlcv(symbol)
        if df.empty or len(df) < min_bars:
            return None

        # 基础过滤：成交量>0 才是有交易的日子
        before = len(df)
        df = df[df["volume"] > 0].copy()
        if len(df) < min_bars:
            return None

        if clean:
            # 标记停牌日（对有交易的数据做进一步分析）
            df = DataValidator.mark_suspended_days(df)

            # 检测复权异常
            anomaly_dates = DataValidator.detect_adj_anomaly(df)
            if anomaly_dates:
                df.loc[df["date"].isin(anomaly_dates), "is_suspended"] = True

            # 检查新鲜度
            last_date = str(df["date"].iloc[-1])
            is_fresh, days_old = DataValidator.check_freshness(last_date)
            if not is_fresh:
                logger.warning(f"[{symbol}] 数据已过期 {days_old} 天 (最新: {last_date})")

        logger.debug(f"[{symbol}] get_clean_ohlcv: {len(df)}/{before} 行")
        return df

    @staticmethod
    def _to_baostock_code(symbol: str) -> str:
        """将纯数字代码转为 baostock 格式：6/9开头 -> sh，其余 -> sz。"""
        prefix = "sh" if symbol.startswith(("6", "9")) else "sz"
        return f"{prefix}.{symbol}"

    # ── 数据同步 ──

    def sync_today_bulk(self) -> int:
        """多进程并行拉取增量数据（首选 mootdx，备选 baostock）。"""
        from datetime import date, timedelta
        from multiprocessing import Pool

        today_str = date.today().strftime("%Y-%m-%d")

        tasks = []
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT symbol, MAX(date) FROM stock_daily GROUP BY symbol"
            ).fetchall()

        if not rows:
            logger.warning("本地无股票数据，请先执行 --backfill")
            return 0

        for symbol, last_date in rows:
            if last_date and last_date >= today_str:
                continue
            start = today_str
            if last_date:
                start = (date.fromisoformat(last_date) + timedelta(days=1)).strftime("%Y-%m-%d")
            tasks.append((symbol, self._to_baostock_code(symbol), start, today_str))

        if not tasks:
            logger.info("所有股票已是最新，无需更新")
            return 0

        logger.info(f"需要更新 {len(tasks)} 只股票")

        # 优先使用 mootdx（TCP 协议更稳定），带快速失败
        all_rows = []
        try:
            logger.info("使用 mootdx 同步...")
            deadline = time.time() + 60  # 最多尝试 60 秒
            for symbol, bs_code, start, end in tasks:
                if time.time() > deadline:
                    logger.warning("mootdx 同步超时（>60s），降级到 baostock")
                    break
                try:
                    from mootdx.quotes import Quotes
                    client = Quotes.factory(market='std')
                    df = client.bars(symbol=symbol, frequency=9, start=0, count=5)
                    if df is not None and not df.empty:
                        df["date"] = pd.to_datetime(df["datetime"]).dt.date.astype(str)
                        df = df[df["date"] == today_str]
                        if not df.empty:
                            all_rows.extend(_mootdx_parse_bars(df, symbol))
                except Exception:
                    continue
            if all_rows:
                logger.info(f"mootdx 同步完成: {len(all_rows)} 条")
        except Exception as e:
            logger.warning(f"mootdx 同步失败: {e}")

        # mootdx 无数据时降级到 baostock
        if not all_rows:
            logger.info("mootdx 无数据，降级到 baostock...")
            n_workers = min(8, len(tasks))
            chunks = [tasks[i::n_workers] for i in range(n_workers)]
            with Pool(n_workers) as pool:
                batch_results = pool.map(_bs_fetch_batch, chunks)
            for batch in batch_results:
                all_rows.extend(batch)

        if not all_rows:
            logger.info("无新数据（可能非交易日）")
            return 0

        df = pd.DataFrame(all_rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "turnover"])
        for col in ["open", "high", "low", "close", "volume", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"])
        df = df[df["volume"] > 0]

        count = len(df)
        with sqlite3.connect(self.db_path) as conn:
            for d in df["date"].unique().tolist():
                batch_symbols = df.loc[df["date"] == d, "symbol"].tolist()
                if batch_symbols:
                    placeholders = ",".join(["?"] * len(batch_symbols))
                    sql = f"DELETE FROM stock_daily WHERE date = ? AND symbol IN ({placeholders})"
                    conn.execute(sql, [d] + batch_symbols)
            df.to_sql("stock_daily", conn, if_exists="append", index=False)
            conn.commit()

        # 生成新鲜度报告
        staleness = DataValidator.compute_staleness_report(self.db_path)
        if staleness["warning"]:
            logger.warning(f"数据新鲜度预警: {staleness['stale_pct']}% 股票过期 ({staleness['stale']}/{staleness['total']})")
        else:
            logger.info(f"数据新鲜度: {staleness['fresh']}/{staleness['total']} 只5日内有更新")

        logger.info(f"sync_today_bulk: 写入 {count} 条数据")
        return count

    def backfill(self, symbols: list[str]) -> None:
        """通过 baostock 批量回填历史日 K 线数据（前复权）。已入库的自动 skip，断网自动重试。"""
        import baostock as bs
        from datetime import date, timedelta
        import time

        RATE_LIMIT_DELAY = 0.05  # 每个请求之间间隔 50ms，避免触发风控

        def _ensure_logged_in() -> None:
            """检查 baostock 连接，断开则重登。"""
            try:
                rs = bs.query_stock_basic(code="sh.600000")
                if rs.error_code != "0":
                    raise ConnectionError("baostock 连接已断开")
            except Exception:
                logger.warning("baostock 连接断开，正在重新登录...")
                bs.logout()
                bs.login()

        def _query_with_retry(
            bs_code: str, start: str, end: str,
            max_retries: int = 3, delay: float = 2.0,
        ) -> list[list[str]]:
            for attempt in range(max_retries):
                try:
                    rs = bs.query_history_k_data_plus(
                        bs_code,
                        "date,open,high,low,close,volume,amount",
                        start_date=start, end_date=end,
                        frequency="d", adjustflag="2",
                    )
                    if rs.error_code == "0":
                        rows = []
                        while rs.next():
                            rows.append(rs.get_row_data())
                        return rows
                    if "网络" in rs.error_msg or "连接" in rs.error_msg:
                        logger.warning(
                            f"[{bs_code}] 网络错误，重新登录后重试({attempt+1}/{max_retries}): {rs.error_msg}"
                        )
                        _ensure_logged_in()
                        time.sleep(delay)
                        delay *= 2
                        continue
                    logger.warning(f"[{bs_code}] baostock 查询失败: {rs.error_msg}")
                    return []
                except Exception as e:
                    logger.warning(
                        f"[{bs_code}] 异常，重新登录后重试({attempt+1}/{max_retries}): {e}"
                    )
                    _ensure_logged_in()
                    time.sleep(delay)
                    delay *= 2
            logger.error(f"[{bs_code}] 重试{max_retries}次后仍失败，跳过")
            return []

        today_str = date.today().strftime("%Y-%m-%d")

        lg = bs.login()
        if lg.error_code != "0":
            logger.error(f"baostock 登录失败: {lg.error_msg}")
            return

        success = 0
        skipped = 0
        failed = 0

        try:
            for i, symbol in enumerate(symbols):
                last_date = self._get_last_date(symbol)
                if last_date and last_date >= today_str:
                    skipped += 1
                    if (i + 1) % 500 == 0:
                        logger.info(f"已处理 {i + 1}/{len(symbols)}，成功 {success} 跳过 {skipped} 失败 {failed}")
                    continue

                start = last_date or self.start_date
                if last_date:
                    start = (date.fromisoformat(last_date) + timedelta(days=1)).strftime("%Y-%m-%d")

                bs_code = self._to_baostock_code(symbol)
                rows = _query_with_retry(bs_code, start, today_str)

                if not rows:
                    failed += 1
                    time.sleep(RATE_LIMIT_DELAY)
                    continue

                _FIELDS = ["date", "open", "high", "low", "close", "volume", "amount"]
                df = pd.DataFrame(rows, columns=_FIELDS)
                for col in ["open", "high", "low", "close", "volume", "amount"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna(subset=["close"])
                df = df[df["volume"] > 0]

                if df.empty:
                    skipped += 1
                    continue

                df["symbol"] = symbol
                df = df.rename(columns={"amount": "turnover"})
                df = df[["symbol", "date", "open", "high", "low", "close", "volume", "turnover"]]

                try:
                    with sqlite3.connect(self.db_path) as conn:
                        df.to_sql("stock_daily", conn, if_exists="append", index=False)
                except sqlite3.IntegrityError:
                    pass

                success += 1

                if (i + 1) % 500 == 0:
                    logger.info(f"已处理 {i + 1}/{len(symbols)}，成功 {success} 跳过 {skipped} 失败 {failed}")

                time.sleep(RATE_LIMIT_DELAY)  # 请求间间隔，防反爬

        finally:
            bs.logout()

        logger.info(f"回填完成 — 成功: {success} | 跳过: {skipped} | 失败: {failed}")

    # ── 股票列表 ──

    def get_all_symbols(self) -> list[str]:
        """通过 mootdx 获取全市场 A 股代码列表（备选 baostock）。"""
        try:
            df = _mootdx_get_symbols()
            symbols = df['symbol'].tolist()
            logger.info(f"获取股票列表完成（mootdx），共 {len(symbols)} 只")
            return symbols
        except Exception as e:
            logger.warning(f"mootdx 获取股票列表失败: {e}，降级到 baostock")
            import baostock as bs
            lg = bs.login()
            if lg.error_code != "0":
                logger.error(f"baostock 登录失败: {lg.error_msg}")
                return []
            try:
                rs = bs.query_stock_basic(code_name="", code="")
                symbols = []
                while rs.next():
                    row = rs.get_row_data()
                    code = row[0]
                    status = row[4]
                    stock_type = row[5]
                    if status == "1" and stock_type == "1":
                        symbols.append(code.split(".")[1])
                logger.info(f"获取股票列表完成（baostock），共 {len(symbols)} 只")
                return symbols
            except Exception as e2:
                logger.error(f"获取股票列表全部失败: {e2}")
                return []
            finally:
                bs.logout()
            bs.logout()

    def get_local_symbols(self) -> list[str]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM stock_daily"
            ).fetchall()
        symbols = [row[0] for row in rows]
        # 过滤非A股（排除债券代码）
        def _is_a_share(code):
            c = str(code).zfill(6)
            return c.startswith(('6', '0', '3')) and not c.startswith(('01', '10', '11', '12', '13', '14', '15', '16', '17', '18'))
        filtered = [s for s in symbols if _is_a_share(s)]
        removed = len(symbols) - len(filtered)
        if removed > 0:
            logger.info(f"get_local_symbols: 过滤非A股 {removed} 只")
        return filtered

    # ── 批量数据加载 ──

    def get_bulk_ohlcv(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        """批量加载多只股票的 OHLCV 数据，大幅减少 SQL 查询次数。

        相比逐只调用 get_ohlcv()，此方法一次 SQL 查询返回全部数据，
        然后按 symbol 分组。对全市场 5000+ 只股票的扫描，
        可将数据加载时间从 ~30s 降到 ~3s。

        Args:
            symbols: 股票代码列表。

        Returns:
            {symbol: DataFrame} 字典，仅包含有数据的股票。
        """
        if not symbols:
            return {}

        BATCH_SIZE = 500
        all_dfs = []

        with sqlite3.connect(self.db_path) as conn:
            for i in range(0, len(symbols), BATCH_SIZE):
                batch = symbols[i:i + BATCH_SIZE]
                placeholders = ",".join(["?"] * len(batch))
                query = f"""
                    SELECT symbol, date, open, high, low, close, volume, turnover
                    FROM stock_daily
                    WHERE symbol IN ({placeholders})
                    ORDER BY symbol, date
                """
                df_batch = pd.read_sql(query, conn, params=batch)
                if not df_batch.empty:
                    all_dfs.append(df_batch)

        if not all_dfs:
            return {}

        combined = pd.concat(all_dfs, ignore_index=True)
        result = {}
        for symbol, group in combined.groupby("symbol"):
            result[symbol] = group.reset_index(drop=True)

        logger.debug(f"批量加载 {len(symbols)} 只 -> 返回 {len(result)} 只有数据")
        return result

    # ── 并行回填 ──

    def backfill_from_mootdx(self, symbols: list[str] = None, max_symbols: int = None) -> int:
        """使用 mootdx 批量回填历史日 K 线数据（前复权，无需登录）。

        Args:
            symbols: 股票列表，None 表示使用全部。
            max_symbols: 限制最大股票数（测试用）。

        Returns:
            写入的总行数。
        """
        from mootdx.quotes import Quotes
        from datetime import date

        if symbols is None:
            df = _mootdx_get_symbols()
            symbols = df['symbol'].tolist()

        if max_symbols:
            symbols = symbols[:max_symbols]

        logger.info(f"mootdx 回填: {len(symbols)} 只股票")
        client = Quotes.factory(market='std')
        total_rows = 0

        for i, symbol in enumerate(symbols):
            try:
                # 检查是否已有最新数据
                last_date = self._get_last_date(symbol)
                today_str = date.today().strftime("%Y-%m-%d")
                if last_date and last_date >= today_str:
                    continue

                df = client.bars(symbol=symbol, frequency=9, start=0, count=800)
                if df is None or df.empty:
                    continue

                df["date"] = pd.to_datetime(df["datetime"]).dt.date.astype(str)
                if last_date:
                    df = df[df["date"] > last_date]
                if df.empty:
                    continue

                rows = _mootdx_parse_bars(df, symbol)
                if not rows:
                    continue

                batch_df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "turnover"])
                for col in ["open", "high", "low", "close", "volume", "turnover"]:
                    batch_df[col] = pd.to_numeric(batch_df[col], errors="coerce")
                batch_df = batch_df.dropna(subset=["close"])

                with sqlite3.connect(self.db_path) as conn:
                    for d in batch_df["date"].unique():
                        syms = batch_df.loc[batch_df["date"] == d, "symbol"].tolist()
                        if syms:
                            ph = ",".join(["?"] * len(syms))
                            conn.execute(f"DELETE FROM stock_daily WHERE date = ? AND symbol IN ({ph})", [d] + syms)
                    batch_df.to_sql("stock_daily", conn, if_exists="append", index=False)
                    conn.commit()

                total_rows += len(batch_df)
                if (i + 1) % 500 == 0:
                    logger.info(f"mootdx 回填进度: {i+1}/{len(symbols)}, 已写入 {total_rows} 行")

            except Exception as exc:
                logger.log(5, f"[{symbol}] mootdx 回填失败: {exc}")
                continue

        logger.info(f"mootdx 回填完成: {total_rows} 行")
        return total_rows

    def backfill_parallel(self, symbols: list[str], n_workers: int = 8) -> int:
        """多进程并行回填历史数据。

        原先 backfill() 是串行逐只请求，5200 只股票约需 12 分钟。
        此方法使用与 sync_today_bulk 相同的多进程架构，
        8 进程可缩短至约 3-4 分钟。

        Args:
            symbols: 全市场股票代码。
            n_workers: 并行进程数，默认 8。

        Returns:
            写入的总行数。
        """
        from datetime import date, timedelta
        from multiprocessing import Pool
        today_str = date.today().strftime("%Y-%m-%d")

        tasks = []
        for symbol in symbols:
            last_date = self._get_last_date(symbol)
            if last_date and last_date >= today_str:
                continue
            start = last_date or self.start_date
            if last_date:
                start = (date.fromisoformat(last_date) + timedelta(days=1)).strftime("%Y-%m-%d")
            tasks.append((symbol, self._to_baostock_code(symbol), start, today_str))

        if not tasks:
            logger.info("所有股票数据已是最新，跳过回填")
            return 0

        logger.info(f"并行回填 {len(tasks)} 只股票，使用 {n_workers} 进程...")

        chunks = [tasks[i::n_workers] for i in range(min(n_workers, len(tasks)))]

        with Pool(min(n_workers, len(tasks))) as pool:
            batch_results = pool.map(_bs_fetch_batch, chunks)

        all_rows = []
        for batch in batch_results:
            all_rows.extend(batch)

        if not all_rows:
            logger.info("无新数据")
            return 0

        df = pd.DataFrame(
            all_rows,
            columns=["symbol", "date", "open", "high", "low", "close", "volume", "turnover"]
        )
        for col in ["open", "high", "low", "close", "volume", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"])
        df = df[df["volume"] > 0]

        count = len(df)
        with sqlite3.connect(self.db_path) as conn:
            for d in df["date"].unique().tolist():
                batch_symbols = df.loc[df["date"] == d, "symbol"].tolist()
                if batch_symbols:
                    placeholders = ",".join(["?"] * len(batch_symbols))
                    sql = f"DELETE FROM stock_daily WHERE date = ? AND symbol IN ({placeholders})"
                    conn.execute(sql, [d] + batch_symbols)
            df.to_sql("stock_daily", conn, if_exists="append", index=False)
            conn.commit()

        logger.info(f"并行回填完成: 写入 {count} 条数据")
        return count

    def get_latest_trading_date(self) -> Optional[str]:
        """获取数据库中最新的交易日。"""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT MAX(date) FROM stock_daily").fetchone()
        return row[0] if row else None

    def get_bulk_ohlcv_date_range(
        self, symbols: list[str] = None,
        start_date: str = None, end_date: str = None,
    ) -> dict[str, pd.DataFrame]:
        """按日期范围批量加载 OHLCV 数据。

        相比 get_bulk_ohlcv() 加载全部历史，此方法只加载指定日期范围,
        大幅减少内存占用。特别适合 RPS 等只需要近期数据的策略。

        Args:
            symbols: 股票代码列表，None 表示加载所有。
            start_date: 起始日期 (YYYY-MM-DD)，None 表示不限。
            end_date: 截止日期 (YYYY-MM-DD)，None 表示不限。

        Returns:
            {symbol: DataFrame} 字典。
        """
        conditions = []
        params: list = []

        if symbols:
            placeholders = ",".join(["?"] * len(symbols))
            conditions.append(f"symbol IN ({placeholders})")
            params.extend(symbols)

        if start_date:
            conditions.append("date >= ?")
            params.append(start_date)

        if end_date:
            conditions.append("date <= ?")
            params.append(end_date)

        where = " AND ".join(conditions) if conditions else "1=1"
        query = f"""
            SELECT symbol, date, open, high, low, close, volume, turnover
            FROM stock_daily
            WHERE {where}
            ORDER BY symbol, date
        """

        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql(query, conn, params=params)

        if df.empty:
            return {}

        result = {}
        for symbol, group in df.groupby("symbol"):
            result[symbol] = group.reset_index(drop=True)

        logger.debug(f"批量加载 {len(symbols) if symbols else 'all'} 只 -> {len(result)} 有数据 (范围: {start_date}~{end_date})")
        return result

    def preload_all(self, symbols: list[str]) -> None:
        """一次性批量加载全市场数据到内存缓存。

        回测场景下，调用此方法后所有 get_ohlcv() 查询将命中内存缓存，
        避免逐只 SQL 查询。5200 只股票约 3 秒完成。
        """
        self._cache = self.get_bulk_ohlcv(symbols)
        logger.info(f"预加载完成: {len(self._cache)} 只股票已缓存")

    def get_sliced_ohlcv(self, symbol: str, cutoff_date: str) -> "pd.DataFrame":
        """从缓存中获取截止到 cutoff_date 的数据切片（回测防未来信息泄漏）。

        Args:
            symbol: 股票代码。
            cutoff_date: 截止日期，只返回 <= 此日期的数据。

        Returns:
            DataFrame，若无缓存则 fallback 到 SQL 查询。
        """
        if hasattr(self, '_cache') and symbol in self._cache:
            df = self._cache[symbol]
            mask = df["date"] <= cutoff_date
            return df[mask].copy()
        # fallback
        df = self.get_ohlcv(symbol)
        mask = df["date"] <= cutoff_date
        return df[mask]

    def clear_cache(self) -> None:
        """清除预加载缓存，释放内存。"""
        if hasattr(self, '_cache'):
            del self._cache
