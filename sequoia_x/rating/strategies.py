"""7 大策略评分模块：将 Flutter 版分析引擎移植到 pandas 向量化实现。"""

from dataclasses import dataclass, field

import pandas as pd
import numpy as np


@dataclass
class StrategyResult:
    score: float = 0.0
    signals: list[str] = field(default_factory=list)


# ── 工具函数 ──


def _ema(series: pd.Series, period: int) -> pd.Series:
    """计算指数移动平均。"""
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> float:
    """计算 RSI。"""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean().iloc[-1]
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean().iloc[-1]
    if loss == 0:
        return 100.0
    rs = gain / loss
    return 100.0 - 100.0 / (1.0 + rs)


def _rsi_series(series: pd.Series, period: int = 14) -> pd.Series:
    """计算 RSI 序列（用于背离检测）。"""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss
    return 100.0 - 100.0 / (1.0 + rs)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """计算 ATR。"""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ── 策略A: MaVolume ──

def ma_volume_strategy(
    df: pd.DataFrame,
    ma_short: int = 5,
    ma_long: int = 20,
    volume_threshold: float = 1.5,
) -> StrategyResult:
    """均线金叉 + 放量确认。"""
    result = StrategyResult()
    if len(df) < ma_long + 2:
        return result

    ma_s = df["close"].rolling(ma_short).mean()
    ma_l = df["close"].rolling(ma_long).mean()
    avg_vol = df["volume"].rolling(ma_long).mean()

    last = df.iloc[-1]
    prev = df.iloc[-2]
    s_now, s_prev = ma_s.iloc[-1], ma_s.iloc[-2]
    l_now, l_prev = ma_l.iloc[-1], ma_l.iloc[-2]
    vol_ratio = last["volume"] / avg_vol.iloc[-1] if avg_vol.iloc[-1] > 0 else 1.0

    # 金叉
    if s_prev <= l_prev and s_now > l_now:
        if vol_ratio > volume_threshold:
            result.score += 3
            result.signals.append(
                f"MaVolume-金叉放量: MA{ma_short}({s_now:.2f})上穿MA{ma_long}({l_now:.2f})，"
                f"量比{vol_ratio:.1f}"
            )
        else:
            result.score += 1
            result.signals.append("MaVolume-金叉: 量能不足")
    # 死叉
    elif s_prev >= l_prev and s_now < l_now:
        result.score -= 3
        result.signals.append(
            f"MaVolume-死叉: MA{ma_short}({s_now:.2f})下穿MA{ma_long}({l_now:.2f})"
        )

    # 多头排列
    if s_now > l_now:
        result.score += 2
        result.signals.append("MaVolume-多头排列")

    # 价格在 MA20 上方
    ma20 = df["close"].rolling(20).mean().iloc[-1]
    if last["close"] > ma20:
        result.score += 1

    return result


# ── 策略B: TurtleTrade ──

def turtle_trade_strategy(
    df: pd.DataFrame,
    break_period: int = 20,
    new_high_period: int = 60,
    volume_threshold: float = 1.5,
) -> StrategyResult:
    """突破 + 趋势强度。"""
    result = StrategyResult()
    if len(df) < max(break_period, new_high_period):
        return result

    last = df.iloc[-1]
    recent_high = df["high"].tail(break_period).max()
    recent_low = df["low"].tail(break_period).min()
    high_60 = df["high"].tail(new_high_period).max()
    avg_vol = df["volume"].tail(break_period).mean()
    vol_ratio = last["volume"] / avg_vol if avg_vol > 0 else 1.0

    # 突破前高
    if last["close"] >= recent_high:
        if vol_ratio > volume_threshold:
            result.score += 3
            result.signals.append(
                f"Turtle-放量突破: 价格突破{break_period}日高点{recent_high:.2f}，"
                f"量比{vol_ratio:.1f}"
            )
        else:
            result.score += 1

    # 跌破前低
    if last["close"] <= recent_low:
        result.score -= 3
        result.signals.append(
            f"Turtle-破位: 价格跌破{break_period}日低点{recent_low:.2f}"
        )

    # 60 日新高
    if last["close"] >= high_60:
        result.score += 2
        result.signals.append("Turtle-60日新高: 强势信号")

    return result


# ── 策略C: 均值回归 ──

