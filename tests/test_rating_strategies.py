"""突破类策略口径测试：窗口只能用「前 N 个交易日」，今日高点不能抬高门槛。

对应 2026-11-09 代码审查 #5。
"""
import numpy as np
import pandas as pd

from sequoia_x.eval.replay_scores import compute_tech_series, parity_check
from sequoia_x.rating.engine import RatingConfig
from sequoia_x.rating.strategies import atr_channel_strategy, turtle_trade_strategy


def _frame(closes, highs, lows, volumes=None):
    n = len(closes)
    volumes = volumes or [1_000_000] * n
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n).strftime("%Y-%m-%d"),
        "open": [c * 0.99 for c in closes],
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
        "turnover": [c * v for c, v in zip(closes, volumes)],
    })


def test_turtle_breakout_not_blocked_by_todays_high():
    """今日 high 不得超过比较门槛：同一收盘价下，今日振幅不影响突破判定。"""
    base = [10.0] * 60
    closes = base + [10.5]
    lows = [9.8] * 61

    calm = _frame(closes, base + [10.6], lows)     # 今日 high 略高于收盘
    wild = _frame(closes, base + [20.0], lows)     # 今日 high 远高于收盘

    assert turtle_trade_strategy(calm).score == turtle_trade_strategy(wild).score
    # 收盘 10.5 突破前 20/60 日高点 10.0，必须被识别
    assert turtle_trade_strategy(calm).score > 0


def test_turtle_does_not_use_today_high_as_threshold():
    """旧实现要求 close == 当日 high，收盘突破前高但留上影线时会漏判。"""
    closes = [10.0] * 60 + [10.5]
    df = _frame(closes, [10.0] * 60 + [11.0], [9.8] * 61)
    result = turtle_trade_strategy(df)
    assert "Turtle-放量突破" in " ".join(result.signals) or result.score >= 3


def test_turtle_breakdown_uses_prior_low():
    closes = [10.0] * 60 + [9.0]
    lows = [9.5] * 60 + [8.0]
    df = _frame(closes, [10.2] * 61, lows)
    result = turtle_trade_strategy(df)
    assert any("破位" in s for s in result.signals)


def test_atr_channel_upper_band_excludes_today():
    """ATR 通道上轨只能用前 N 日 high：收盘价已高于前 20 日高点时，
    即使今日 high 更高，也必须识别为突破。

    （ATR 本身会用到今日 high，所以这里只断言通道判定，不比总分。）
    """
    closes = list(np.linspace(10.0, 13.0, 60)) + [13.5]
    lows = [c - 0.1 for c in closes[:-1]] + [13.2]
    prior_highs = [c + 0.1 for c in closes[:-1]]

    df = _frame(closes, prior_highs + [13.6], lows)   # 今日 high 高于收盘
    signals = " ".join(atr_channel_strategy(df).signals)
    assert "突破" in signals


def test_replay_matches_live_definitions():
    """replay_scores 必须与生产函数逐点一致（否则历史回放会复制同一错误）。"""
    rng = np.random.default_rng(11)
    n = 180
    close = pd.Series(10 * np.cumprod(1 + rng.normal(0, 0.02, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.01, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.01, n)))
    volume = pd.Series(rng.integers(100_000, 10_000_000, n))
    df = _frame(close.tolist(), high.tolist(), low.tolist(), volume.tolist())

    report = parity_check(df, compute_tech_series(df), RatingConfig(), sample_rows=40)
    assert report
    for key, stats in report.items():
        assert stats["mismatch"] == 0, f"{key} 与生产实现不一致: {stats}"
