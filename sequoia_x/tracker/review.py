
"""推荐追踪模块：记录每日推荐到 SQLite，7天后回溯检查命中率。"""

import sqlite3, json
from datetime import date, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional

from sequoia_x.core.logger import get_logger
from sequoia_x.signal.models import TradeSignal, SignalResult
from sequoia_x.utils.name_resolver import get_name_resolver

logger = get_logger(__name__)


@dataclass
class TrackedRecommendation:
    """已记录的推荐。"""
    id: Optional[int] = None
    symbol: str = ""
    name: str = ""
    date_recorded: str = ""
    strategies: str = ""          # 逗号分隔
    entry_low: float = 0.0
    entry_high: float = 0.0
    stop_loss: float = 0.0
    target_1: float = 0.0
    target_2: Optional[float] = None
    position_pct: float = 0.0
    confidence: float = 0.0

    # 回溯结果（7天后填充）
    review_date: Optional[str] = None
    actual_return: Optional[float] = None  # 实际涨跌幅
    hit_stop_loss: Optional[bool] = None
    hit_target_1: Optional[bool] = None
    hit_target_2: Optional[bool] = None
    max_return: Optional[float] = None     # 期间最大涨幅
    max_drawdown: Optional[float] = None   # 期间最大回撤
    review_days: Optional[int] = None


class RecommendationTracker:
    """推荐追踪器。"""

    TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS recommendations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        name TEXT DEFAULT '',
        date_recorded TEXT NOT NULL,
        strategies TEXT DEFAULT '',
        entry_low REAL DEFAULT 0,
        entry_high REAL DEFAULT 0,
        stop_loss REAL DEFAULT 0,
        target_1 REAL DEFAULT 0,
        target_2 REAL DEFAULT 0,
        position_pct REAL DEFAULT 0,
        confidence REAL DEFAULT 0,
        review_date TEXT,
        actual_return REAL,
        hit_stop_loss INTEGER,
        hit_target_1 INTEGER,
        hit_target_2 INTEGER,
        max_return REAL,
        max_drawdown REAL,
        review_days INTEGER
    )
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(self.TABLE_SQL)
            conn.commit()

    def record_signals(self, signals: list[TradeSignal], date_str: Optional[str] = None):
        """记录推荐信号到数据库（只记录置信度 >= 0.5 的信号）。"""
        if not signals:
            return 0
        date_str = date_str or date.today().strftime("%Y-%m-%d")
        count = 0
        with sqlite3.connect(self.db_path) as conn:
            for sig in signals:
                if sig.confidence < 0.5:
                    continue
                conn.execute(
                    """INSERT INTO recommendations 
                    (symbol, name, date_recorded, strategies, entry_low, entry_high,
                     stop_loss, target_1, target_2, position_pct, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        sig.symbol, sig.name, date_str,
                        ",".join(sig.strategies),
                        sig.entry_zone_low, sig.entry_zone_high,
                        sig.stop_loss, sig.take_profit_1, sig.take_profit_2,
                        sig.suggested_position_pct, sig.confidence,
                    ),
                )
                count += 1
            conn.commit()
        logger.info(f"已记录 {count} 条推荐到数据库")
        return count

    def review_pending(self, engine) -> list[dict]:
        """回溯所有未检查且超过 7 天的推荐。"""
        cutoff = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")
        rows = []
        daily_max_ret: dict[str, float] = {}  # symbol -> max close
        with sqlite3.connect(self.db_path) as conn:
            pending = conn.execute(
                "SELECT id, symbol, entry_low, date_recorded FROM recommendations WHERE review_date IS NULL AND date_recorded <= ?",
                (cutoff,),
            ).fetchall()
            if not pending:
                return []

            name_resolver = get_name_resolver()
            results = []
            for rec_id, symbol, entry_low, rec_date in pending:
                try:
                    df = engine.get_ohlcv(symbol)
                    if df.empty:
                        continue
                    # 找出推荐日期之后的数据
                    mask = df["date"] >= rec_date
                    df_after = df[mask].copy()
                    if df_after.empty:
                        continue
                    closes = df_after["close"].values
                    first_close = float(closes[0])
                    last_close = float(closes[-1])

                    # 实际涨跌幅
                    actual_return = (last_close - first_close) / first_close

                    # 期间最大涨幅/最大回撤
                    max_close = float(max(closes))
                    min_close = float(min(closes))
                    max_return = (max_close - first_close) / first_close
                    max_dd = (min_close - first_close) / first_close

                    # 是否触及止损/目标
                    stop_price = entry_low * 0.97  # 近似止损
                    hit_stop = int(min_close <= stop_price)
                    target_price = entry_low * 1.05
                    hit_t1 = int(max_close >= target_price)

                    review_days = len(df_after)

                    conn.execute(
                        """UPDATE recommendations SET 
                           review_date=date('now'),
                           actual_return=?, hit_stop_loss=?, hit_target_1=?,
                           max_return=?, max_drawdown=?, review_days=?
                           WHERE id=?""",
                        (round(actual_return, 4), hit_stop, hit_t1,
                         round(max_return, 4), round(max_dd, 4),
                         review_days, rec_id),
                    )
                    name = name_resolver.get_name(symbol)
                    results.append({
                        "symbol": symbol,
                        "name": name,
                        "rec_date": rec_date,
                        "actual_return": round(actual_return * 100, 1),
                        "hit_stop": bool(hit_stop),
                        "hit_target": bool(hit_t1),
                        "max_return": round(max_return * 100, 1),
                        "max_dd": round(max_dd * 100, 1),
                        "days": review_days,
                    })
                except Exception:
                    continue
            conn.commit()
            logger.info(f"回溯完成: {len(results)} 条, 成功率 {sum(1 for r in results if r['actual_return'] > 0)}/{len(results)}")
            return results

    def get_stats(self) -> dict:
        """统计总体命中率。"""
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM recommendations WHERE review_date IS NOT NULL").fetchone()[0]
            if total == 0:
                return {}
            wins = conn.execute("SELECT COUNT(*) FROM recommendations WHERE review_date IS NOT NULL AND actual_return > 0").fetchone()[0]
            hit_stop = conn.execute("SELECT COUNT(*) FROM recommendations WHERE review_date IS NOT NULL AND hit_stop_loss = 1").fetchone()[0]
            hit_t1 = conn.execute("SELECT COUNT(*) FROM recommendations WHERE review_date IS NOT NULL AND hit_target_1 = 1").fetchone()[0]
            avg_return = conn.execute("SELECT AVG(actual_return) FROM recommendations WHERE review_date IS NOT NULL").fetchone()[0] or 0
            return {
                "total": total,
                "wins": wins,
                "win_rate": round(wins / total * 100, 1) if total else 0,
                "hit_stop_loss": hit_stop,
                "hit_target_1": hit_t1,
                "avg_return": round(avg_return * 100, 1),
            }