def mean_reversion_strategy(
    df: pd.DataFrame,
    rsi_period: int = 14,
    rsi_overbought: float = 70,
    rsi_oversold: float = 30,
) -> StrategyResult:
    """超买超卖 + 布林带 + RSI 背离。"""
    result = StrategyResult()
    if len(df) < 30:
        return result

    last = df.iloc[-1]
    ma20 = df["close"].rolling(20).mean().iloc[-1]
    std20 = df["close"].rolling(20).std().iloc[-1]
    upper_band = ma20 + 2 * std20
    lower_band = ma20 - 2 * std20
    rsi_val = _rsi(df["close"], rsi_period)

    # 触及下轨 + 超卖
    if last["close"] <= lower_band and rsi_val < rsi_oversold:
        result.score += 3
        result.signals.append(
            f"超卖-下轨反弹: 价格触及布林下轨({lower_band:.2f})，RSI({rsi_val:.1f})超卖"
        )
    elif last["close"] <= lower_band:
        result.score += 1
        result.signals.append(f"布林-触及下轨({lower_band:.2f})")

    # 触及上轨 + 超买
    if last["close"] >= upper_band and rsi_val > rsi_overbought:
        result.score -= 2
        result.signals.append(
            f"超买-上轨风险: 价格触及布林上轨({upper_band:.2f})，RSI({rsi_val:.1f})超买"
        )

    # RSI 背离检测
    if len(df) >= 30:
        close_20 = df["close"].tail(20)
        lowest_close = close_20.min()
        highest_close = close_20.max()

        rsi_all = _rsi_series(df["close"], rsi_period)
        rsi_20 = rsi_all.tail(20)
        lowest_rsi = rsi_20.min()
        highest_rsi = rsi_20.max()

        # 底背离
        if last["close"] <= lowest_close and rsi_val > lowest_rsi + 5:
            result.score += 3
            result.signals.append("RSI-底背离: 价格新低但RSI未同步新低")
        # 顶背离
        if last["close"] >= highest_close and rsi_val < highest_rsi - 5:
            result.score -= 3
            result.signals.append("RSI-顶背离: 价格新高但RSI未同步新高")

    return result


# ── 策略D: 量价关系 ──

def volume_price_strategy(
    df: pd.DataFrame,
    volume_threshold: float = 1.5,
    volume_surge_threshold: float = 3.0,
    ma_long: int = 20,
) -> StrategyResult:
    """量价配合验证。"""
    result = StrategyResult()
    if len(df) < 10:
        return result

    last = df.iloc[-1]
    prev = df.iloc[-2]
    avg_vol = df["volume"].rolling(ma_long).mean().iloc[-1]
    vol_ratio = last["volume"] / avg_vol if avg_vol > 0 else 1.0

    # 放量上涨
    if last["close"] > prev["close"] * 1.02:
        if vol_ratio > volume_threshold:
            result.score += 2
            result.signals.append(f"量价-放量上涨: 量比{vol_ratio:.1f}")

    # 缩量回调
    if last["close"] < prev["close"] and vol_ratio < 1.0:
        result.score += 1
        result.signals.append("量价-缩量回调: 回调健康")

    # 价创新高、量没创新高
    high_10 = df["close"].tail(10).max()
    vol_10 = df["volume"].tail(10).max()
    if last["close"] >= high_10 and last["volume"] < vol_10 * 0.8:
        result.score -= 2
        result.signals.append("量价-量价背离: 上涨动力不足")

    # 成交量异常放大
    if vol_ratio > volume_surge_threshold:
        result.score -= 1
        result.signals.append(f"量价-异常放量: 量比{vol_ratio:.1f}，警惕派发")

    return result


# ── 策略E: ATR通道突破 ──

