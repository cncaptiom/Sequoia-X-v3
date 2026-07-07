"""评级引擎：趋势过滤 → 多策略打分 → 盈亏比 → 综合评级。

用法:
    from sequoia_x.rating.engine import RatingEngine

    engine = RatingEngine()
    result = engine.analyze(df)
    print(result.rating, result.total_score)
"""

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from sequoia_x.rating.strategies import (
    StrategyResult,
    atr_channel_strategy,
    kdj_strategy,
    ma_cross_strategy,
    ma_volume_strategy,
    macd_rsi_strategy,
    mean_reversion_strategy,
    turtle_trade_strategy,
    volume_price_strategy,
)
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)


# ── 配置 ──

@dataclass
class RatingConfig:
    """与 Flutter StrategyConfig 对应的评级配置。"""
    ma_volume_enabled: bool = True
    ma_short: int = 5
    ma_long: int = 20
    volume_threshold: float = 1.5

    turtle_trade_enabled: bool = True
    break_period: int = 20
    new_high_period: int = 60

    mean_reversion_enabled: bool = True
    rsi_period: int = 14
    rsi_overbought: float = 70
    rsi_oversold: float = 30

    volume_price_enabled: bool = True
    volume_surge_threshold: float = 3.0

    atr_channel_enabled: bool = True
    atr_trend_period: int = 30
    atr_channel_window: int = 20
    atr_exit_score: float = 0.2
    atr_multiple: float = 2.0

    macd_rsi_enabled: bool = True
    ma_cross_enabled: bool = True

    kdj_enabled: bool = True
    kdj_k_period: int = 9
    kdj_d_period: int = 3
    kdj_j_period: int = 3

    trend_filter_enabled: bool = True

    # 默认权重（等权分配，不根据市场状态调整）
    weight_ma_volume: float = 0.125
    weight_turtle_trade: float = 0.125
    weight_mean_reversion: float = 0.125
    weight_volume_price: float = 0.125
    weight_atr_channel: float = 0.125
    weight_macd_rsi: float = 0.125
    weight_ma_cross: float = 0.125
    weight_kdj: float = 0.125

    # ── 策略类别与市场状态权重映射 ──
    # 各策略所属类别
    STRATEGY_CATEGORIES = {
        "trend": ["ma_volume", "turtle_trade", "atr_channel"],
        "momentum": ["macd_rsi", "ma_cross"],
        "reversal": ["mean_reversion", "volume_price", "kdj"],
    }
    # 策略权重属性名映射
    WEIGHT_ATTRS = {
        "ma_volume": "weight_ma_volume",
        "turtle_trade": "weight_turtle_trade",
        "mean_reversion": "weight_mean_reversion",
        "volume_price": "weight_volume_price",
        "atr_channel": "weight_atr_channel",
        "macd_rsi": "weight_macd_rsi",
        "ma_cross": "weight_ma_cross",
        "kdj": "weight_kdj",
    }
    # 各市场状态下策略类别的偏向权重（基于回测数据优化）
    # 强牛: trend+reversal接近，momentum较弱
    # 弱牛: reversal大幅领先，trend接近零
    # 震荡: 三类接近，reversal略优
    # 弱熊/强熊: 全部亏损，reversal损失最小
    REGIME_BIAS = {
        # 所有市场状态统一等权分配，不根据状态调整
        # 原因：缺乏足够数据支撑不同状态下策略收益有显著差异
        "强牛": {"trend": 0.34, "momentum": 0.33, "reversal": 0.33},
        "弱牛": {"trend": 0.34, "momentum": 0.33, "reversal": 0.33},
        "震荡": {"trend": 0.34, "momentum": 0.33, "reversal": 0.33},
        "弱熊": {"trend": 0.34, "momentum": 0.33, "reversal": 0.33},
        "强熊": {"trend": 0.34, "momentum": 0.33, "reversal": 0.33},
    }

    def apply_regime(self, regime_label: str) -> None:
        """根据市场状态动态调整各策略权重。

        强牛→趋势权重提升，弱熊→反转权重提升，震荡→平均分配。
        使用 object.__setattr__ 绕过 dataclass 冻结。
        """
        bias = self.REGIME_BIAS.get(regime_label)
        if bias is None:
            return  # 未知状态，使用默认权重

        # 计算每类策略的总权重（同类内平均分配）
        for category, cat_bias in bias.items():
            strategies = self.STRATEGY_CATEGORIES.get(category, [])
            if not strategies:
                continue
            # 同类策略均分该类权重
            per_strategy_weight = cat_bias / len(strategies)
            for s in strategies:
                attr = self.WEIGHT_ATTRS.get(s)
                if attr:
                    object.__setattr__(self, attr, round(per_strategy_weight, 4))

    def to_dict(self) -> dict:
        """序列化为字典，供 COS 上传和手机端同步。"""
        return {
            "maVolumeEnabled": self.ma_volume_enabled,
            "maShort": self.ma_short,
            "maLong": self.ma_long,
            "volumeThreshold": self.volume_threshold,
            "turtleTradeEnabled": self.turtle_trade_enabled,
            "breakPeriod": self.break_period,
            "newHighPeriod": self.new_high_period,
            "meanReversionEnabled": self.mean_reversion_enabled,
            "rsiPeriod": self.rsi_period,
            "rsiOverbought": self.rsi_overbought,
            "rsiOversold": self.rsi_oversold,
            "volumePriceEnabled": self.volume_price_enabled,
            "volumeSurgeThreshold": self.volume_surge_threshold,
            "atrChannelEnabled": self.atr_channel_enabled,
            "atrTrendPeriod": self.atr_trend_period,
            "atrChannelWindow": self.atr_channel_window,
            "atrExitScore": self.atr_exit_score,
            "atrMultiple": self.atr_multiple,
            "macdRsiEnabled": self.macd_rsi_enabled,
            "maCrossEnabled": self.ma_cross_enabled,
            "kdjEnabled": self.kdj_enabled,
            "kdjKPeriod": self.kdj_k_period,
            "kdjDPeriod": self.kdj_d_period,
            "kdjJPeriod": self.kdj_j_period,
            "trendFilterEnabled": self.trend_filter_enabled,
            "weightMaVolume": self.weight_ma_volume,
            "weightTurtleTrade": self.weight_turtle_trade,
            "weightMeanReversion": self.weight_mean_reversion,
            "weightVolumePrice": self.weight_volume_price,
            "weightAtrChannel": self.weight_atr_channel,
            "weightMacdRsi": self.weight_macd_rsi,
            "weightMaCross": self.weight_ma_cross,
            "weightKdj": self.weight_kdj,
        }


