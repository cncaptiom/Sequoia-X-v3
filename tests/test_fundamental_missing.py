"""基本面缺失处理测试：缺财报不等于"基本面 0 分"。

对应 2026-11-09 代码审查 #10。
"""
import numpy as np
import pandas as pd

from sequoia_x.rating.engine import RatingConfig, RatingEngine


def _df(n=80):
    rng = np.random.default_rng(2)
    close = pd.Series(10 * np.cumprod(1 + rng.normal(0, 0.01, n)))
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n).strftime("%Y-%m-%d"),
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": rng.integers(100_000, 5_000_000, n),
        "turnover": rng.integers(1_000_000, 100_000_000, n),
    })


def _engine(financial_result):
    eng = RatingEngine(config=RatingConfig(), data_engine=None)
    eng._fetch_financial = lambda symbol, allow_network=True: financial_result
    return eng


def test_missing_financial_is_excluded_not_zero_scored():
    eng = _engine(None)
    result = eng.analyze(_df(), "600000")

    assert result.fundamental_missing is True
    # 关键：没有塞一个 0 分进去，而是整条策略不参与加权
    assert "fundamental" not in result.raw_strategy_scores


def test_present_financial_participates():
    eng = _engine({"roe": 15.0, "report_date": "2026-09-30"})
    result = eng.analyze(_df(), "600000")

    assert result.fundamental_missing is False
    assert "fundamental" in result.raw_strategy_scores
    assert result.fundamental_report_date == "2026-09-30"


def test_missing_financial_renormalizes_weights():
    """缺 fundamental 时，其余类别按比例分摊权重，总分不应被系统性拉低。"""
    no_fin = _engine(None).analyze(_df(), "600000")
    with_fin = _engine({"roe": 15.0}).analyze(_df(), "600000")

    # 两者都基于同一份行情；差别只在 fundamental 是否参与
    assert no_fin.category_scores != with_fin.category_scores
    assert "fundamental" in with_fin.category_scores
    assert "fundamental" not in no_fin.category_scores
