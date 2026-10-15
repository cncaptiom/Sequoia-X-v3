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
    fundamental_strategy,
    kdj_strategy,
    ma_cross_strategy,
    ma_volume_strategy,
    macd_rsi_strategy,
    mean_reversion_strategy,
    turtle_trade_strategy,
    volume_price_strategy,
)
from sequoia_x.core.logger import get_logger
from sequoia_x.regime import REGIME_BIAS as _REGIME_BIAS
from sequoia_x.signal.quality import winsorize, standardize

logger = get_logger(__name__)


# ── 配置 ──

@dataclass
class RatingConfig:
    """与 Flutter StrategyConfig 对应的评级配置。"""
    ma_volume_enabled: bool = False       # 回测确认：48种参数组合全部负收益
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

    volume_price_enabled: bool = False    # 回测确认：IR为负、换手率高
    volume_surge_threshold: float = 3.0

    atr_channel_enabled: bool = True
    atr_trend_period: int = 30
    atr_channel_window: int = 20
    atr_exit_score: float = 0.2
    atr_multiple: float = 2.0

    macd_rsi_enabled: bool = False        # 回测确认：IR最差、IC正比例仅33.9%
    ma_cross_enabled: bool = True

    kdj_enabled: bool = True
    kdj_k_period: int = 9
    kdj_d_period: int = 3
    kdj_j_period: int = 3

    fundamental_enabled: bool = True

    trend_filter_enabled: bool = True

    # 默认权重（2026-08 全量因子验证校准）：
    #   横截面IC实证：meanReversion(+0.10) > kdj(+0.03) > turtleTrade(+0.006) > fundamental(-0.06)
    #   > atrChannel(bear市正/全量负) > maCross(-0.24最差,大幅降权)
    #   超跌反转信号20日超额+0.59%胜率56.9%，熊市胜率85.4%，故 reversal 主导
    weight_ma_volume: float = 0.0
    weight_turtle_trade: float = 0.125
    weight_mean_reversion: float = 0.20
    weight_volume_price: float = 0.0
    weight_atr_channel: float = 0.05
    weight_macd_rsi: float = 0.0
    weight_ma_cross: float = 0.05
    weight_kdj: float = 0.125
    weight_fundamental: float = 0.20

    # ── 策略类别与市场状态权重映射 ──
    # 各策略所属类别
    STRATEGY_CATEGORIES = {
        "trend": ["ma_volume", "turtle_trade", "atr_channel"],
        "momentum": ["macd_rsi", "ma_cross"],
        "reversal": ["mean_reversion", "volume_price", "kdj"],
        "fundamental": ["fundamental"],
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
        "fundamental": "weight_fundamental",
    }
    # ICTracker 策略名 → WEIGHT_ATTRS 键名映射
    IR_STRATEGY_MAP = {
        "maVolume": "ma_volume",
        "turtleTrade": "turtle_trade",
        "meanReversion": "mean_reversion",
        "volumePrice": "volume_price",
        "atrChannel": "atr_channel",
        "macdRsi": "macd_rsi",
        "maCross": "ma_cross",
        "kdj": "kdj",
        "fundamental": "fundamental",
    }
    # 各市场状态下策略类别的偏向权重：唯一来源是 sequoia_x.regime.REGIME_BIAS，
    # 那里同时供 RegimeResult.strategy_bias 展示，避免两套口径打架。
    REGIME_BIAS = _REGIME_BIAS

    def apply_regime(self, regime_label: str) -> None:
        """根据市场状态动态调整各策略权重（每类权重均分给该类【启用】策略）。

        强牛→趋势权重提升，弱熊→反转权重提升，震荡→平均分配。
        使用 object.__setattr__ 绕过 dataclass 冻结。
        """
        bias = self.REGIME_BIAS.get(regime_label)
        if bias is None:
            return  # 未知状态，使用默认权重

        # 每类权重均分给该类启用的策略（禁用策略不参与，避免稀释权重）
        for category, cat_bias in bias.items():
            strategies = self.STRATEGY_CATEGORIES.get(category, [])
            enabled = [s for s in strategies if getattr(self, s + "_enabled", False)]
            if not enabled:
                continue
            per_strategy_weight = round(cat_bias / len(enabled), 4)
            for s in enabled:
                attr = self.WEIGHT_ATTRS.get(s)
                if attr:
                    object.__setattr__(self, attr, per_strategy_weight)

    def apply_ir_weights(self, ir_summary: dict[str, dict]) -> None:
        """【已退出生产路径】按 ICTracker 的 IR 调整策略权重——仅供诊断脚本调用。

        原逻辑：IR 越高权重越大，IR <= 0 的策略权重直接清零，正 IR 策略按比例
        分配权重，不足 3 个正 IR 则保留 apply_regime 设置的权重。

        2026-10-14 停用（D5）。原因是它让权重由【噪声 IC 的符号】决定：
          · 光是给 IC 历史去重，9 个策略里就有 3 个的归零判定翻转
            （fundamental 保留→归零，kdj / turtleTrade 归零→保留）
          · 去重后 turtleTrade 的 IR 只有 +0.0033、kdj +0.098，全落在 ±0.2 内
          · 样本只有约 42 个交易日（5 日序列）/ 35 个（20 日序列），且全部落在
            同一个市场状态（熊市），无法外推到别的状态
          · 用 Newey-West 校正重叠窗口后 θ = 2·SE(IR) ≈ 0.28~0.80，只有
            meanReversion 勉强过关；换成 Harvey-Liu-Zhu 的多重检验门槛（k=3）
            则一个都不剩

        启用条件（预注册，不要改）：等每个市场状态下的【独立】IC 窗口都 ≥ 20 个时，
        按下列规则重写本方法，不要临时按当时的结果挑参数——
            SE 用 Newey-West，L = h − 1（重叠窗口，Hansen-Hodrick / Newey-West）
            按 lookforward 分开算 IR 与 SE，绝不跨期限合并（见 signal/quality.py）
            θ = k × SE(IR)，k = 3（Harvey-Liu-Zhu 多重检验门槛）
            g(IR) = 1            若 |IR| < θ     → 保留 apply_regime 的权重
                  = min(IR/θ, 2) 若 IR ≥ +θ     → 上调，上限 2 倍
                  = 0            若 IR ≤ −θ     → 清零
            w = w_regime × g，最后统一归一化

        Args:
            ir_summary: ICTracker.get_summary() 返回的摘要
        """
        # 正 IR 策略收集；IR<=0 归零；无 IR 数据（未跟踪）的策略保留当前权重（中性）
        ir_values: dict[str, float] = {}
        retained: dict[str, float] = {}
        for ic_name, attr_key in self.IR_STRATEGY_MAP.items():
            # 禁用策略不参与 IR 加权，避免历史 IR 数据重新激活
            if not getattr(self, attr_key + "_enabled", False):
                continue
            info = ir_summary.get(ic_name)
            if info is None:
                retained[attr_key] = float(getattr(self, self.WEIGHT_ATTRS[attr_key]))
                continue
            ir = info.get("ir_60d", 0) or info.get("ir_20d", 0)
            if ir > 0:
                ir_values[attr_key] = ir
            else:
                object.__setattr__(self, self.WEIGHT_ATTRS[attr_key], 0.0)

        # 如果只有不到3个策略有正 IR，说明 IR 数据不足：
        # 保留 apply_regime 已设置的权重（市场状态导向），不再回退等权，
        # 避免把负 IC 策略（如 maCross）按 0.25 等权恢复
        if len(ir_values) < 3:
            return

        total_ir = sum(ir_values.values())
        if total_ir <= 0:
            return

        # 清零正 IR 以外的策略（保留的策略稍后恢复）
        for attr in self.WEIGHT_ATTRS.values():
            object.__setattr__(self, attr, 0.0)

        # 正 IR 策略按比例分配
        for attr_key, ir in ir_values.items():
            attr = self.WEIGHT_ATTRS[attr_key]
            object.__setattr__(self, attr, round(ir / total_ir, 4))

        # 无 IR 数据的策略保留原权重
        for attr_key, w in retained.items():
            object.__setattr__(self, self.WEIGHT_ATTRS[attr_key], w)

        # 重新归一化到 1.0
        total = sum(float(getattr(self, a)) for a in self.WEIGHT_ATTRS.values())
        if total > 0:
            for attr in self.WEIGHT_ATTRS.values():
                object.__setattr__(self, attr, round(float(getattr(self, attr)) / total, 4))

    def apply_equal_weights(self) -> None:
        """重置为类别等权：每类 0.25，类内均分给启用策略。"""
        for category, strategies in self.STRATEGY_CATEGORIES.items():
            enabled = [s for s in strategies if getattr(self, s + "_enabled", False)]
            if not enabled:
                continue
            per = round(0.25 / len(enabled), 4)
            for s in enabled:
                attr = self.WEIGHT_ATTRS.get(s)
                if attr:
                    object.__setattr__(self, attr, per)

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
            "fundamentalEnabled": self.fundamental_enabled,
            "trendFilterEnabled": self.trend_filter_enabled,
            "weightMaVolume": self.weight_ma_volume,
            "weightTurtleTrade": self.weight_turtle_trade,
            "weightMeanReversion": self.weight_mean_reversion,
            "weightVolumePrice": self.weight_volume_price,
            "weightAtrChannel": self.weight_atr_channel,
            "weightMacdRsi": self.weight_macd_rsi,
            "weightMaCross": self.weight_ma_cross,
            "weightKdj": self.weight_kdj,
            "weightFundamental": self.weight_fundamental,
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
    # 各策略标准化分（截面 Z-score，非类内平均），供手机端展示策略间差异；不参与评分
    per_strategy_scores: dict[str, float] = field(default_factory=dict)
    # 各策略原始分（正交化前），供 IC 追踪使用；不用于手机端展示
    raw_strategy_scores: dict[str, float] = field(default_factory=dict)
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
            "per_strategy_scores": {k: round(v, 1) for k, v in self.per_strategy_scores.items()},
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

    # 唯一策略分类（与 RatingConfig.STRATEGY_CATEGORIES 同构，键为评分用的 camelCase 名）
    # 评分、类别展示、正交化均以此为准
    RATING_GROUPS = {
        "trend": ["maVolume", "turtleTrade", "atrChannel"],
        "momentum": ["macdRsi", "maCross"],
        "reversal": ["meanReversion", "volumePrice", "kdj"],
        "fundamental": ["fundamental"],
    }

    def __init__(self, config: Optional[RatingConfig] = None,
                 data_engine=None):
        self.config = config or RatingConfig()
        self._data_engine = data_engine
        self._financial_cache: dict[str, dict] = {}
        # efinance 全市场财务数据缓存（None=未加载，{} = efinance 不可用）
        self._efinance_all: dict | None = None
        # 重置 baostock 登录状态
        RatingEngine._bs_logged_in = False

    def _get_ohlcv(self, symbol: str):
        """获取 OHLCV：优先用注入的 DataEngine；未注入时惰性缓存一个（避免每股新建）。"""
        if self._data_engine is not None:
            return self._data_engine.get_ohlcv(symbol)
        if getattr(RatingEngine, '_lazy_data_engine', None) is None:
            from sequoia_x.data.engine import DataEngine
            from sequoia_x.core.config import get_settings
            RatingEngine._lazy_data_engine = DataEngine(get_settings())
        return RatingEngine._lazy_data_engine.get_ohlcv(symbol)

    def analyze(self, df: pd.DataFrame, symbol: str = "", name: str = "",
                financial: dict | None = None) -> AnalysisResult:
        """对单只股票的 DataFrame 执行完整分析。

        Args:
            df: OHLCV DataFrame
            symbol: 股票代码
            name: 股票名称
            financial: 可选，财务数据字典（如不传且需要基本面策略时将尝试自动获取）
        """
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

        total_score = 0.0
        strategy_scores: dict[str, float] = {}

        # 如果基本面策略启用但没有传入财务数据，只在趋势通过的股票上获取
        if self.config.fundamental_enabled and financial is None and symbol and trend_passed:
            financial = self._fetch_financial(symbol)

        strategy_map: list[tuple[str, bool, callable]] = [
            ("maVolume", self.config.ma_volume_enabled, lambda: ma_volume_strategy(
                df, self.config.ma_short, self.config.ma_long, self.config.volume_threshold)),
            ("turtleTrade", self.config.turtle_trade_enabled, lambda: turtle_trade_strategy(
                df, self.config.break_period, self.config.new_high_period)),
            ("meanReversion", self.config.mean_reversion_enabled, lambda: mean_reversion_strategy(
                df, self.config.rsi_period, self.config.rsi_overbought, self.config.rsi_oversold)),
            ("volumePrice", self.config.volume_price_enabled, lambda: volume_price_strategy(
                df, volume_surge_threshold=self.config.volume_surge_threshold)),
            ("atrChannel", self.config.atr_channel_enabled, lambda: atr_channel_strategy(
                df, self.config.atr_trend_period, self.config.atr_channel_window,
                self.config.atr_exit_score)),
            ("macdRsi", self.config.macd_rsi_enabled, lambda: macd_rsi_strategy(df)),
            ("maCross", self.config.ma_cross_enabled, lambda: ma_cross_strategy(df)),
            ("kdj", self.config.kdj_enabled, lambda: kdj_strategy(
                df, self.config.kdj_k_period, self.config.kdj_d_period, self.config.kdj_j_period)),
            ("fundamental", self.config.fundamental_enabled,
             lambda: fundamental_strategy(df, financial)),
        ]

        for strategy_key, enabled, fn in strategy_map:
            if enabled:
                try:
                    result = fn()
                    strategy_scores[strategy_key] = result.score
                    signals.extend(result.signals)
                except Exception as exc:
                    logger.log(5, f"策略 [{strategy_key}] 执行异常: {exc}")
                    continue

        # ── 策略正交化 + 配置权重驱动评分（组合逻辑统一在 _combine_scores） ──
        total_score, ortho_scores, category_scores = self._combine_scores(strategy_scores)

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

        # 正交化后的评分用于输出（同类策略等值）
        output_strategy_scores = {k: round(v, 1) for k, v in ortho_scores.items()}
        # 定级与显示用同一个数（理由见 _cross_sectional_standardize）
        stored_score = round(total_score, 1)
        rating = self._calculate_rating(trend_passed, stored_score)

        return AnalysisResult(
            symbol=symbol,
            name=name,
            trend_passed=trend_passed,
            trend_description=trend_desc,
            total_score=stored_score,
            rating=rating,
            strategy_scores=output_strategy_scores,
            raw_strategy_scores=strategy_scores,
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
            "fundamental": self.config.weight_fundamental,
        }
        enabled = {"maVolume": self.config.ma_volume_enabled,
                   "turtleTrade": self.config.turtle_trade_enabled,
                   "meanReversion": self.config.mean_reversion_enabled,
                   "volumePrice": self.config.volume_price_enabled,
                   "atrChannel": self.config.atr_channel_enabled,
                   "macdRsi": self.config.macd_rsi_enabled,
                   "maCross": self.config.ma_cross_enabled,
                   "kdj": self.config.kdj_enabled,
                   "fundamental": self.config.fundamental_enabled}
        filtered = {k: v for k, v in weights.items() if enabled.get(k, True)}
        total = sum(filtered.values())
        if total == 0:
            return {}
        return {k: v / total for k, v in filtered.items()}

    def _apply_ic_decay_weights(self, ic_tracker) -> None:
        """【已退出生产路径】IC 衰减降权，理由同 apply_ir_weights。

        它建立在同一份 2 个月、单一市场状态的 IC 历史上，因此同样不再驱动权重。
        """
        try:
            base = self._get_normalized_weights()
            adjusted = ic_tracker.adjust_weights(base)
            if not adjusted:
                return
            for k, w in adjusted.items():
                snake = self.config.IR_STRATEGY_MAP.get(k)
                attr = self.config.WEIGHT_ATTRS.get(snake)
                if attr:
                    object.__setattr__(self.config, attr, round(w, 4))
            total = sum(float(getattr(self.config, a))
                        for a in self.config.WEIGHT_ATTRS.values())
            if total > 0:
                for attr in self.config.WEIGHT_ATTRS.values():
                    object.__setattr__(self.config, attr,
                                       round(float(getattr(self.config, attr)) / total, 4))
        except Exception as exc:
            logger.log(5, f"IC 衰减调权失败: {exc}")

    def _strategy_weight(self, strategy_key: str) -> float:
        """读取策略的配置权重（camelCase 评分名 → config.weight_* 属性）。"""
        snake = self.config.IR_STRATEGY_MAP.get(strategy_key)
        if not snake:
            return 0.0
        attr = self.config.WEIGHT_ATTRS.get(snake)
        if not attr:
            return 0.0
        return float(getattr(self.config, attr))

    def _combine_scores(self, strategy_scores: dict[str, float]) -> tuple[float, dict[str, float], dict[str, float]]:
        """加权正交化合成：返回 (total_score, 类内均分后的每策略分, 类别均分)。

        类别权重 = 该类【启用且权重>0】的策略的配置权重之和（归一化），
        由 apply_regime（生产唯一来源）/ apply_equal_weights 改写 config.weight_* 驱动；
        同类内取平均（正交化），权重为 0 的策略不参与评分。
        """
        cat_weights: dict[str, float] = {}
        for cat, members in self.RATING_GROUPS.items():
            active = [s for s in members if s in strategy_scores and self._strategy_weight(s) > 0]
            if active:
                cat_weights[cat] = sum(self._strategy_weight(s) for s in active)
        total_cat = sum(cat_weights.values())
        if total_cat <= 0:  # 极端兜底：所有启用策略权重为 0 → 各类等权
            cat_weights = {c: 1.0 for c in cat_weights}
            total_cat = len(cat_weights)

        total_score = 0.0
        ortho: dict[str, float] = {}
        category_scores: dict[str, float] = {}
        for cat, cat_w in cat_weights.items():
            members = [s for s in self.RATING_GROUPS[cat]
                       if s in strategy_scores and self._strategy_weight(s) > 0]
            if not members:
                continue
            avg_score = sum(strategy_scores[s] for s in members) / len(members)
            total_score += avg_score * (cat_w / total_cat)
            category_scores[cat] = round(avg_score, 1)
            for s in members:
                ortho[s] = round(avg_score, 2)
        return total_score, ortho, category_scores

    def _cross_sectional_standardize(self, results: list[AnalysisResult]) -> None:
        """对全市场评级结果做截面标准化：每策略原始分去极值+Z-score，重算总分与评级。

        使不同策略的评分尺度可比（如 fundamental 满分~10 与 kdj 满分~5，等权相加不公平）。
        样本不足（<20 只）时保留原始分不动。
        """
        if len(results) < 20:
            return

        raw_by_strat: dict[str, list[tuple[str, float]]] = {}
        for r in results:
            for s, v in r.raw_strategy_scores.items():
                raw_by_strat.setdefault(s, []).append((r.symbol, v))

        std_by_strat: dict[str, dict[str, float]] = {}
        for s, pairs in raw_by_strat.items():
            if len(pairs) < 10:
                continue
            trimmed = winsorize([v for _, v in pairs], 1.0, 99.0)
            zs = standardize(trimmed)
            std_by_strat[s] = dict(zip([sym for sym, _ in pairs], zs))

        for r in results:
            std_scores: dict[str, float] = {}
            for s, v in r.raw_strategy_scores.items():
                m = std_by_strat.get(s)
                std_scores[s] = m.get(r.symbol, v) if m else v
            total, ortho, cat = self._combine_scores(std_scores)
            # 定级与显示必须用**同一个数**：先圆整存下，后面一律引用它。
            # 此前是「存 round(total,1)、按未圆整的 total 定级」，于是在阈值附近
            # 出现「显示 0.5 却判 attention」「显示 -0.0 却判 avoid」这类自相矛盾。
            r.total_score = round(total, 1)
            r.strategy_scores = {k: round(v, 1) for k, v in ortho.items()}
            r.per_strategy_scores = {k: round(v, 1) for k, v in std_scores.items()}
            r.category_scores = cat
            r.rating = self._calculate_rating(r.trend_passed, r.total_score)

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

    # baostock 财务查询：网络类错误码才触发重连；"无数据"等正常错误码不重连
    _BS_NETWORK_ERRORS = frozenset({
        "10001001", "10002001", "10002002", "10002003", "10002004",
        "10002005", "10002006", "10002007", "10002008",
    })
    _BS_QUERY_INTERVAL = 0.15  # 财务查询节流间隔（秒），控制请求频率避免被限流踢线
    _bs_last_query_ts = 0.0

    @classmethod
    def _bs_throttle(cls) -> None:
        """两次 baostock 查询间至少间隔 _BS_QUERY_INTERVAL，降低被服务端踢连接的概率。"""
        import time
        now = time.monotonic()
        wait = cls._bs_last_query_ts + cls._BS_QUERY_INTERVAL - now
        if wait > 0:
            time.sleep(wait)
        cls._bs_last_query_ts = time.monotonic()

    @classmethod
    def _bs_query_with_reconnect(cls, query_fn, *args, **kwargs):
        """执行 baostock 查询；连接被服务端断开（WinError 10054 等）时 logout+login 重试。

        网络类错误码（10xx/1002xxxx）或抛异常才重连；无数据等正常错误码直接返回。
        """
        import baostock as bs
        import time
        rs = None
        for attempt in range(3):
            cls._bs_throttle()
            try:
                rs = query_fn(*args, **kwargs)
            except Exception:
                rs = None
            if rs is not None and str(getattr(rs, "error_code", "0")) == "0":
                return rs
            err = getattr(rs, "error_code", None) if rs is not None else None
            is_network = rs is None or str(err) in cls._BS_NETWORK_ERRORS
            if not is_network:
                return rs  # 无数据等正常错误码：重试无意义，直接返回
            if attempt < 2:
                try:
                    bs.logout()
                    bs.login()
                except Exception:
                    pass
                time.sleep(1.0 * (attempt + 1))  # 退避后重试
        return rs

    def _fetch_financial(self, symbol: str) -> dict | None:
        """获取股票财务数据（带缓存）。优先 efinance 全市场批量，失败回退 baostock。"""
        if symbol in self._financial_cache:
            return self._financial_cache[symbol]

        # 优先 efinance 全市场批量（一次拉全市场业绩，更快且含净利率/毛利率）
        if self._efinance_all is None:
            try:
                from sequoia_x.data.efinance_financial import load_financial_all
                self._efinance_all = load_financial_all()
            except Exception:
                self._efinance_all = {}
        efin = self._efinance_all.get(symbol)
        if efin:
            efin = dict(efin)
            eps = efin.get("eps")
            if eps and eps > 0:
                try:
                    df = self._get_ohlcv(symbol)
                    if df is not None and not df.empty:
                        efin["pe"] = round(float(df["close"].iloc[-1]) / eps, 2)
                except Exception:
                    pass
            self._financial_cache[symbol] = efin
            return efin

        # 回退：baostock 逐股查询（efinance 不可用或未覆盖该股）
        # 延迟登录：整个扫描周期只登录一次
        if not getattr(RatingEngine, '_bs_logged_in', False):
            try:
                import baostock as bs
                bs.login()
                RatingEngine._bs_logged_in = True
            except Exception:
                return None

        try:
            import baostock as bs
            bs_code = self._symbol_to_bs(symbol)
            if not bs_code:
                return None

            fin = {}
            import datetime
            today = datetime.date.today()
            yr = today.year

            # baostock 财务字段为小数（实测 600000: roeAvg=0.0644=6.44%、npMargin=0.2897、
            # YOYNI=0.0997），此处 ×100 转百分数；fundamental_strategy 阈值按百分数校准。
            # 只查最新一个季度（减少 baostock 查询量，避免限流）
            for year_candidate in [yr, yr - 1]:
                for q in [4, 3, 2, 1]:
                    rs = self._bs_query_with_reconnect(
                        bs.query_profit_data, code=bs_code, year=year_candidate, quarter=q
                    )
                    if rs.error_code == "0" and rs.next():
                        d = dict(zip(rs.fields, rs.get_row_data()))
                        if d.get("roeAvg"):
                            fin["roe"] = round(float(d["roeAvg"]) * 100, 2)
                            fin["eps"] = float(d.get("epsTTM", 0) or 0)
                            fin["net_profit_margin"] = round(float(d.get("npMargin", 0) or 0) * 100, 2)
                        break
                if "roe" in fin:
                    break

            # 净利润增长率（只查一次）
            for year_candidate in [yr, yr - 1]:
                for q in [4, 3, 2, 1]:
                    rs = self._bs_query_with_reconnect(
                        bs.query_growth_data, code=bs_code, year=year_candidate, quarter=q
                    )
                    if rs.error_code == "0" and rs.next():
                        d = dict(zip(rs.fields, rs.get_row_data()))
                        if d.get("YOYNI"):
                            fin["profit_growth"] = round(float(d["YOYNI"]) * 100, 2)
                        break
                if "profit_growth" in fin:
                    break

            # 现金流（只查一次）
            for year_candidate in [yr, yr - 1]:
                for q in [4, 3, 2, 1]:
                    rs = self._bs_query_with_reconnect(
                        bs.query_cash_flow_data, code=bs_code, year=year_candidate, quarter=q
                    )
                    if rs.error_code == "0" and rs.next():
                        d = dict(zip(rs.fields, rs.get_row_data()))
                        if d.get("CFOToNP"):
                            fin["cfo_to_np"] = float(d["CFOToNP"])
                        break
                if "cfo_to_np" in fin:
                    break

            # PE = 价格 / EPS（使用已缓存的OHLCV数据，不需额外查询）
            if fin.get("eps") and fin["eps"] > 0:
                try:
                    df = self._get_ohlcv(symbol)
                    if df is not None and not df.empty:
                        last_close = float(df["close"].iloc[-1])
                        fin["pe"] = round(last_close / fin["eps"], 2)
                except Exception:
                    pass

            self._financial_cache[symbol] = fin if fin else None
            return self._financial_cache[symbol]
        except Exception as exc:
            logger.log(5, f"[{symbol}] 财务数据获取失败: {exc}")
            self._financial_cache[symbol] = None
            return None

    @staticmethod
    def _symbol_to_bs(symbol: str) -> str:
        sym = symbol.strip().zfill(6)
        prefix = "sh." if sym.startswith(("6", "9")) else "sz."
        return prefix + sym

    @staticmethod
    def _calculate_rating(trend_passed: bool, score: float) -> str:
        """软过滤评级：趋势不过时评级上限降级（最高 watch），不直接丢弃评分。

        阈值对齐全市场评分分布：
          S/strong_buy ≥1.5(前0.2%), A/buy ≥1.0(前7%), B/watch ≥0.5(前28%)。
        """
        if not trend_passed:
            if score >= 0.5:
                return Rating.watch
            if score >= 0:
                return Rating.attention
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

        # 策略权重在进入本函数前已由 apply_regime（市场状态）定好。
        # 此处不再用 IC/IR 调权——理由见 apply_ir_weights 的注释。

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

        # 截面标准化：使各策略评分尺度可比，重算 total_score 与评级
        self._cross_sectional_standardize(all_results)

        # 记录评分到 IC Tracker（用正交化前的原始分，保证每策略 IC 相互独立）
        if ic_tracker is not None:
            scores_by_symbol = {}
            for r in all_results:
                if r.raw_strategy_scores:
                    scores_by_symbol[r.symbol] = dict(r.raw_strategy_scores)
            if scores_by_symbol:
                date_str = date.today().strftime("%Y-%m-%d")
                ic_tracker.record_scores(date_str, scores_by_symbol)

        # 扫描结束，登出 baostock
        if getattr(RatingEngine, '_bs_logged_in', False):
            try:
                import baostock as bs
                bs.logout()
            except Exception:
                pass
            RatingEngine._bs_logged_in = False

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