# ── 结果 ──

class Rating:
    strong_buy = "strong_buy"
    buy = "buy"
    watch = "watch"
    attention = "attention"
    avoid = "avoid"

    LABELS = {
        strong_buy: "S级",
        buy: "A级",
        watch: "B级",
        attention: "C级",
        avoid: "回避",
    }

    STARS = {
        strong_buy: "★★★★★",
        buy: "★★★★",
        watch: "★★★",
        attention: "★★",
        avoid: "★",
    }


@dataclass
class AnalysisResult:
    """单只股票的完整分析结果。"""
    symbol: str
    name: str = ""

    trend_passed: bool = False
    trend_description: str = ""
    total_score: float = 0.0
    rating: str = Rating.avoid
    strategy_scores: dict[str, float] = field(default_factory=dict)
    category_scores: dict[str, float] = field(default_factory=dict)
    signals: list[str] = field(default_factory=list)
    risk_reward_ratio: float = 0.0
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    buy_range: Optional[str] = None
    current_price: float = 0.0

    @property
    def rating_label(self) -> str:
        return Rating.LABELS.get(self.rating, self.rating)

    @property
    def rating_stars(self) -> str:
        return Rating.STARS.get(self.rating, "★")

    def to_dict(self) -> dict:
        """序列化为字典，供 COS 上传。"""
        return {
            "symbol": self.symbol,
            "name": self.name,
            "rating": self.rating,
            "rating_label": self.rating_label,
            "rating_stars": self.rating_stars,
            "total_score": round(self.total_score, 1),
            "trend_passed": self.trend_passed,
            "trend_description": self.trend_description,
            "strategy_scores": {k: round(v, 1) for k, v in self.strategy_scores.items()},
            "category_scores": {k: round(v, 1) for k, v in self.category_scores.items()},
            "signals": self.signals[:5],
            "risk_reward_ratio": round(self.risk_reward_ratio, 2),
            "stop_loss_price": round(self.stop_loss_price, 2) if self.stop_loss_price else None,
            "take_profit_price": round(self.take_profit_price, 2) if self.take_profit_price else None,
            "buy_range": self.buy_range or "",
            "current_price": round(self.current_price, 2),
        }


# ── 引擎 ──

