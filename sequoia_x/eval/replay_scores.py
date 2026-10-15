"""逐日技术策略分重建：把 rating/strategies.py 的"最后一根bar"打分逻辑，
向量化复现成整段历史的每日原始分，供回放逐日截面使用（无前视，全部指标仅用当日及之前数据）。

镜像对象 = RatingEngine.analyze() 对单只股票 df（自库内最早一根bar起连续）在任意截断日 i 的返回值。
口径保证：随机抽样 (symbol, date) 直接调用 strategies 真实函数逐一比对（见 parity_check）。
"""

from typing import Callable

import numpy as np
import pandas as pd

from sequoia_x.rating.strategies import (
    atr_channel_strategy,
    kdj_strategy,
    ma_cross_strategy,
    mean_reversion_strategy,
    turtle_trade_strategy,
)

# 参与校验的技术类策略（与 engine.RATING_GROUPS 的 camelCase 名一致）。
# ⚠️ 必须与 RatingConfig 里【启用且 replay 能算】的策略集合保持一致——
# 生产新启用了技术类策略却忘了加到这里，回放校验会**静默漏检**（见 M1）。
# fundamental 不在其中：历史回放拿不到当时的财报数据。
TECH_KEYS = ["turtleTrade", "meanReversion", "atrChannel", "maCross", "kdj"]


def _rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    """复刻 strategies._rsi_series：滚动均值式 RSI（无 loss==0 分支）。"""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    return 100.0 - 100.0 / (1.0 + gain / loss)


def _rsi_scalar_arr(gain: pd.Series, loss: pd.Series) -> pd.Series:
    """复刻 strategies._rsi 的单值语义（loss==0 → 100）做成逐日序列。"""
    rsi = 100.0 - 100.0 / (1.0 + gain / loss)
    return rsi.mask(loss == 0, 100.0)


