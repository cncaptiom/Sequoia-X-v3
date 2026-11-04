"""买入区间/止损/盈亏比的性质测试（REVIEW-2026-11-03 S1/S2/S4 的回归网）。"""

import pytest

from sequoia_x.signal.pricing import (
    RR_CAP,
    build_entry_zone,
    compute_risk_reward,
    entry_reference,
    pick_target,
    plan_risk_ok,
    stop_price,
)


def test_zone_is_symmetric_around_support():
    z = build_entry_zone(price=100, atr=2, supports={"MA20": 96}, k=0.5)
    assert z.status == "outside"
    assert z.anchor == 96
    assert z.high == pytest.approx(97.0)
    assert z.low == pytest.approx(95.0)


def test_zone_never_uses_support_too_close_to_price():
    # 支撑贴现价 0.1% 不算支撑
    z = build_entry_zone(price=100, atr=2, supports={"MA5": 99.9}, k=0.5)
    assert z.status == "none"
    assert z.reason == "已跌破全部支撑"


def test_zone_rejects_support_farther_than_cap():
    z = build_entry_zone(price=100, atr=1, supports={"低点": 95}, k=0.5, distance_cap=3)
    assert z.status == "none"
    assert z.reason == "下方支撑过远"


def test_zone_status_below_when_price_under_low():
    z = build_entry_zone(price=90, atr=2, supports={"低点": 92}, k=0.5)
    assert z.status == "none"  # 支撑在现价上方，视为已跌破


def test_zone_inside_and_outside_boundaries():
    inside = build_entry_zone(price=100, atr=2, supports={"S": 99}, k=0.5)
    assert inside.status == "inside"
    outside = build_entry_zone(price=100, atr=2, supports={"S": 96}, k=0.5)
    assert outside.status == "outside"


def test_entry_reference_by_status():
    # 区间只作风险参考：区间内与高于区间都按现价买
    outside = build_entry_zone(price=100, atr=2, supports={"S": 96}, k=0.5)
    assert outside.status == "outside"
    assert entry_reference(outside, 100) == 100
    inside = build_entry_zone(price=100, atr=2, supports={"S": 99}, k=0.5)
    assert entry_reference(inside, 100) == 100
    none_zone = build_entry_zone(price=100, atr=2, supports={"S": 90}, k=0.5)
    assert entry_reference(none_zone, 100) is None


def test_pick_target_skips_too_close_resistance():
    # 97.5 只比上轨高 0.5 ATR，不合格；取 99（≥ 上轨 + 1 ATR）
    target = pick_target([97.5, 99.0], ref=97.0, atr=1.0)
    assert target == pytest.approx(99.0)


def test_pick_target_falls_back_when_no_resistance():
    target = pick_target([], ref=97.0, atr=1.0)
    assert target == pytest.approx(99.0)


def test_stop_is_always_below_low_with_buffer():
    stop = stop_price(low=95.0, atr=2.0, original_stop=99.0)
    assert stop == pytest.approx(94.0)  # min(99, 95 - 1)
    assert stop < 95.0


def test_risk_reward_none_when_stop_too_close():
    rr = compute_risk_reward(entry=100, stop=99.9, target=110)
    assert rr.value is None
    assert rr.reason == "止损过近"


def test_risk_reward_capped():
    rr = compute_risk_reward(entry=100, stop=95, target=200)
    assert rr.value == RR_CAP
    assert rr.capped is True
    assert rr.text() == f"≥{RR_CAP:g}"


def test_risk_reward_normal():
    rr = compute_risk_reward(entry=100, stop=96, target=108)
    assert rr.value == pytest.approx(2.0)
    assert rr.text() == "2.00"


def test_plan_risk_rejects_tiny_volatility():
    # 风险距离 0.5 < 现价 1%
    assert not plan_risk_ok(upper=100, stop=99.5)
    assert plan_risk_ok(upper=100, stop=99.0)
