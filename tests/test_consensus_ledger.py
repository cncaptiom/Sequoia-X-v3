"""共识回测账本测试：现金/持仓/权益必须守恒，不能用市值重复计入。

对应 2026-11-09 代码审查 #1。
"""
import pandas as pd
import pytest

import run_consensus_backtest as rcb


class _FixedStrategy:
    """按 reference_date 返回固定选股。"""

    def __init__(self, signals):
        self.reference_date = None
        self._signals = signals

    def run(self):
        return list(self._signals.get(self.reference_date, []))


def _cache(prices):
    return {
        "sh.600000": pd.DataFrame({
            "date": list(prices.keys()),
            "open": list(prices.values()),
            "high": list(prices.values()),
            "low": list(prices.values()),
            "close": list(prices.values()),
            "volume": [1_000_000] * len(prices),
            "turnover": [10_000_000] * len(prices),
        })
    }


def test_open_position_deducts_cash_and_records_cost():
    positions = {}
    cash = rcb._open_position(positions, "sh.600000", 10.0, 1_000_000, 1_000_000, "d1")
    pos = positions["sh.600000"]

    assert 0 < pos["quantity"]
    assert pos["quantity"] % 100 == 0
    assert pos["entry_cost"] == pytest.approx(
        pos["entry_price"] * pos["quantity"] * (1 + rcb.COMMISSION))
    assert cash == pytest.approx(1_000_000 - pos["entry_cost"])


def test_close_position_returns_net_proceeds_and_pnl():
    positions = {}
    cash = rcb._open_position(positions, "sh.600000", 10.0, 1_000_000, 1_000_000, "d1")
    trades = []

    cash_after = rcb._close_position(positions, "sh.600000", 12.0, "d2", cash,
                                     "take_profit", trades)

    assert positions == {}
    # 期末现金 = 初始资金 + 已实现盈亏
    assert cash_after == pytest.approx(1_000_000 + trades[0]["pnl"])
    # 平仓收回的净额 = 新现金 - 开仓后的剩余现金
    assert cash_after - cash == pytest.approx(
        trades[0]["exit_price"] * trades[0]["quantity"]
        * (1 - rcb.COMMISSION - rcb.STAMP_TAX), rel=1e-9)


def test_equity_does_not_double_count_position_value(monkeypatch):
    """开仓当日权益不应跳升——旧实现开仓不扣现金、又加市值，凭空多出仓位金额。"""
    monkeypatch.setattr(rcb, "MIN_BARS", 0)
    dates = ["2026-04-01", "2026-04-02", "2026-04-03", "2026-04-04"]
    cache = _cache({d: 10.0 for d in dates})
    strategy = _FixedStrategy({dates[1]: ["sh.600000"]})

    trades, equity = rcb._run_one(
        cache, dates, [("S", strategy)], lambda s: True)

    curve = dict(zip([e["date"] for e in equity], [e["equity"] for e in equity]))
    # 全区间价格不变 → 权益应始终等于初始资金（只损失手续费）
    assert all(v <= rcb.INITIAL_CAPITAL + 1e-6 for v in curve.values())
    assert curve[dates[0]] == pytest.approx(rcb.INITIAL_CAPITAL)


def test_budget_below_one_lot_is_skipped(monkeypatch):
    monkeypatch.setattr(rcb, "INITIAL_CAPITAL", 1_000)
    monkeypatch.setattr(rcb, "MIN_BARS", 0)
    dates = ["2026-04-01", "2026-04-02"]
    cache = _cache({d: 100.0 for d in dates})
    strategy = _FixedStrategy({dates[0]: ["sh.600000"]})

    trades, equity = rcb._run_one(cache, dates, [("S", strategy)], lambda s: True)

    assert trades == []
    assert all(e["equity"] == pytest.approx(1_000) for e in equity)