def atr_channel_strategy(
    df: pd.DataFrame,
    atr_period: int = 30,
    channel_window: int = 20,
    exit_score: float = 0.2,
) -> StrategyResult:
    """ATR 通道 + 趋势强度。"""
    result = StrategyResult()
    n = len(df)
    if n < atr_period + 5:
        return result

    close = df["close"]
    high = df["high"]
    low = df["low"]

    ema = _ema(close, atr_period)
    atr = _atr(high, low, close, atr_period)
    last_close = close.iloc[-1]
    current_ema = ema.iloc[-1]
    prev_ema = ema.iloc[-2]
    current_atr = atr.iloc[-1]

    if current_atr <= 0:
        return result

    # 趋势评分
    trend_score = (last_close - current_ema) / current_atr
    ema_slope = (current_ema - prev_ema) / prev_ema if prev_ema != 0 else 0

    is_bullish = trend_score >= 1.0 and ema_slope > 0
    is_bearish = trend_score <= -1.0 and ema_slope < 0

    # 通道上下轨
    upper_channel = high.tail(channel_window).max()
    lower_channel = low.tail(channel_window).min()
    broke_upper = last_close >= upper_channel
    broke_lower = last_close <= lower_channel

    # 评分逻辑
    if is_bullish and broke_upper:
        result.score += 3
        result.signals.append(
            f"ATR-多头突破: 趋势评分{trend_score:.1f}，突破{channel_window}日通道"
        )
    elif is_bullish:
        result.score += 1
        result.signals.append("ATR-多头趋势: 等待突破")
    elif is_bearish and broke_lower:
        result.score -= 3
        result.signals.append(
            f"ATR-空头破位: 趋势评分{trend_score:.1f}，跌破{channel_window}日通道"
        )
    elif is_bearish:
        result.score -= 1
        result.signals.append("ATR-空头趋势")

    # 过热/超跌
    if trend_score > 3.0:
        result.score -= 1
        result.signals.append(f"ATR-趋势过热({trend_score:.1f})，注意回调")
    elif trend_score < -3.0:
        result.score += 1
        result.signals.append(f"ATR-超跌反弹({trend_score:.1f})")

    # 趋势减弱
    if n >= atr_period + 2:
        prev_score = (close.iloc[-2] - ema.iloc[-2]) / (atr.iloc[-2] if atr.iloc[-2] > 0 else 1)
        if prev_score >= 1.5 and trend_score < exit_score:
            result.score -= 2
            result.signals.append("ATR-趋势减弱: 动能衰减")

    # 无趋势扣分
    if not is_bullish and not is_bearish and abs(trend_score) < 0.3:
        result.score -= 1

    return result


# ── 策略F: MACD+RSI共振 ──

def macd_rsi_strategy(
    df: pd.DataFrame,
) -> StrategyResult:
    """MACD 金叉/死叉 + RSI 共振。"""
    result = StrategyResult()
    n = len(df)
    if n < 35:
        return result

    close = df["close"]
    volume = df["volume"]

    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    dif = ema12 - ema26
    dea = _ema(dif, 9)
    macd = (dif - dea) * 2

    dif_now, dif_prev = dif.iloc[-1], dif.iloc[-2]
    dea_now, dea_prev = dea.iloc[-1], dea.iloc[-2]
    macd_now, macd_prev = macd.iloc[-1], macd.iloc[-2]

    rsi_val = _rsi(close, 14)
    avg_vol = volume.rolling(20).mean().iloc[-1]
    vol_ratio = volume.iloc[-1] / avg_vol if avg_vol > 0 else 1.0

    # DIF 上穿 DEA（金叉）
    if dif_prev <= dea_prev and dif_now > dea_now:
        if rsi_val < 60 and vol_ratio > 1.5:
            result.score += 3
            result.signals.append(f"MACD-金叉放量: RSI({rsi_val:.1f})健康，成交放量")
        else:
            result.score += 2
            result.signals.append("MACD-金叉")

    # DIF 下穿 DEA（死叉）
    if dif_prev >= dea_prev and dif_now < dea_now:
        result.score -= 3
        result.signals.append("MACD-死叉")

    # RSI 超卖 + MACD 绿柱缩短
    if rsi_val < 30 and macd_prev < 0 and macd_now > macd_prev:
        result.score += 2
        result.signals.append("MACD-RSI底背离: 超卖反弹")

    # RSI 超买 + MACD 红柱缩短
    if rsi_val > 70 and macd_prev > 0 and macd_now < macd_prev:
        result.score -= 2
        result.signals.append("MACD-RSI顶背离: 回调风险")

    # DIF 零轴上方
    if dif_now > 0:
        result.score += 1
    else:
        result.score -= 1

    return result


# ── 策略G: MA20/60金叉死叉 ──

