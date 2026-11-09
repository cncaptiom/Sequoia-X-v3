"""COS 导出的 JSON 契约：无区间时不得用 0 充数。"""

import pytest

from sequoia_x.cloud import CosUploader
from sequoia_x.signal.models import TradeSignal


def _capture(uploader):
    captured = {}
    uploader._put_json = lambda path, data, retries=3: (captured.update(data=data), True)[1]
    return captured


@pytest.fixture
def uploader():
    return CosUploader("dummy-bucket", "ap-beijing", "id", "key")


def test_recommendations_none_zone_exports_null(uploader):
    sig = TradeSignal(symbol="600519", name="贵州茅台", zone_status="none",
                      zone_reason="下方支撑过远")
    cap = _capture(uploader)
    assert uploader.upload_recommendations("rec.json", [sig])
    item = cap["data"]["recommendations"][0]
    assert item["zone_status"] == "none"
    assert item["zone_reason"] == "下方支撑过远"
    assert item["entry_low"] is None
    assert item["entry_high"] is None
    assert item["entry_ref"] is None
    assert item["stop_loss"] is None
    assert item["target_1"] is None
    assert item["risk_reward_ratio"] is None
    assert item["risk_reward_text"] == "—"


def test_signals_none_zone_exports_null(uploader):
    sig = TradeSignal(symbol="600519", zone_status="none")
    cap = _capture(uploader)
    assert uploader.upload_signals([sig], market_regime="强熊", suggested_exposure=0.15)
    item = cap["data"]["signals"][0]
    assert item["entry_low"] is None
    assert item["stop_loss"] is None
    assert item["target_1"] is None
    assert item["entry_ref"] is None


def test_signals_actionable_keeps_numbers(uploader):
    sig = TradeSignal(symbol="000001", zone_status="outside", entry_zone_low=11.42,
                      entry_zone_high=11.67, entry_ref=11.74, stop_loss=11.15,
                      take_profit_1=12.23, risk_reward=0.83, risk_reward_capped=False,
                      target_is_fallback=True)
    cap = _capture(uploader)
    assert uploader.upload_signals([sig], market_regime="强熊", suggested_exposure=0.15)
    item = cap["data"]["signals"][0]
    assert item["entry_low"] == 11.42
    assert item["entry_ref"] == 11.74
    assert item["stop_loss"] == 11.15
    assert item["risk_reward_ratio"] == 0.83
    assert item["target_is_fallback"] is True


def test_metadata_carries_run_id_and_data_as_of():
    """每个文件都要带同一个 run_id 和真实行情日期（不是运行日期）。"""
    up = CosUploader("dummy-bucket", "ap-beijing", "id", "key",
                     data_as_of="2026-11-06", run_id="run-abc123")
    cap = _capture(up)
    up.upload_market_regime({"label": "震荡"})
    data = cap["data"]
    assert data["run_id"] == "run-abc123"
    assert data["data_as_of"] == "2026-11-06"
    assert data["date"] == "2026-11-06"


def test_run_id_is_stable_within_one_uploader():
    up = CosUploader("dummy-bucket", "ap-beijing", "id", "key")
    cap = _capture(up)
    up.upload_market_regime({"label": "震荡"})
    first = cap["data"]["run_id"]
    up.upload_rating_config({"x": 1})
    assert cap["data"]["run_id"] == first


def test_manifest_lists_uploaded_files():
    up = CosUploader("dummy-bucket", "ap-beijing", "id", "key", data_as_of="2026-11-06")
    up._uploaded = ["summary/today.json", "ratings/today.json"]
    cap = _capture(up)
    assert up.upload_manifest()
    assert cap["data"]["type"] == "manifest"
    assert cap["data"]["files"] == ["ratings/today.json", "summary/today.json"]
    assert cap["data"]["run_id"] == up.run_id
