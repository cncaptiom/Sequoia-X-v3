
"""推荐追踪模块：记录每日推荐到 SQLite，7 天后回溯检查命中率。

回溯窗口是**固定的 7 个自然日**（`REVIEW_WINDOW_DAYS`）：一条推荐在
[推荐日, 推荐日+7 天] 这段里评估，且要等这段走完才回溯。这样同一条推荐
无论第 8 天还是第 50 天回溯，结论都一样。
"""

import sqlite3, json
from datetime import date, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional

from sequoia_x.core.logger import get_logger
from sequoia_x.signal.models import TradeSignal, SignalResult
from sequoia_x.utils.name_resolver import get_name_resolver

logger = get_logger(__name__)

# 回溯窗口（自然日）。改动它等于改动「命中率」的定义。
REVIEW_WINDOW_DAYS = 7


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
        """记录推荐信号到数据库。

        本方法不做任何筛选：调用方应传入**实际展示给用户的那批**信号。
        原先这里硬编码「confidence < 0.5 就跳过」，导致推送名单里低置信度的股票
        （实测占 40%）从未进入统计，而 get_stats() 的分母并不标注这一点。
        """
        if not signals:
            return 0
        date_str = date_str or date.today().strftime("%Y-%m-%d")
        count = 0
        with sqlite3.connect(self.db_path) as conn:
            for sig in signals:
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
        """回溯所有「已过回溯窗口、且尚未检查」的推荐，窗口固定 7 个自然日。"""
        cutoff = (date.today() - timedelta(days=REVIEW_WINDOW_DAYS)).strftime("%Y-%m-%d")
        results = []
        with sqlite3.connect(self.db_path) as conn:
            pending = conn.execute(
                "SELECT id, symbol, stop_loss, target_1, date_recorded"
                " FROM recommendations WHERE review_date IS NULL AND date_recorded <= ?",
                (cutoff,),
            ).fetchall()
            if not pending:
                return []

            name_resolver = get_name_resolver()
            for rec_id, symbol, stop_loss, target_1, rec_date in pending:
                try:
                    df = engine.get_ohlcv(symbol)
                    if df.empty:
                        continue
                    # 进场价 = 推荐日【次日开盘价】。推荐是拿推荐日收盘算出来的，
                    # 用同一根 K 线的收盘价成交是前视——与回测 H3 同一条，量级还更大：
                    # 实测把 7 日平均收益从 −0.18% 抬到 +10.41%，全部「优势」都来自它。
                    after = df[df["date"] > rec_date]
                    if after.empty:
                        continue
                    entry_price = float(after["open"].iloc[0])
                    if entry_price <= 0:
                        continue
                    entry_date = str(after["date"].iloc[0])

                    # 评估窗口：[进场日, 推荐日 + REVIEW_WINDOW_DAYS]。
                    # 此前是 `df["date"] >= rec_date`——把推荐日之后【所有】历史都算进来，
                    # 于是同一条推荐第 8 天和第 50 天回溯结论不同（实测回溯期跑到约 48 天）。
                    end_date = (date.fromisoformat(rec_date)
                                + timedelta(days=REVIEW_WINDOW_DAYS)).isoformat()
                    df_after = df[(df["date"] >= entry_date) & (df["date"] <= end_date)]
                    if df_after.empty:
                        continue
                    closes = df_after["close"].values
                    highs = df_after["high"].values
                    lows = df_after["low"].values
                    last_close = float(closes[-1])

                    # 实际涨跌幅（窗口末对比进场价）
                    actual_return = (last_close - entry_price) / entry_price

                    # 期间最大涨幅/最大回撤：用 high/low，与止损止盈同一套价格口径
                    max_return = (float(max(highs)) - entry_price) / entry_price
                    max_dd = (float(min(lows)) - entry_price) / entry_price

                    # 止损/止盈：按时间顺序走，先触及哪个就以哪个为准，两者互斥。
                    # 此前是「用整段最高价判止盈 + 整段最低价判止损」——只要期间既
                    # 冲高又探底就两边都记命中（实测止盈 97.5% / 止损 92.6%，自相矛盾）。
                    # 价格用库里存的 stop_loss / target_1；此前用 entry_low*0.97 / *1.05 近似。
                    # 从第 0 根起算——进场价是这根的开盘价，它的高低点都在进场之后。
                    hit_stop = hit_t1 = 0
                    for i in range(len(df_after)):
                        if stop_loss and float(lows[i]) <= stop_loss:
                            hit_stop = 1
                            break
                        if target_1 and float(highs[i]) >= target_1:
                            hit_t1 = 1
                            break

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
            logger.info(f"回溯完成: {len(results)} 条, 收涨 {sum(1 for r in results if r['actual_return'] > 0)}/{len(results)}")
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
