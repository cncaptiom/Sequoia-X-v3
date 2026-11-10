"""市场状态识别模块：判断 A 股当前处于牛市/熊市/震荡市。

方法论:
  - 均线排列法：MA5/MA20/MA60/MA120 的多头/空头排列
  - ATR 趋势强度法：价格偏离 EMA 的标准化距离
  - 波动率状态：高波/低波环境

机构应用:
  - 趋势策略（海龟、RPS）在牛市中放大权重
  - 均值回归策略在震荡市中放大权重
  - 高波动环境降低总仓位

用法:
    from sequoia_x.regime import RegimeDetector

    detector = RegimeDetector(data_engine)
    regime = detector.detect(benchmark_symbol="sh.000300")
    print(f"当前市场: {regime.label}, 建议仓位: {regime.suggested_exposure:.0%}")
"""

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)


class MarketRegime(Enum):
    STRONG_BULL = "strong_bull"      # 强牛：均线多头 + 趋势强劲
    WEAK_BULL = "weak_bull"          # 弱牛：价格在 MA60 之上但非完美多头
    RANGE = "range"                  # 震荡：均线缠绕，无明显方向
    WEAK_BEAR = "weak_bear"          # 弱熊：价格在 MA60 之下但非完美空头
    STRONG_BEAR = "strong_bear"      # 强熊：均线空头排列
    UNKNOWN = "unknown"              # 数据不足/基准缺失：不是一种市场状态


REGIME_LABELS = {
    MarketRegime.STRONG_BULL: "强牛",
    MarketRegime.WEAK_BULL: "弱牛",
    MarketRegime.RANGE: "震荡",
    MarketRegime.WEAK_BEAR: "弱熊",
    MarketRegime.STRONG_BEAR: "强熊",
    MarketRegime.UNKNOWN: "未知",
}

# 各状态下的建议仓位
REGIME_EXPOSURE = {
    MarketRegime.STRONG_BULL: 1.00,
    MarketRegime.WEAK_BULL: 0.70,
    MarketRegime.RANGE: 0.50,
    MarketRegime.WEAK_BEAR: 0.30,
    MarketRegime.STRONG_BEAR: 0.15,
    # 指数数据缺失/过期时不能假装是"震荡 50%"，而是不新增风险敞口
    MarketRegime.UNKNOWN: 0.00,
}

# 各市场状态下策略类别的权重偏向（2026-08 全量因子验证校准）
#
# 全项目唯一的 regime 权重表：rating/engine.py::apply_regime 直接引用它，
# RegimeResult.strategy_bias（飞书推送 / COS / --regime 展示）也用它，
# 避免出现两套口径互相打架。
#
# 实证依据（2024-01~2026-08，5206只 / 584交易日）：
#   · 超跌反转信号 20日超额+0.59% 胜率56.9%；熊市胜率85.4%、超额+1.17% → 熊市 reversal 主导
#   · 趋势突破 熊市超额-3.24%（假突破多）→ 熊市 trend 权重极低
#   · 动量类 横截面 IC 全程为负 → momentum 权重最低
#   · fundamental 独立防御维度，熊市保持稳定权重
#
# 2026-11-10 重校准：trend 类权重 ×0.554。
#   原因不是回测寻优，而是修复带来的**有效暴露变化**：海龟突破的窗口此前包含
#   当日 high（`close >= max(含当日 high)` 只有 close==high 才成立），生产里
#   turtleTrade 全市场恒为 0 分，trend 类实际只有 atrChannel 在工作，类别分
#   方差仅 (0+z_atr)/2。2026-11-09 修好窗口后两策略同时生效，实测类别分
#   标准差 0.5 → 0.902（1.80×，两策略横截面相关 0.63）。
#   0.554 = 0.5/0.902，把 trend 的有效暴露恢复到本表当初被校准时的水平；
#   momentum/reversal 按原比例吸收剩余预算，非财务总预算 0.68 不变。
#   单参数扫描（run_trend_calib.py）的样本内外结果不足以支持任何更大幅度的
#   调整，故不外推。
REGIME_BIAS = {
    "强牛": {"trend": 0.122, "momentum": 0.146, "reversal": 0.412, "fundamental": 0.32},
    "弱牛": {"trend": 0.089, "momentum": 0.136, "reversal": 0.455, "fundamental": 0.32},
    "震荡": {"trend": 0.078, "momentum": 0.089, "reversal": 0.513, "fundamental": 0.32},
    "弱熊": {"trend": 0.033, "momentum": 0.042, "reversal": 0.605, "fundamental": 0.32},
    "强熊": {"trend": 0.000, "momentum": 0.000, "reversal": 0.680, "fundamental": 0.32},
    # 数据不足时只用于"展示评分"，不应据此开新仓（exposure=0）
    "未知": {"trend": 0.078, "momentum": 0.089, "reversal": 0.513, "fundamental": 0.32},
}


@dataclass
class RegimeResult:
    """市场状态检测结果。"""
    regime: MarketRegime
    label: str
    suggested_exposure: float                # 建议总仓位 0~1
    strategy_bias: dict[str, float]          # 策略类型权重偏向
    trend_strength: float                     # 趋势强度 (-1 到 +1)
    volatility_regime: str                    # "high" | "normal" | "low"
    volatility_pct: float                     # 当前波动率百分位
    details: str                              # 详细描述

    @property
    def is_valid(self) -> bool:
        """市场状态是否可用。UNKNOWN 表示数据缺失，不能据此给出仓位。"""
        return self.regime is not MarketRegime.UNKNOWN


