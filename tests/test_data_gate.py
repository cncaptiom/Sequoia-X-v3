"""数据质量闸门与市场状态异常态测试。

对应 2026-11-09 代码审查 #7、#11。
"""
import sqlite3

import pandas as pd

from main import _evaluate_data_gate
from sequoia_x.regime import MarketRegime, RegimeDetector


class _Log:
    def __init__(self):
        self.messages = []

    def info(self, m):
        self.messages.append(("info", m))

    def warning(self, m):
        self.messages.append(("warning", m))

    def error(self, m):
        self.messages.append(("error", m))


class _Engine:
    def __init__(self, db_path):
        self.db_path = str(db_path)


def _make_db(path, rows):
    """rows: [(symbol, date), ...]"""
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE stock_daily (symbol TEXT, date TEXT)")
        conn.executemany("INSERT INTO stock_daily VALUES (?, ?)", rows)
        conn.commit()


def test_gate_rejects_empty_db(tmp_path):
    db = tmp_path / "t.db"
    _make_db(db, [])
    ok, data_as_of, reasons = _evaluate_data_gate(_Engine(db), _Log())
    assert not ok
    assert data_as_of == ""
    assert reasons


def test_gate_rejects_low_coverage(tmp_path):
    db = tmp_path / "t.db"
    date = "2026-11-06"
    rows = [("000905", date)]
    # 90 只股票里只有 1 只在最新行情日有数据
    rows += [(f"60{i:04d}", "2026-11-05") for i in range(89)]
    rows += [("600001", date)]
    _make_db(db, rows)

    ok, _, reasons = _evaluate_data_gate(_Engine(db), _Log())
    assert not ok
    assert any("覆盖率" in r for r in reasons)


def test_gate_rejects_missing_index(tmp_path):
    db = tmp_path / "t.db"
    date = "2026-11-06"
    _make_db(db, [("600001", date), ("600002", date)])

    ok, _, reasons = _evaluate_data_gate(_Engine(db), _Log())
    assert not ok
    assert any("000905" in r for r in reasons)


def test_gate_passes_and_reports_data_as_of(tmp_path):
    db = tmp_path / "t.db"
    date = "2026-11-06"
    _make_db(db, [("000905", date), ("600001", date), ("600002", date)])

    ok, data_as_of, reasons = _evaluate_data_gate(_Engine(db), _Log())
    assert ok
    assert data_as_of == date
    assert reasons == []


# ── 市场状态：数据不可用时必须是 UNKNOWN，而不是"震荡 50%" ──

class _EmptyDataEngine:
    def get_ohlcv(self, symbol):
        return pd.DataFrame()


class _ShortDataEngine:
    def get_ohlcv(self, symbol):
        return pd.DataFrame({"date": ["2026-11-06"] * 10,
                             "high": [1.0] * 10, "low": [1.0] * 10,
                             "close": [1.0] * 10})


def test_regime_unknown_when_benchmark_missing():
    result = RegimeDetector(_EmptyDataEngine()).detect("000905")
    assert result.regime is MarketRegime.UNKNOWN
    assert result.label == "未知"
    assert result.suggested_exposure == 0.0
    assert not result.is_valid


def test_regime_unknown_when_benchmark_too_short():
    result = RegimeDetector(_ShortDataEngine()).detect("000905")
    assert result.regime is MarketRegime.UNKNOWN
    assert not result.is_valid


def test_regime_bias_tables_are_valid_distributions():
    """每档权重必须非负且合计为 1 —— 校准改表时最容易在这里出错。"""
    from sequoia_x.regime import REGIME_BIAS

    for label, bias in REGIME_BIAS.items():
        assert set(bias) == {"trend", "momentum", "reversal", "fundamental"}, label
        assert all(v >= 0 for v in bias.values()), label
        assert abs(sum(bias.values()) - 1.0) < 1e-6, label


def test_priority_watchlist_not_configured_returns_none(tmp_path, monkeypatch):
    """未配置优先清单是「跳过」，不能被当成上传失败去阻断 manifest。"""
    from main import _upload_priority_watchlist

    monkeypatch.chdir(tmp_path)   # 目录里没有 data/priority_watchlist.json
    assert _upload_priority_watchlist(object(), object()) is None
