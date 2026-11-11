"""回测引擎回归测试：账本守恒、成交时点、异常日风控、最小一手约束。

对应 2026-11-09 代码审查 #1-#4。
纯内存假数据，不依赖 SQLite。
"""
import pandas as pd
import pytest

from sequoia_x.backtest.engine import BacktestConfig, BacktestEngine


DATES = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]


def _bars(opens, closes, highs=None, lows=None, symbol="sh.600000"):
    highs = highs or [max(o, c) for o, c in zip(opens, closes)]
    lows = lows or [min(o, c) for o, c in zip(opens, closes)]
    return pd.DataFrame({
        "symbol": symbol,
        "date": DATES[:len(opens)],
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1_000_000] * len(opens),
        "turnover": [10_000_000] * len(opens),
    })


class _FakeData:
    def __init__(self, frames):
        self._frames = frames
        self._cache = {}
        self.db_path = ":memory:"

    def preload_all(self, symbols):
        self._cache = {s: df.copy() for s, df in self._frames.items()}

    def get_ohlcv(self, symbol):
        return self._frames.get(symbol, pd.DataFrame()).copy()

    def get_local_symbols(self):
        return list(self._frames)


class _DummyStrategy:
    """按 reference_date 返回预设选股结果；fail_on 指定的日期抛异常。"""

    def __init__(self, engine, settings, signals=None, fail_on=None):
        self.engine = engine
        self.reference_date = None
        self._signals = signals or {}
        self._fail_on = fail_on

    def preload(self, data):
        pass

    def run(self):
        if self._fail_on and self.reference_date == self._fail_on:
            raise RuntimeError("strategy boom")
        return list(self._signals.get(self.reference_date, []))


def _make_engine(frames, config):
    data = _FakeData(frames)

    class _Engine(BacktestEngine):
        def _get_trading_dates(self, sample_symbol, start, end):
            return list(DATES)

    return _Engine(data, config), data


def test_next_open_does_not_change_signal_day_equity():
    """T 日信号不能在 T 日就扣款建仓、也不能计入 T 日权益。"""
    frames = {"sh.600000": _bars(
        opens=[10.0, 10.0, 10.0, 10.0, 10.0],
        closes=[10.0, 10.0, 10.0, 10.0, 10.0],
    )}
    cfg = BacktestConfig(min_bars=0, max_positions=1)
    engine, _ = _make_engine(frames, cfg)

    result = engine.run(_DummyStrategy, ["sh.600000"], DATES[0], DATES[-1],
                        strategy_kwargs={"settings": None, "signals": {DATES[0]: ["sh.600000"]}})

    equity = dict(zip(result.equity_curve["date"], result.equity_curve["equity"]))
    assert equity[DATES[0]] == pytest.approx(cfg.initial_capital)
    assert len(result.trades) == 0 or result.trades[0].entry_date == DATES[1]


def test_trade_enters_on_next_open_price():
    frames = {"sh.600000": _bars(
        opens=[10.0, 10.5, 10.5, 11.0, 11.0],
        closes=[10.0, 10.5, 10.5, 11.0, 11.0],
    )}
    cfg = BacktestConfig(min_bars=0, max_positions=1, slippage=0.0, commission=0.0,
                         take_profit_pct=0.03)
    engine, _ = _make_engine(frames, cfg)

    result = engine.run(_DummyStrategy, ["sh.600000"], DATES[0], DATES[-1],
                        strategy_kwargs={"settings": None, "signals": {DATES[0]: ["sh.600000"]}})

    assert result.trades
    assert result.trades[0].entry_date == DATES[1]
    assert result.trades[0].entry_price == pytest.approx(10.5)


def test_strategy_exception_still_manages_positions_and_equity():
    """策略在 T 日抛异常时，已有持仓仍需止损并记录权益。"""
    frames = {"sh.600000": _bars(
        opens=[10.0, 10.0, 10.0, 10.0, 10.0],
        closes=[10.0, 10.0, 10.0, 10.0, 10.0],
        lows=[10.0, 10.0, 8.0, 10.0, 10.0],   # d3 盘中跌破 -8% 止损线
    )}
    cfg = BacktestConfig(min_bars=0, max_positions=1, slippage=0.0, commission=0.0)
    engine, _ = _make_engine(frames, cfg)

    result = engine.run(
        _DummyStrategy, ["sh.600000"], DATES[0], DATES[-1],
        strategy_kwargs={"settings": None, "signals": {DATES[0]: ["sh.600000"]},
                         "fail_on": DATES[2]},
    )

    # 权益曲线不能因为策略异常缺日期
    assert len(result.equity_curve) == len(DATES)
    # 止损仍在异常日执行
    assert result.trades
    assert result.trades[0].exit_reason == "stop_loss"
    assert result.trades[0].exit_date == DATES[2]


