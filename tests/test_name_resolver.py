"""名称解析：磁盘缓存与失败退避的测试。"""

import json
import sys
import time
import types

from sequoia_x.utils.name_resolver import NameResolver


def _resolver(monkeypatch, tmp_path) -> NameResolver:
    monkeypatch.setattr(NameResolver, "_CACHE_PATH", tmp_path / "name_cache.json")
    return NameResolver()


def test_fresh_cache_avoids_network(monkeypatch, tmp_path):
    cache = tmp_path / "name_cache.json"
    cache.write_text(
        json.dumps({"saved_at": time.time(), "names": {"600519": "贵州茅台"}}),
        encoding="utf-8",
    )
    resolver = _resolver(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(resolver, "_try_mootdx", lambda: calls.append(1) or None)

    assert resolver.get_name("600519") == "贵州茅台"
    assert calls == [], "有效期内不应访问网络"


def test_stale_cache_refreshes_and_saves(monkeypatch, tmp_path):
    cache = tmp_path / "name_cache.json"
    cache.write_text(
        json.dumps({
            "saved_at": time.time() - 30 * 86400,
            "names": {"600519": "贵州茅台"},
        }),
        encoding="utf-8",
    )
    resolver = _resolver(monkeypatch, tmp_path)
    monkeypatch.setattr(resolver, "_try_mootdx", lambda: {"000001": "平安银行"})

    assert resolver.get_name("000001") == "平安银行"
    assert resolver.get_name("600519") == "600519"  # 新表覆盖旧表
    saved = json.loads(cache.read_text(encoding="utf-8"))
    assert saved["names"]["000001"] == "平安银行"


def test_corrupt_cache_falls_back_to_network(monkeypatch, tmp_path):
    cache = tmp_path / "name_cache.json"
    cache.write_text("not json", encoding="utf-8")
    resolver = _resolver(monkeypatch, tmp_path)
    monkeypatch.setattr(resolver, "_try_mootdx", lambda: {"600519": "贵州茅台"})

    assert resolver.get_name("600519") == "贵州茅台"


def test_backoff_after_both_sources_fail(monkeypatch, tmp_path):
    resolver = _resolver(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(resolver, "_try_mootdx", lambda: calls.append(1) or None)
    fake_bs = types.SimpleNamespace(
        login=lambda: types.SimpleNamespace(error_code="1"))
    monkeypatch.setitem(sys.modules, "baostock", fake_bs)

    resolver._load_all()
    resolver._load_all()

    assert len(calls) == 1, "退避期内不应重复尝试数据源"
    assert resolver.get_name("600519") == "600519"


def test_caches_successful_load(monkeypatch, tmp_path):
    resolver = _resolver(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(
        resolver, "_try_mootdx",
        lambda: calls.append(1) or {"600519": "贵州茅台"})

    assert resolver.get_name("600519") == "贵州茅台"
    assert resolver.get_name("000001") == "000001"
    assert len(calls) == 1
