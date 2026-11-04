"""名称解析失败退避的测试（网络不可用时不应每只股票都重等 13 秒）。"""

import sys
import types

from sequoia_x.utils.name_resolver import NameResolver


def test_backoff_after_both_sources_fail(monkeypatch):
    resolver = NameResolver()
    calls = []
    monkeypatch.setattr(resolver, "_try_mootdx", lambda: calls.append(1) or None)
    # 让 baostock 登录直接失败
    fake_bs = types.SimpleNamespace(
        login=lambda: types.SimpleNamespace(error_code="1"))
    monkeypatch.setitem(sys.modules, "baostock", fake_bs)

    resolver._load_all()
    resolver._load_all()

    assert len(calls) == 1, "退避期内不应重复尝试数据源"
    assert resolver.get_name("600519") == "600519"


def test_caches_successful_load(monkeypatch):
    resolver = NameResolver()
    calls = []
    monkeypatch.setattr(
        resolver, "_try_mootdx",
        lambda: calls.append(1) or {"600519": "贵州茅台"})

    assert resolver.get_name("600519") == "贵州茅台"
    assert resolver.get_name("000001") == "000001"
    assert len(calls) == 1