class RatingEngine:
    """评级引擎：全市场 A 股评级筛选核心。"""

    def __init__(self, config: Optional[RatingConfig] = None):
        self.config = config or RatingConfig()

    def analyze(self, df: pd.DataFrame, symbol: str = "", name: str = "") -> AnalysisResult:
        """对单只股票的 DataFrame 执行完整分析。"""
        if len(df) < 20:
            return AnalysisResult(
                symbol=symbol,
                name=name,
                rating=Rating.attention,
                total_score=0,
                trend_description="数据不足（需要至少20根K线）",
            )

        closes = df["close"].values
        last_close = float(closes[-1])
        signals: list[str] = []

        trend_passed, trend_desc, trend_sigs = self._check_trend(df)
        signals.extend(trend_sigs)

        weights = self._get_normalized_weights()
        total_score = 0.0
        strategy_scores: dict[str, float] = {}

        strategy_map: list[tuple[str, bool, callable]] = [
            ("maVolume", self.config.ma_volume_enabled, lambda: ma_volume_strategy(
                df, self.config.ma_short, self.config.ma_long, self.config.volume_threshold)),
            ("turtleTrade", self.config.turtle_trade_enabled, lambda: turtle_trade_strategy(
                df, self.config.break_period)),
            ("meanReversion", self.config.mean_reversion_enabled, lambda: mean_reversion_strategy(
                df, self.config.rsi_period, self.config.rsi_overbought, self.config.rsi_oversold)),
            ("volumePrice", self.config.volume_price_enabled, lambda: volume_price_strategy(
                df, self.config.volume_surge_threshold)),
            ("atrChannel", self.config.atr_channel_enabled, lambda: atr_channel_strategy(
                df, self.config.atr_trend_period, self.config.atr_channel_window,
                self.config.atr_exit_score, self.config.atr_multiple)),
            ("macdRsi", self.config.macd_rsi_enabled, lambda: macd_rsi_strategy(df)),
            ("maCross", self.config.ma_cross_enabled, lambda: ma_cross_strategy(df)),
            ("kdj", self.config.kdj_enabled, lambda: kdj_strategy(
                df, self.config.kdj_k_period, self.config.kdj_d_period, self.config.kdj_j_period)),
        ]

        for name, enabled, fn in strategy_map:
            if enabled and name in weights:
                try:
                    result = fn()
                    strategy_scores[name] = result.score
                    total_score += result.score * weights[name]
                    signals.extend(result.signals)
                except Exception:
                    continue

        support = self._find_support(df)
        resistance = self._find_resistance(df)
        risk_reward = 0.0
        stop_loss = support * 0.97
        take_profit = resistance
        buy_low = support
        buy_high = last_close * 1.02

        if stop_loss > 0:
            entry_mid = (buy_low + buy_high) / 2
            reward = take_profit - entry_mid
            risk_entry = entry_mid - stop_loss
            if risk_entry > 0:
                risk_reward = round(reward / risk_entry, 2)

        category_scores = self._calc_category_scores(strategy_scores)
        rating = self._calculate_rating(trend_passed, total_score, risk_reward)

        return AnalysisResult(
            symbol=symbol,
            name=name,
            trend_passed=trend_passed,
            trend_description=trend_desc,
            total_score=round(total_score, 1),
            rating=rating,
            strategy_scores=strategy_scores,
            category_scores=category_scores,
            signals=signals,
            risk_reward_ratio=risk_reward,
            stop_loss_price=round(stop_loss, 2),
            take_profit_price=round(take_profit, 2),
            buy_range=f"{buy_low:.2f} - {buy_high:.2f}",
            current_price=round(last_close, 2),
        )

    def _get_normalized_weights(self) -> dict[str, float]:
        weights = {
            "maVolume": self.config.weight_ma_volume,
            "turtleTrade": self.config.weight_turtle_trade,
            "meanReversion": self.config.weight_mean_reversion,
            "volumePrice": self.config.weight_volume_price,
            "atrChannel": self.config.weight_atr_channel,
            "macdRsi": self.config.weight_macd_rsi,
            "maCross": self.config.weight_ma_cross,
            "kdj": self.config.weight_kdj,
        }
        enabled = {"maVolume": self.config.ma_volume_enabled,
                   "turtleTrade": self.config.turtle_trade_enabled,
                   "meanReversion": self.config.mean_reversion_enabled,
                   "volumePrice": self.config.volume_price_enabled,
                   "atrChannel": self.config.atr_channel_enabled,
                   "macdRsi": self.config.macd_rsi_enabled,
                   "maCross": self.config.ma_cross_enabled,
                   "kdj": self.config.kdj_enabled}
        filtered = {k: v for k, v in weights.items() if enabled.get(k, True)}
        total = sum(filtered.values())
        if total == 0:
            return {}
        return {k: v / total for k, v in filtered.items()}

    def _calc_category_scores(self, strategy_scores: dict[str, float]) -> dict[str, float]:
        CATEGORY_MAP = {
            "aggressive": ["maVolume", "turtleTrade", "atrChannel"],
            "conservative": ["meanReversion", "volumePrice", "kdj"],
            "signal": ["macdRsi", "maCross"],
        }
        scores = {}
        for cat_name, strategies in CATEGORY_MAP.items():
            total = sum(strategy_scores.get(s, 0) for s in strategies)
            count = sum(1 for s in strategies if s in strategy_scores)
            scores[cat_name] = round(total / count, 1) if count > 0 else 0.0
        return scores

    def _check_trend(self, df: pd.DataFrame) -> tuple[bool, str, list[str]]:
        close = df["close"]
        ma5 = close.rolling(5).mean().iloc[-1]
        ma10 = close.rolling(10).mean().iloc[-1]
        ma20 = close.rolling(20).mean()
        ma20_now = ma20.iloc[-1]
        ma60 = close.rolling(60).mean().iloc[-1] if len(df) >= 60 else 0.0

        conditions: list[str] = []
        signals: list[str] = []
        passed = True

        if ma5 > ma10 > ma20_now > ma60:
            conditions.append("均线多头排列 ✓")
            signals.append("趋势-均线多头排列")
        else:
            conditions.append("均线非多头排列")
            passed = False

        ma20_prev = close.rolling(20).mean().iloc[-2] if len(df) >= 21 else 0.0
        if ma20_now > ma20_prev:
            conditions.append("MA20上翘 ✓")
        else:
            conditions.append("MA20走平/下弯")
            passed = False

        last_close = close.iloc[-1]
        if last_close > ma20_now:
            conditions.append("价格在MA20之上 ✓")
            signals.append(f"趋势-MA20支撑: 收盘价{last_close:.2f}在MA20({ma20_now:.2f})之上")
        else:
            conditions.append("价格跌破MA20")
            passed = False

        if not self.config.trend_filter_enabled:
            passed = True

        return passed, " | ".join(conditions), signals

    @staticmethod
    def _find_support(df: pd.DataFrame) -> float:
        lows = df["low"].tail(20).values
        if len(lows) == 0:
            return float(df["close"].iloc[-1]) * 0.95
        q1 = float(np.percentile(lows, 25))
        current = float(df["close"].iloc[-1])
        return min(q1, current * 0.95)

    @staticmethod
    def _find_resistance(df: pd.DataFrame) -> float:
        highs = df["high"].tail(20).values
        if len(highs) < 3:
            return float(df["close"].iloc[-1]) * 1.05
        q3 = float(np.percentile(highs, 75))
        current = float(df["close"].iloc[-1])
        return max(q3, current * 1.03)

    @staticmethod
    def _calculate_rating(trend_passed: bool, score: float, risk_reward: float) -> str:
        """计算评级。阈值对齐全市场评分分布：
           S/strong_buy ≥1.5(前0.2%), A/buy ≥1.0(前7%), B/watch ≥0.5(前28%)。"""
        if not trend_passed:
            return Rating.avoid
        if score >= 1.5:
            return Rating.strong_buy
        if score >= 1.0:
            return Rating.buy
        if score >= 0.5:
            return Rating.watch
        if score >= 0:
            return Rating.attention
        return Rating.avoid

    def scan_market(
        self,
        symbols: list[str],
        data_loader,
        top_n: int = 20,
        min_bars: int = 60,
        ic_tracker=None,
    ) -> list[AnalysisResult]:
        all_results: list[AnalysisResult] = []
        failed = 0
        for symbol in symbols:
            try:
                df = data_loader(symbol)
                if len(df) < min_bars:
                    continue
                result = self.analyze(df, symbol)
                all_results.append(result)
            except Exception:
                failed += 1
                if failed <= 5 or failed % 100 == 0:
                    logger.debug(f"扫描 [{symbol}] 跳过 (异常#{failed})")
                continue

        # 记录评分到 IC Tracker
        if ic_tracker is not None:
            scores_by_symbol = {}
            for r in all_results:
                if r.strategy_scores:
                    scores_by_symbol[r.symbol] = dict(r.strategy_scores)
            if scores_by_symbol:
                date_str = date.today().strftime("%Y-%m-%d")
                ic_tracker.record_scores(date_str, scores_by_symbol)

        def sort_key(r: AnalysisResult) -> tuple:
            rating_order = {
                Rating.strong_buy: 0,
                Rating.buy: 1,
                Rating.watch: 2,
                Rating.attention: 3,
                Rating.avoid: 4,
            }
            return (rating_order.get(r.rating, 5), -r.total_score)

        all_results.sort(key=sort_key)
        return all_results[:top_n]