def test_budget_below_one_lot_is_skipped():
    """单票预算买不起一手时跳过，不强行买 100 股。"""
    frames = {"sh.600000": _bars(
        opens=[100.0] * 5,
        closes=[100.0] * 5,
    )}
    cfg = BacktestConfig(
        initial_capital=1_000,
        min_bars=0, max_positions=1,
        position_size_pct=0.10,      # 预算 100 元 < 100 股 × 100 元
        slippage=0.0, commission=0.0,
    )
    engine, _ = _make_engine(frames, cfg)

    result = engine.run(_DummyStrategy, ["sh.600000"], DATES[0], DATES[-1],
                        strategy_kwargs={"settings": None, "signals": {DATES[0]: ["sh.600000"],
                                                     DATES[1]: ["sh.600000"]}})

    assert result.trades == []
    assert all(e == cfg.initial_capital for e in result.equity_curve["equity"])


def test_ledger_conservation_after_round_trip():
    """无费用无滑点时：期末权益 = 初始资金 + 已实现盈亏。"""
    frames = {"sh.600000": _bars(
        opens=[10.0, 10.0, 10.0, 10.0, 10.0],
        closes=[10.0, 12.0, 12.0, 12.0, 12.0],
    )}
    cfg = BacktestConfig(min_bars=0, max_positions=1, slippage=0.0, commission=0.0,
                         stamp_tax=0.0, take_profit_pct=0.15, max_hold_days=1)
    engine, _ = _make_engine(frames, cfg)

    result = engine.run(_DummyStrategy, ["sh.600000"], DATES[0], DATES[-1],
                        strategy_kwargs={"settings": None, "signals": {DATES[0]: ["sh.600000"]}})

    final_equity = float(result.equity_curve["equity"].iloc[-1])
    assert final_equity == pytest.approx(cfg.initial_capital + result.total_pnl, rel=1e-6)


def test_same_close_skips_limit_up_close():
    """same_close 模式同样不能买在涨停收盘价（2026-11-11 复审残留 #2）。"""
    frames = {"sh.600000": _bars(
        opens=[10.0, 10.5, 11.0, 11.0, 11.0],
        closes=[10.0, 11.0, 11.0, 11.0, 11.0],
        highs=[10.2, 11.0, 11.2, 11.2, 11.2],
        lows=[9.9, 10.9, 10.9, 10.9, 10.9],
    )}
    cfg = BacktestConfig(min_bars=0, max_positions=1, fill_mode="same_close",
                         slippage=0.0, commission=0.0, take_profit_pct=0.01)
    engine, _ = _make_engine(frames, cfg)

    result = engine.run(_DummyStrategy, ["sh.600000"], DATES[0], DATES[-1],
                        strategy_kwargs={"settings": None,
                                         "signals": {DATES[1]: ["sh.600000"]}})

    assert result.trades == []


def test_same_close_still_buys_below_limit_up():
    """对照：未涨停时 same_close 仍应正常成交，避免上一条因其他原因空过。"""
    frames = {"sh.600000": _bars(
        opens=[10.0, 10.5, 11.0, 11.2, 11.2],
        closes=[10.0, 10.8, 11.0, 11.2, 11.2],
        highs=[10.2, 10.9, 11.2, 11.3, 11.3],
        lows=[9.9, 10.7, 10.9, 11.0, 11.0],
    )}
    cfg = BacktestConfig(min_bars=0, max_positions=1, fill_mode="same_close",
                         slippage=0.0, commission=0.0, take_profit_pct=0.01)
    engine, _ = _make_engine(frames, cfg)

    result = engine.run(_DummyStrategy, ["sh.600000"], DATES[0], DATES[-1],
                        strategy_kwargs={"settings": None,
                                         "signals": {DATES[1]: ["sh.600000"]}})

    assert len(result.trades) == 1
    assert result.trades[0].entry_date == DATES[1]
