"""数据引擎属性测试。"""

import sqlite3
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

from sequoia_x.core.config import Settings
from sequoia_x.data.engine import DataEngine


def make_engine_in(tmp_dir: str) -> tuple[DataEngine, Settings]:
    """创建使用临时数据库的 DataEngine 实例。"""
    settings = Settings(
        db_path=str(Path(tmp_dir) / "test.db"),
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
    )
    engine = DataEngine(settings)
    return engine, settings


# Property 4: (symbol, date) 唯一约束防止重复写入
@given(
    symbol=st.text(min_size=6, max_size=6, alphabet="0123456789"),
    trade_date=st.dates(min_value=date(2024, 1, 1), max_value=date(2025, 12, 31)),
)
@h_settings(max_examples=50, deadline=None)
def test_unique_symbol_date_constraint(symbol: str, trade_date: date) -> None:
    """相同 (symbol, date) 插入两次，数据库中该组合记录数应保持为 1。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        row = {
            "symbol": symbol, "date": str(trade_date),
            "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
            "volume": 1000.0, "turnover": 10500.0,
        }
        df = pd.DataFrame([row])
        conn = sqlite3.connect(engine.db_path)
        try:
            df.to_sql("stock_daily", conn, if_exists="append", index=False, method="multi")
            try:
                df.to_sql("stock_daily", conn, if_exists="append", index=False, method="multi")
            except sqlite3.IntegrityError:
                pass
            count = conn.execute(
                "SELECT COUNT(*) FROM stock_daily WHERE symbol=? AND date=?",
                (symbol, str(trade_date)),
            ).fetchone()[0]
        finally:
            conn.close()  # 显式关闭，避免 Windows 下临时文件被占用
        assert count == 1


def test_is_a_share_excludes_indices_and_bonds() -> None:
    """过滤器要剔除指数与债券，但保留与指数同号的真实个股。

    000016 既是上证50指数又是深市个股深康佳A，代码本身合法，应保留。
    """
    from sequoia_x.data.engine import _BENCHMARK_INDEX_SYMBOLS, _is_a_share

    for code in ("399006", "395015", "360001", "019003", "020780", "018003", "010609"):
        assert not _is_a_share(code), f"{code} 是指数/债券，不应通过"
    for code in _BENCHMARK_INDEX_SYMBOLS:
        assert not _is_a_share(code), f"基准指数 {code} 不应参与个股扫描"
    for code in ("600000", "300750", "688252", "002594", "000016"):
        assert _is_a_share(code), f"{code} 是正常个股，应通过"


def test_benchmark_index_not_routed_through_stock_quote(tmp_path, monkeypatch) -> None:
    """sync_today_bulk 必须把基准指数与个股分开取数。

    000905 既是中证500指数又是深市个股厦门港务。若按裸代码走个股行情源，
    厦门港务的价格会逐日覆盖指数序列，让 RegimeDetector 读到 -99.9% 的断崖、
    regime 恒定为强熊。这里断言基准指数不进个股快照，而是带 sh. 代码交给 baostock。
    """
    import sequoia_x.data.sina_quote as sina_mod
    from sequoia_x.data.engine import _BENCHMARK_BS_CODES

    engine, _ = make_engine_in(str(tmp_path))
    with sqlite3.connect(engine.db_path) as conn:
        conn.executemany(
            "INSERT INTO stock_daily(symbol,date,open,high,low,close,volume,turnover)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [
                ("000905", "2026-08-21", 1, 1, 1, 7854.33, 1, 1),  # 指数
                ("600000", "2026-08-21", 1, 1, 1, 9.0, 1, 1),  # 个股
            ],
        )
        conn.commit()

    snapshot_symbols: list[set[str]] = []

    def fake_snapshot(symbols):
        snapshot_symbols.append(set(symbols))
        return [], list(symbols)  # 模拟一个都没取到

    monkeypatch.setattr(sina_mod, "fetch_snapshot", fake_snapshot)

    baostock_calls: list[tuple[list[str], dict]] = []

    def fake_baostock(self, symbols, missing, bs_code_map=None):
        baostock_calls.append((sorted(symbols), dict(bs_code_map or {})))
        return []

    monkeypatch.setattr(DataEngine, "_baostock_fetch_daily", fake_baostock)

    engine.sync_today_bulk()

    assert snapshot_symbols, "未调用个股快照"
    assert "000905" not in snapshot_symbols[0], "基准指数不能进入个股行情源"
    assert "600000" in snapshot_symbols[0]

    benchmark_calls = [c for c in baostock_calls if "000905" in c[0]]
    assert benchmark_calls, "基准指数应单独走 baostock"
    assert benchmark_calls[0][1]["000905"] == _BENCHMARK_BS_CODES["000905"], (
        "基准指数必须用 sh. 前缀，否则会取到同号个股"
    )
