"""IC 追踪模块测试：交易日历必须来自全市场，不能被个股停牌带偏。

对应 2026-11-09 代码审查 #6。
"""
import sqlite3

import pandas as pd

from sequoia_x.signal.quality import ICTracker


class _FakeEngine:
    """只提供 ICTracker 需要的接口。"""

    def __init__(self, db_path, frames):
        self.db_path = db_path
        self._frames = frames

    def get_bulk_ohlcv(self, symbols):
        return {s: self._frames[s] for s in symbols if s in self._frames}


def _make_db(path, dates):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE stock_daily (symbol TEXT, date TEXT)")
        conn.executemany("INSERT INTO stock_daily VALUES (?, ?)",
                         [("sh.600000", d) for d in dates])
        conn.commit()


def test_calendar_uses_all_market_dates(tmp_path):
    db = tmp_path / "t.db"
    _make_db(db, ["2026-01-05", "2026-01-06", "2026-01-07"])

    class _E:
        db_path = str(db)

    dates = ICTracker._market_trade_dates(_E(), "2026-01-05")
    assert dates == ["2026-01-05", "2026-01-06", "2026-01-07"]


def test_suspended_stock_does_not_drop_snapshot(tmp_path, monkeypatch):
    """样本股票在 01-06 停牌，但全市场当天正常交易 —— 快照不能被清掉。"""
    db = tmp_path / "t.db"
    _make_db(db, ["2026-01-05", "2026-01-06", "2026-01-07"])

    tracker = ICTracker(history_path=str(tmp_path / "ic.json"))
    # 固定"今天"，让 01-05 的快照满足可评估条件
    monkeypatch.setattr("sequoia_x.signal.quality.date", _FixedDate)

    symbols = {f"sh.60000{i}": {s: float(i) for s in tracker.STRATEGIES}
               for i in range(1, 4)}
    tracker.record_scores("2026-01-05", symbols)

    # 每只股票都缺 01-06 的 K 线（模拟停牌）
    frames = {
        sym: pd.DataFrame({
            "date": ["2026-01-05", "2026-01-07", "2026-01-08", "2026-01-09"],
            "close": [10.0, 11.0, 12.0, 13.0],
        })
        for sym in symbols
    }
    tracker.eval_forward_returns(_FakeEngine(str(db), frames), lookforward_days=5)

    assert "2026-01-05" in tracker._pending
    assert tracker._eval_record.get("2026-01-05", {}).get(5) is not True


class _FixedDate:
    """date.today() 固定为 2026-02-01（距 01-05 足够远）。"""

    @staticmethod
    def today():
        import datetime
        return datetime.date(2026, 2, 1)

    @staticmethod
    def fromisoformat(s):
        import datetime
        return datetime.date.fromisoformat(s)
