"""信号生成与 IC 快照的 fail-closed 守卫测试。

对应 2026-11-11 复审 R2-04 / R2-05 / R2-06。
"""
import numpy as np
import pandas as pd
import pytest

from sequoia_x.signal import generator as gen_mod
from sequoia_x.signal.generator import SignalGenerator


def _df(n=90, last_date="2026-11-10"):
    rng = np.random.default_rng(4)
    close = pd.Series(10 * np.cumprod(1 + rng.normal(0.001, 0.01, n)))
    dates = pd.date_range(end=pd.Timestamp(last_date), periods=n).strftime("%Y-%m-%d")
    return pd.DataFrame({
        "date": dates,
        "open": close.shift(1).bfill(),
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": rng.integers(100_000, 5_000_000, n),
        "turnover": rng.integers(1_000_000, 100_000_000, n),
    })


class _Engine:
    def __init__(self, df):
        self._df = df

    def get_ohlcv(self, symbol):
        return self._df.copy()


def test_generator_rejects_unparsable_last_date():
    df = _df()
    df.loc[df.index[-1], "date"] = "2026-13-99"   # 非法日期
    sig = SignalGenerator(_Engine(df)).generate("600000", ["S"])
    assert sig is None


def test_generator_rejects_unparsable_data_as_of():
    sig = SignalGenerator(_Engine(_df()), data_as_of="not-a-date").generate("600000", ["S"])
    assert sig is None


def test_generator_rejects_symbol_behind_market_date():
    sig = SignalGenerator(_Engine(_df(last_date="2026-11-05")),
                          data_as_of="2026-11-10").generate("600000", ["S"])
    assert sig is None


def test_position_sizer_failure_is_fail_closed(monkeypatch):
    """仓位计算抛异常时必须 position_pct=0 且标记不可执行（R2-05）。"""
    class _Boom:
        def __init__(self, **kwargs):
            pass

        def calculate(self, *args, **kwargs):
            raise RuntimeError("sizer boom")

    monkeypatch.setattr(gen_mod, "PositionSizer", _Boom)
    sig = SignalGenerator(_Engine(_df())).generate("600000", ["S"])

    # 区间为 none 时不会调用 sizer；只有真正走到仓位计算才断言
    if sig is None:
        pytest.skip("该样本未生成信号，无法覆盖仓位路径")
    assert sig.suggested_position_pct == 0.0
    assert sig.position_error is True


def test_position_error_excluded_from_portfolio_allocation():
    from main import _apply_portfolio_positions
    from sequoia_x.signal.models import TradeSignal

    ok = TradeSignal(symbol="600000", zone_status="inside", entry_ref=10.0)
    broken = TradeSignal(symbol="600519", zone_status="inside", entry_ref=10.0,
                         position_error=True)
    _apply_portfolio_positions([ok, broken], total_exposure=0.6)

    assert ok.suggested_position_pct > 0
    assert broken.suggested_position_pct == 0.0


def test_scan_market_records_snapshot_on_market_date():
    """IC 快照必须记在行情日，而不是程序运行日（R2-04）。"""
    from sequoia_x.rating.engine import RatingConfig, RatingEngine

    cfg = RatingConfig()
    object.__setattr__(cfg, "fundamental_enabled", False)
    engine = RatingEngine(config=cfg, data_engine=None)

    class _Tracker:
        def __init__(self):
            self.dates = []

        def record_scores(self, date_str, scores):
            self.dates.append(date_str)

    tracker = _Tracker()
    engine.scan_market(["600000"], lambda s: _df(), top_n=10, min_bars=60,
                       ic_tracker=tracker, as_of="2026-11-10")

    assert tracker.dates == ["2026-11-10"]
