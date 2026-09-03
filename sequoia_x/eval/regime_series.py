"""点内市场状态重建：用基准指数（000905，缺则000300）整段历史，
逐日复现 RegimeDetector.detect() 的判定规则，输出 {date: 5档label}。

只依赖当日及之前数据 → 可历史回放、无前视。
"""

from typing import Optional

import numpy as np
import pandas as pd

from sequoia_x.regime import (
    MarketRegime,
    REGIME_LABELS,
)


def _label_of(regime: MarketRegime) -> str:
    return REGIME_LABELS.get(regime, "震荡")


def _atr_series(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def point_in_time_regimes(index_df: pd.DataFrame) -> dict[str, str]:
    """对基准指数日线（date 升序）逐日判定市场状态，返回 {date: "强牛"|...|"强熊"}。

    判定逻辑逐条复现 RegimeDetector.detect()：
      价 vs MA120(不足120根用MA60) ±5% → 牛/熊方向；
      趋势强度=clip((价-ema30)/atr30/3, -1, 1) 决定强/弱；
      波动率百分位(满500根前按0.5) 高波动时牛→弱牛、震荡→弱熊。
    """
    closes = index_df["close"].astype(float)
    highs = index_df["high"].astype(float)
    lows = index_df["low"].astype(float)
    dates = index_df["date"].astype(str).tolist()
    n = len(index_df)
    if n == 0:
        return {}

    ma5 = closes.rolling(5).mean()
    ma20 = closes.rolling(20).mean()
    ma60 = closes.rolling(60).mean()
    ma120 = closes.rolling(120).mean()
    ma120f = ma120.where(ma120.notna(), ma60)  # 不足120根时回退MA60（同 detect）

    ema30 = closes.ewm(span=30, adjust=False).mean()
    atr30 = _atr_series(highs, lows, closes, 30)
    trend_raw = (closes - ema30) / atr30.where(atr30 > 0, np.nan)
    trend_strength = pd.Series(np.clip(trend_raw / 3.0, -1.0, 1.0), index=index_df.index)

    ret = closes.pct_change()
    ret60_std = ret.rolling(60).std()

    labels: list[str] = []
    vol_arr = ret60_std.values
    for i in range(n):
        cl = closes.iloc[i]
        m120 = ma120f.iloc[i]
        if not np.isfinite(m120) or m120 <= 0:
            labels.append("震荡")
            continue
        ts = trend_strength.iloc[i]

        # 波动率状态（detect 在 len<500 时直接用 0.5）
        cur = vol_arr[i] if i + 1 >= 60 else np.nan
        if i + 1 >= 500 and np.isfinite(cur):
            hist = vol_arr[: i + 1]
            valid = hist[np.isfinite(hist)]
            vol_pct = float((valid < cur).mean()) if len(valid) else 0.5
        else:
            vol_pct = 0.5
        vol_high = vol_pct > 0.8
        vol_low = vol_pct < 0.2
        # (高波修正 only 高低/范围用 vol_high)

        # 判定
        if cl > m120 * 1.05:
            if ts > 0.3:
                regime = MarketRegime.STRONG_BULL
            else:
                regime = MarketRegime.WEAK_BULL
        elif cl < m120 * 0.95:
            if ts < -0.3:
                regime = MarketRegime.STRONG_BEAR
            else:
                regime = MarketRegime.WEAK_BEAR
        else:
            regime = MarketRegime.RANGE

        if vol_high:
            if regime in (MarketRegime.STRONG_BULL, MarketRegime.WEAK_BULL):
                regime = MarketRegime.WEAK_BULL
            elif regime == MarketRegime.RANGE:
                regime = MarketRegime.WEAK_BEAR
        labels.append(_label_of(regime))

    return dict(zip(dates, labels))