def compute_tech_series(df: pd.DataFrame) -> pd.DataFrame:
    """对单只股票 df（含 date/open/high/low/close/volume，按 date 升序）计算逐日：
    score_<key> 原始分、trend_ok（engine._check_trend 布尔）、gap_excl（近 gap_lookback 根有
    未复权跳变 → 与 get_ohlcv_rating_safe 一致剔除）、bar_idx（0 基 bar 计数）。

    仅 bar_idx>=59（>=60 根K线，与 scan_market min_bars=60 对齐）之后的评分可用。
    """
    out = pd.DataFrame(index=df.index)
    out["date"] = df["date"]
    out["bar_idx"] = np.arange(len(df))

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    vol = df["volume"].astype(float)

    # 复权跳变标记：近120根内任一日 |涨跌|>21% → 该股票当日从评级池剔除
    chg = close.pct_change().abs()
    gap_excl = chg.rolling(120, min_periods=1).max() > 0.21
    out["gap_excl"] = gap_excl.fillna(False).astype(bool)

    # ── 指标（均向前看，仅用当日及以前） ──
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    std20 = close.rolling(20).std()

    # turtle / atr / ma_cross 共用窗口极值
    roll_h20 = high.rolling(20, min_periods=1).max()
    roll_l20 = low.rolling(20, min_periods=1).min()
    roll_h60 = high.rolling(60, min_periods=1).max()
    avg_vol20 = vol.rolling(20, min_periods=1).mean()

    # mean_reversion
    rsi14_series = _rsi_series(close, 14)
    delta = close.diff()
    gain14 = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss14 = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rsi14_scalar = _rsi_scalar_arr(gain14, loss14)
    low_close20 = close.rolling(20, min_periods=1).min()
    high_close20 = close.rolling(20, min_periods=1).max()
    low_rsi20 = rsi14_series.rolling(20, min_periods=1).min()
    high_rsi20 = rsi14_series.rolling(20, min_periods=1).max()

    # atr_channel
    ema30 = close.ewm(span=30, adjust=False).mean()
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    atr30 = tr.rolling(30).mean()

    # kdj
    low9 = low.rolling(9, min_periods=1).min()
    high9 = high.rolling(9, min_periods=1).max()
    denom = high9 - low9
    rsv = pd.Series(np.where(denom > 0, (close - low9) / denom * 100.0, 50.0), index=df.index)
    k = rsv.ewm(span=3, adjust=False).mean()
    d = k.ewm(span=3, adjust=False).mean()
    j = 3 * k - 2 * d

    s = out  # 别名
    # ── turtle ──
    vol_ratio_t = vol / avg_vol20.where(avg_vol20 > 0, np.nan)
    vol_ratio_t = vol_ratio_t.fillna(1.0)
    sc = pd.Series(0.0, index=df.index)
    sc += np.where(close >= roll_h20, np.where(vol_ratio_t > 1.5, 3.0, 1.0), 0.0)
    sc += np.where(close <= roll_l20, -3.0, 0.0)
    sc += np.where(close >= roll_h60, 2.0, 0.0)
    s["score_turtleTrade"] = sc

    # ── mean_reversion ──
    upper = ma20 + 2 * std20
    lower = ma20 - 2 * std20
    sc = pd.Series(0.0, index=df.index)
    cond_over = (close <= lower) & (rsi14_scalar < 30)
    cond_lower = close <= lower
    sc += np.where(cond_over, 3.0, np.where(cond_lower, 1.0, 0.0))
    sc += np.where((close >= upper) & (rsi14_scalar > 70), -2.0, 0.0)
    # RSI 底背离 / 顶背离
    sc += np.where((close <= low_close20) & (rsi14_scalar > low_rsi20 + 5), 3.0, 0.0)
    sc += np.where((close >= high_close20) & (rsi14_scalar < high_rsi20 - 5), -3.0, 0.0)
    s["score_meanReversion"] = sc

    # ── atr_channel ──
    trend_score = (close - ema30) / atr30.where(atr30 > 0, np.nan)
    trend_score = trend_score.fillna(0.0)
    ema_slope = (ema30 - ema30.shift(1)) / ema30.shift(1).where(ema30.shift(1) != 0, np.nan)
    ema_slope = ema_slope.fillna(0.0)
    is_bull = (trend_score >= 1.0) & (ema_slope > 0)
    is_bear = (trend_score <= -1.0) & (ema_slope < 0)
    broke_up = close >= roll_h20
    broke_dn = close <= roll_l20
    sc = np.where(is_bull & broke_up, 3.0,
                  np.where(is_bull, 1.0,
                           np.where(is_bear & broke_dn, -3.0,
                                    np.where(is_bear, -1.0, 0.0))))
    sc = pd.Series(sc, index=df.index)
    sc += np.where(trend_score > 3.0, -1.0, 0.0)
    sc += np.where(trend_score < -3.0, 1.0, 0.0)
    prev_score = (close.shift(1) - ema30.shift(1)) / atr30.shift(1).where(atr30.shift(1) > 0, 1.0)
    sc += np.where((prev_score >= 1.5) & (trend_score < 0.2), -2.0, 0.0)
    sc += np.where(~is_bull & ~is_bear & (trend_score.abs() < 0.3), -1.0, 0.0)
    # atr<=0 的早段：engine 直接 return 0
    sc = sc.mask(atr30 <= 0, 0.0)
    s["score_atrChannel"] = sc

    # ── ma_cross ──
    m20 = ma20
    m60 = ma60
    m60_p2 = m60.shift(2)
    sc = pd.Series(0.0, index=df.index)
    golden = (m20.shift(1) <= m60.shift(1)) & (m20 > m60)
    death = (m20.shift(1) >= m60.shift(1)) & (m20 < m60)
    sc += np.where(golden, 3.0, 0.0)
    sc += np.where(death, -3.0, 0.0)
    sc += np.where((close > m20) & (close > m60), 1.0, 0.0)
    sc += np.where(m60 > m60_p2, 1.0, 0.0)
    sc += np.where(m20 > m60, 1.0, 0.0)
    # 不足62根K线时 engine 返回 0
    sc = sc.mask(out["bar_idx"] < 61, 0.0)
    s["score_maCross"] = sc

    # ── kdj ──
    k_prev, d_prev = k.shift(1), d.shift(1)
    cross_up = (k_prev <= d_prev) & (k > d)
    cross_dn = (k_prev >= d_prev) & (k < d)
    add = np.where(cross_up, np.where(k < 25, 2.0, np.where(k > 75, 0.5, 1.5)), 0.0)
    sub = np.where(cross_dn, np.where(k > 75, 2.0, 1.0), 0.0)
    sc = pd.Series(add - sub, index=df.index)
    sc += np.where((k < 20) & (d < 20), 1.0, 0.0)
    sc += np.where((k > 80) & (d > 80), -1.0, 0.0)
    sc += np.where(j < 0, 1.0, 0.0)
    sc += np.where(j > 100, -1.0, 0.0)
    sc += np.where((close > ma20) & (k > d), 1.0, 0.0)
    s["score_kdj"] = sc

    # ── 趋势过滤（engine._check_trend）──
    trend_ok = (ma5 > ma10) & (ma10 > ma20) & (ma20 > ma60) \
        & (ma20 > ma20.shift(1)) & (close > ma20)
    s["trend_ok"] = trend_ok.fillna(False).astype(bool)

    return out