class RegimeDetector:
    """市场状态检测器。

    对基准指数（如沪深300）进行均线和趋势分析，判断市场状态。
    """

    def __init__(self, data_engine):
        self.engine = data_engine

    def detect(self, benchmark_symbol: str = "000905",
               max_staleness_days: int | None = None) -> RegimeResult:
        """检测当前市场状态。

        Args:
            benchmark_symbol: 基准指数 baostock 代码，默认沪深300。
            max_staleness_days: 基准指数最后一个交易日距今天超过该天数时判为
                UNKNOWN（数据过期）。None 表示不检查（历史回放场景）。

        Returns:
            RegimeResult。数据缺失/不足/过期时 regime=UNKNOWN、exposure=0。
        """
        try:
            df = self.engine.get_ohlcv(benchmark_symbol)
        except Exception as exc:
            logger.warning(f"无法获取基准 {benchmark_symbol} 数据: {exc}")
            return self._unknown(f"无法获取基准数据: {exc}")

        if df.empty or len(df) < 60:
            return self._unknown(f"基准数据不足（{len(df)} 条）")

        if max_staleness_days is not None:
            from datetime import date
            try:
                last = date.fromisoformat(str(df["date"].iloc[-1]))
                age = (date.today() - last).days
                if age > max_staleness_days:
                    return self._unknown(f"基准数据过期 {age} 天（最新 {last}）")
            except ValueError:
                return self._unknown("基准数据日期格式异常")

        closes = df["close"]

        # ── 均线系统 ──
        ma5 = closes.rolling(5).mean().iloc[-1]
        ma20 = closes.rolling(20).mean().iloc[-1]
        ma60 = closes.rolling(60).mean().iloc[-1]
        ma120 = closes.rolling(120).mean().iloc[-1] if len(closes) >= 120 else ma60
        last_close = closes.iloc[-1]

        # ── 趋势强度 (ATR 标准化偏离) ──
        ema30 = closes.ewm(span=30, adjust=False).mean()
        atr30 = self._compute_atr(df["high"], df["low"], closes, 30)
        trend_strength_raw = (last_close - ema30.iloc[-1]) / atr30 if atr30 > 0 else 0
        trend_strength = float(np.clip(trend_strength_raw / 3.0, -1.0, 1.0))

        # ── 波动率状态 ──
        returns_60d = closes.pct_change().dropna().tail(60)
        current_vol = float(returns_60d.std())
        # 滚动 2 年波动率分布
        if len(closes) >= 500:
            rolling_vol = closes.pct_change().rolling(60).std().dropna()
            vol_percentile = (rolling_vol < current_vol).mean()
        else:
            vol_percentile = 0.5

        if vol_percentile > 0.8:
            vol_regime = "high"
        elif vol_percentile < 0.2:
            vol_regime = "low"
        else:
            vol_regime = "normal"

        # ── 判断市场状态 ──
        # 行业标准方法：价格 vs 长期均线(MA120)位置 + 趋势强度
        details_parts = []

        # 1. 价格相对 MA120 的位置（趋势方向）
        if last_close > ma120 * 1.05:  # 价格在 MA120 上方超过5%
            if trend_strength > 0.3:
                regime = MarketRegime.STRONG_BULL
            elif trend_strength > 0:
                regime = MarketRegime.WEAK_BULL
            else:
                regime = MarketRegime.WEAK_BULL  # 上穿但动能不足，仍为弱牛
        elif last_close < ma120 * 0.95:  # 价格在 MA120 下方超过5%
            if trend_strength < -0.3:
                regime = MarketRegime.STRONG_BEAR
            elif trend_strength < 0:
                regime = MarketRegime.WEAK_BEAR
            else:
                regime = MarketRegime.WEAK_BEAR  # 下穿但有反弹，仍为弱熊
        else:
            # 价格在 MA120 附近 ±5% → 震荡
            regime = MarketRegime.RANGE

        # 2. 波动率修正：高波环境向保守调整
        if vol_regime == "high":
            if regime in (MarketRegime.STRONG_BULL, MarketRegime.WEAK_BULL):
                regime = MarketRegime.WEAK_BULL  # 高波牛降为弱牛
            elif regime == MarketRegime.RANGE:
                regime = MarketRegime.WEAK_BEAR  # 高波震荡视为偏熊

        suggested_exposure = REGIME_EXPOSURE.get(regime, 0.5)
        if vol_regime == "high":
            suggested_exposure *= 0.7
        elif vol_regime == "low":
            suggested_exposure = min(suggested_exposure * 1.2, 1.0)

        # ── 生成描述 ──
        pct_diff = (last_close / ma120 - 1) * 100
        details_parts.append(f"价格vsMA120={pct_diff:+.1f}%")
        details_parts.append(f"趋势强度={trend_strength:.2f}")
        details_parts.append(f"波动率={current_vol:.4f}({vol_regime}, P{vol_percentile:.0%})")

        return RegimeResult(
            regime=regime,
            label=REGIME_LABELS.get(regime, "未知"),
            suggested_exposure=round(suggested_exposure, 2),
            strategy_bias=REGIME_BIAS.get(REGIME_LABELS.get(regime, ""), REGIME_BIAS["震荡"]),
            trend_strength=round(trend_strength, 2),
            volatility_regime=vol_regime,
            volatility_pct=round(float(vol_percentile), 2),
            details=" | ".join(details_parts),
        )

    @staticmethod
    def _unknown(detail: str) -> RegimeResult:
        """数据不可用时的明确状态：不给出仓位建议。"""
        return RegimeResult(
            regime=MarketRegime.UNKNOWN,
            label=REGIME_LABELS[MarketRegime.UNKNOWN],
            suggested_exposure=0.0,
            strategy_bias=REGIME_BIAS["未知"],
            trend_strength=0.0,
            volatility_regime="unknown",
            volatility_pct=0.0,
            details=detail,
        )

    @staticmethod
    def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> float:
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        return float(tr.tail(period).mean())
