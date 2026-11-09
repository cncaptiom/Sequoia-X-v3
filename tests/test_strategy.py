"""策略引擎属性测试。"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

from sequoia_x.core.config import Settings
from sequoia_x.data.engine import DataEngine
from sequoia_x.strategy.ma_volume import MaVolumeStrategy


# Feature: sequoia-x-v2, Property 9: 策略 run() 返回值类型正确
@given(
    symbols=st.lists(
        st.text(min_size=6, max_size=6, alphabet="0123456789"),
        min_size=0, max_size=3, unique=True,
    )
)
@h_settings(max_examples=30, deadline=None)
def test_strategy_run_returns_list_of_str(symbols: list[str]) -> None:
    """属性 9：run() 应返回 list[str]，每个元素为非空字符串。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
        )
        engine = DataEngine(settings)

        with patch.object(engine, "get_all_symbols", return_value=symbols):
            with patch.object(engine, "get_ohlcv", return_value=pd.DataFrame()):
                strategy = MaVolumeStrategy(engine=engine, settings=settings)
                result = strategy.run()
        del strategy
        del engine
        import gc
        gc.collect()

    assert isinstance(result, list)
    assert all(isinstance(s, str) and len(s) > 0 for s in result)


def test_get_data_truncates_when_cache_miss() -> None:
    """回测模式下，缓存未命中的股票也必须按 reference_date 截断。

    此前只有缓存分支截断，未预加载的股票走 get_ohlcv() 会拿到回测日之后的
    数据，形成未来函数（2026-11-09 审查 #1）。
    """
    from sequoia_x.strategy.base import BaseStrategy

    full = pd.DataFrame({
        "date": ["2026-01-05", "2026-01-06", "2026-01-07"],
        "close": [10.0, 11.0, 12.0],
    })

    class _Engine:
        def get_ohlcv(self, symbol):
            return full.copy()

    class _Strategy(BaseStrategy):
        def run(self):
            return []

    strategy = _Strategy(_Engine(), None)
    strategy.reference_date = "2026-01-06"

    df = strategy.get_data("sh.600000")
    assert list(df["date"]) == ["2026-01-05", "2026-01-06"]