# ── 保真校验：抽样逐日调用真实策略函数比对 ──

_LIVE_FN: dict[str, Callable] = {
    "turtleTrade": turtle_trade_strategy,
    "meanReversion": mean_reversion_strategy,
    "atrChannel": atr_channel_strategy,
    "maCross": ma_cross_strategy,
    "kdj": kdj_strategy,
}


def live_strategy_score(strategy_key: str, df: pd.DataFrame, cfg) -> float:
    """调真实 strategies 函数，返回引擎 analyze() 会采到的该策略原始分。"""
    from sequoia_x.rating.strategies import StrategyResult

    fn = _LIVE_FN[strategy_key]
    if strategy_key == "turtleTrade":
        res: StrategyResult = fn(df, cfg.break_period, cfg.new_high_period)
    elif strategy_key == "meanReversion":
        res = fn(df, cfg.rsi_period, cfg.rsi_overbought, cfg.rsi_oversold)
    elif strategy_key == "atrChannel":
        res = fn(df, cfg.atr_trend_period, cfg.atr_channel_window, cfg.atr_exit_score)
    elif strategy_key == "maCross":
        res = fn(df)
    else:  # kdj
        res = fn(df, cfg.kdj_k_period, cfg.kdj_d_period, cfg.kdj_j_period)
    return float(res.score)


def parity_check(df: pd.DataFrame, tech: pd.DataFrame, cfg,
                 sample_rows: int = 60, seed: int = 0) -> dict:
    """随机抽 (bar_idx) 与真实函数比对，返回每策略 |最大误差|、不一致行数。

    tech 须由同一 df 经 compute_tech_series 产出。
    """
    import random

    rng = random.Random(seed)
    n = len(df)
    candidates = [i for i in range(n) if i >= 62 and not bool(tech["gap_excl"].iloc[i])]
    if not candidates:
        return {}
    picked = rng.sample(candidates, min(sample_rows, len(candidates)))
    report: dict[str, dict] = {}
    for key in TECH_KEYS:
        live_vals = [live_strategy_score(key, df.iloc[: i + 1], cfg) for i in picked]
        mine_vals = [float(tech[f"score_{key}"].iloc[i]) for i in picked]
        errs = [abs(a - b) for a, b in zip(live_vals, mine_vals)]
        report[key] = {
            "max_err": round(float(max(errs)), 4),
            "mismatch": int(sum(1 for e in errs if e > 1e-6)),
            "n": len(picked),
        }
    return report
