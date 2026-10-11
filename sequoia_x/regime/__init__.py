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


REGIME_LABELS = {
    MarketRegime.STRONG_BULL: "强牛",
    MarketRegime.WEAK_BULL: "弱牛",
    MarketRegime.RANGE: "震荡",
    MarketRegime.WEAK_BEAR: "弱熊",
    MarketRegime.STRONG_BEAR: "强熊",
}

# 各状态下的建议仓位
REGIME_EXPOSURE = {
    MarketRegime.STRONG_BULL: 1.00,
    MarketRegime.WEAK_BULL: 0.70,
    MarketRegime.RANGE: 0.50,
    MarketRegime.WEAK_BEAR: 0.30,
    MarketRegime.STRONG_BEAR: 0.15,
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
REGIME_BIAS = {
    "强牛": {"trend": 0.22, "momentum": 0.12, "reversal": 0.34, "fundamental": 0.32},
    "弱牛": {"trend": 0.16, "momentum": 0.12, "reversal": 0.40, "fundamental": 0.32},
    "震荡": {"trend": 0.14, "momentum": 0.08, "reversal": 0.46, "fundamental": 0.32},
    "弱熊": {"trend": 0.06, "momentum": 0.04, "reversal": 0.58, "fundamental": 0.32},
    "强熊": {"trend": 0.00, "momentum": 0.00, "reversal": 0.68, "fundamental": 0.32},
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


class RegimeDetector:
    """市场状态检测器。

    对基准指数（如沪深300）进行均线和趋势分析，判断市场状态。
    """

    def __init__(self, data_engine):
        self.engine = data_engine

    def detect(self, benchmark_symbol: str = "000905") -> RegimeResult:
        """检测当前市场状态。

        Args:
            benchmark_symbol: 基准指数 baostock 代码，默认沪深300。

        Returns:
            RegimeResult。
        """
        try:
            df = self.engine.get_ohlcv(benchmark_symbol)
        except Exception:
            logger.warning(f"无法获取基准 {benchmark_symbol} 数据，默认返回震荡市")
            return RegimeResult(
                regime=MarketRegime.RANGE,
                label="震荡(默认)",
                suggested_exposure=0.50,
                strategy_bias=REGIME_BIAS["震荡"],
                trend_strength=0.0,
                volatility_regime="normal",
                volatility_pct=0.5,
                details="无法获取基准数据",
            )

        if df.empty or len(df) < 60:
            return RegimeResult(
                regime=MarketRegime.RANGE,
                label="震荡(数据不足)",
                suggested_exposure=0.50,
                strategy_bias=REGIME_BIAS["震荡"],
                trend_strength=0.0,
                volatility_regime="normal",
                volatility_pct=0.5,
                details="基准数据不足",
            )

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
    def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> float:
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        return float(tr.tail(period).mean())