def ma_cross_strategy(
    df: pd.DataFrame,
) -> StrategyResult:
    """MA20 与 MA60 金叉/死叉。"""
    result = StrategyResult()
    n = len(df)
    if n < 61:
        return result

    close = df["close"]
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()

    m20_now, m20_prev = ma20.iloc[-1], ma20.iloc[-2]
    m60_now, m60_prev = ma60.iloc[-1], ma60.iloc[-2]
    m60_prev2 = ma60.iloc[-3] if len(ma60) >= 3 else m60_now

    # MA20 上穿 MA60（金叉）
    if m20_prev <= m60_prev and m20_now > m60_now:
        result.score += 3
        result.signals.append(
            f"MA20/60-金叉: MA20({m20_now:.2f})上穿MA60({m60_now:.2f})"
        )

    # MA20 下穿 MA60（死叉）
    if m20_prev >= m60_prev and m20_now < m60_now:
        result.score -= 3
        result.signals.append(
            f"MA20/60-死叉: MA20({m20_now:.2f})下穿MA60({m60_now:.2f})"
        )

    # 价格在 MA20 和 MA60 之上
    last_close = close.iloc[-1]
    if last_close > m20_now and last_close > m60_now:
        result.score += 1

    # MA60 方向向上
    if m60_now > m60_prev2:
        result.score += 1
        result.signals.append("MA60-趋势向上: 中长期看多")

    # MA20 在 MA60 之上（多头排列）
    if m20_now > m60_now:
        result.score += 1

    return result


# ── 策略H: KDJ ──

def kdj_strategy(
    df: pd.DataFrame,
    k_period: int = 9,
    d_period: int = 3,
    j_period: int = 3,
) -> StrategyResult:
    """KDJ 金叉/死叉 + 超卖区金叉。

    定位：辅助评分策略，权重不宜过高。
    KDJ 在短线转折点判断上比 MACD 灵敏，
    但单指标有效性较弱，适合与均线/BOLL 配合使用。
    """
    result = StrategyResult()
    if len(df) < 30:
        return result

    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    denom = (high_max - low_min)
    rsv = df["close"].copy()
    mask = denom > 0
    rsv[mask] = (df["close"] - low_min) / denom * 100
    rsv[~mask] = 50.0

    k = rsv.ewm(span=d_period, adjust=False).mean()
    d = k.ewm(span=j_period, adjust=False).mean()
    j = 3 * k - 2 * d

    k_now, k_prev = k.iloc[-1], k.iloc[-2]
    d_now, d_prev = d.iloc[-1], d.iloc[-2]
    j_now = j.iloc[-1]

    # 金叉: K 上穿 D
    if k_prev <= d_prev and k_now > d_now:
        if k_now < 25:
            result.score += 2
            result.signals.append(
                f"KDJ-超卖金叉: K({k_now:.1f})上穿D({d_now:.1f})在超卖区"
            )
        elif k_now > 75:
            result.score += 0.5
            result.signals.append("KDJ-高位金叉: 注意超买风险")
        else:
            result.score += 1.5
            result.signals.append(f"KDJ-金叉: K({k_now:.1f})上穿D({d_now:.1f})")

    # 死叉: K 下穿 D
    if k_prev >= d_prev and k_now < d_now:
        if k_now > 75:
            result.score -= 2
            result.signals.append(
                f"KDJ-超买死叉: K({k_now:.1f})从高位下穿D({d_now:.1f})"
            )
        else:
            result.score -= 1
            result.signals.append(f"KDJ-死叉: K({k_now:.1f})下穿D({d_now:.1f})")

    # 超卖区（辅助 mean_reversion 确认）
    if k_now < 20 and d_now < 20:
        result.score += 1
        result.signals.append(f"KDJ-超卖: K({k_now:.1f}) D({d_now:.1f})")

    # 超买区
    if k_now > 80 and d_now > 80:
        result.score -= 1
        result.signals.append(f"KDJ-超买: K({k_now:.1f}) D({d_now:.1f})")

    # J 值极端
    if j_now < 0:
        result.score += 1
        result.signals.append("KDJ-J值负: 极短线超卖")
    if j_now > 100:
        result.score -= 1
        result.signals.append("KDJ-J值>100: 极短线超买")

    # KDJ 与均线多头共振（增强信号）
    if len(df) >= 20:
        ma20 = df["close"].rolling(20).mean().iloc[-1]
        last_close = df["close"].iloc[-1]
        if last_close > ma20 and k_now > d_now:
            result.score += 1
            result.signals.append("KDJ-均线共振: 价格在MA20之上且KDJ金叉")

    return result
