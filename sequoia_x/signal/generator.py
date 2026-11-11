"""信号生成器：基于策略输出和技术分析生成结构化买卖信号。"""

from datetime import date
from typing import Optional, Callable

import pandas as pd
import numpy as np

from sequoia_x.core.logger import get_logger
from sequoia_x.signal.models import TradeSignal
from sequoia_x.data.engine import DataEngine
from sequoia_x.risk import PositionSizer, compute_atr
from sequoia_x.utils.name_resolver import get_name_resolver
from sequoia_x.signal.pricing import (
    EntryZone,
    build_entry_zone,
    compute_risk_reward,
    entry_reference,
    original_stop_price,
    pick_target,
    plan_risk_ok,
    resistance_candidates_from_bars,
    stop_price,
    support_candidates_from_bars,
)

logger = get_logger(__name__)


class SignalGenerator:
    """交易信号生成器。

    将策略选出的股票代码，结合技术分析和风险管理，
    生成带有买入区间、止损止盈、仓位建议的结构化 TradeSignal。
    """

    def __init__(self, engine: DataEngine, total_capital: float = 1_000_000.0,
                 data_as_of: Optional[str] = None):
        self.engine = engine
        self.total_capital = total_capital
        # 全市场最新行情日。提供时，行情落后的个股（停牌/数据缺失）不再出信号，
        # 避免旧行情被当成当日推荐（2026-11-09 审查 #7）。
        self.data_as_of = data_as_of

    def generate(
        self,
        symbol: str,
        strategies: list[str],
        market_regime: str = "",
    ) -> Optional[TradeSignal]:
        """对单只股票生成完整交易信号。

        Args:
            symbol: 股票代码。
            strategies: 触发该信号的策略名称列表。
            market_regime: 当前市场状态。

        Returns:
            TradeSignal 或 None（数据不足时返回 None）。
        """
        df = self.engine.get_ohlcv(symbol)
        if df.empty or len(df) < 30:
            return None

        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else last
        current_price = float(last["close"])
        last_date = str(last["date"])

        # 数据新鲜度：日期必须先严格解析再比较。
        # 此前直接做字符串比较、且解析失败被 `except: pass` 吞掉，
        # 脏日期可以绕过过期检查继续出信号（复审 R2-06）。
        try:
            last_dt = date.fromisoformat(last_date)
        except (TypeError, ValueError):
            logger.warning(f"[{symbol}] 行情日期无法解析 ({last_date!r})，跳过")
            return None

        if self.data_as_of:
            try:
                as_of_dt = date.fromisoformat(self.data_as_of)
            except (TypeError, ValueError):
                logger.warning(f"[{symbol}] data_as_of 无法解析 ({self.data_as_of!r})，跳过")
                return None
            if last_dt < as_of_dt:
                logger.log(5, f"[{symbol}] 行情落后于全市场最新日 "
                              f"({last_date} < {self.data_as_of})，跳过")
                return None

        if (date.today() - last_dt).days > 7:
            logger.log(5, f"[{symbol}] 数据过期 ({last_date})，跳过")
            return None

        # ---- 计算技术指标用于生成信号 ----
        closes = df["close"]
        highs = df["high"]
        lows = df["low"]
        volumes = df["volume"]

        # 均线系统
        ma5 = float(closes.rolling(5).mean().iloc[-1])
        ma10 = float(closes.rolling(10).mean().iloc[-1])
        ma20 = float(closes.rolling(20).mean().iloc[-1])

        # ATR 和波动率
        atr_val = compute_atr(highs, lows, closes)
        volatility_20d = float(closes.tail(20).std() / closes.tail(20).mean())

        # 风险指标
        atr_ratio = atr_val / current_price if current_price > 0 else 0  # ATR/价格比
        # VaR 95%: 近60日收益率第5百分位
        returns_60d = closes.tail(60).pct_change().dropna()
        var_95 = float(abs(returns_60d.quantile(0.05))) if len(returns_60d) >= 20 else 0.05
        # 近期最大回撤（20日）
        recent_peak = float(closes.tail(20).max())
        recent_trough = float(closes.tail(20).min())
        recent_max_dd = (recent_trough - recent_peak) / recent_peak if recent_peak > 0 else 0

        # 近期高低点
        recent_low_10 = float(lows.tail(10).min())
        recent_low_20 = float(lows.tail(20).min())

        # 成交量
        vol_ma20 = float(volumes.tail(20).mean())
        vol_ratio = float(last["volume"]) / vol_ma20 if vol_ma20 > 0 else 1.0

        # ---- 生成买入区间（唯一定义见 signal/pricing.py） ----
        supports = support_candidates_from_bars(closes, lows)
        resistances = resistance_candidates_from_bars(highs)

        # 原 ATR 止损作为候选，与区间下轨取更深者
        original_stop = original_stop_price(current_price, atr_val, ma20, recent_low_10)

        zone = build_entry_zone(current_price, atr_val, supports)
        stop_loss = 0.0
        target_1 = 0.0
        target_2 = None
        target_is_fallback = False
        entry_ref = 0.0
        rr = None
        if zone.status != "none":
            candidate_stop = stop_price(zone.low, atr_val, original_stop)
            entry_ref = entry_reference(zone, current_price) or 0.0
            if plan_risk_ok(entry_ref, candidate_stop):
                stop_loss = candidate_stop
                tgt = pick_target(resistances, entry_ref, atr_val)
                target_1, target_is_fallback = tgt.value, tgt.is_fallback
                above = sorted({round(r, 2) for r in resistances if r > target_1 * 1.02})
                target_2 = above[0] if above else None
                rr = compute_risk_reward(entry_ref, stop_loss, target_1)
            else:
                # 波动过小：止损距离小于现价 1%，与「不再给数字」的处理一致
                zone = EntryZone(status="none", reason="止损距离不足")

        # ---- 计算建议仓位（终值由 main 按组合总仓位重新分配） ----
        position_pct = 0.0
        position_error = False
        atr_val_rounded = round(atr_val, 2)
        if zone.status != "none":
            try:
                sizer = PositionSizer(total_capital=self.total_capital)
                position = sizer.calculate(symbol, df, entry_price=entry_ref or current_price)
                position_pct = position.position_pct
                atr_val_rounded = position.atr
            except Exception as exc:
                # fail closed：风险计算失败不能给默认仓位继续建议买入
                position_pct = 0.0
                position_error = True
                logger.warning(f"[{symbol}] 仓位计算失败，本次信号标记为不可执行: {exc}")

        # ---- 计算形态分（内部字段仍为 confidence） ----
        confidence, confidence_details = self._calc_confidence(
            strategies=strategies,
            vol_ratio=vol_ratio,
            trend_bullish=(ma5 > ma10 > ma20),
            volatility_low=(volatility_20d < 0.03),
            volume_active=(vol_ratio > 1.2),
            near_support=(current_price / recent_low_20 < 1.1),
            df=df,
            market_regime=market_regime,
            var_95=var_95,
            atr_ratio=atr_ratio,
            recent_max_dd=recent_max_dd,
        )

        # ---- 生成选中理由与触发条件 ----
        if zone.status == "none":
            entry_low = entry_high = 0.0
            entry_condition = f"{zone.reason}，本期只观察"
            rationale = f"{'; '.join(strategies)} 策略触发 | {zone.reason} | 现价 {current_price:.2f}"
        else:
            entry_low, entry_high = zone.low, zone.high
            entry_condition = self._entry_condition_text(zone, current_price)
            rationale = self._build_rationale(
                strategies=strategies,
                vol_ratio=vol_ratio,
                atr_val=atr_val_rounded,
                current_price=current_price,
                entry_low=entry_low,
                entry_high=entry_high,
                stop_loss=stop_loss,
                target_1=target_1,
            )

        # 获取股票名称
        name = get_name_resolver().get_name(symbol)

        return TradeSignal(
            symbol=symbol,
            name=name,
            strategies=strategies,
            current_price=round(current_price, 2),
            last_date=last_date,
            entry_zone_low=round(entry_low, 2),
            entry_zone_high=round(entry_high, 2),
            entry_condition=entry_condition,
            stop_loss=round(stop_loss, 2),
            take_profit_1=round(target_1, 2) if target_1 else 0.0,
            take_profit_2=round(target_2, 2) if target_2 else None,
            target_is_fallback=target_is_fallback,
            max_hold_days=20,
            suggested_position_pct=round(position_pct, 4),
            position_error=position_error,
            atr_value=round(atr_val_rounded, 2),
            confidence=round(confidence, 3),
            score_details=confidence_details,
            zone_status=zone.status,
            zone_reason=zone.reason,
            entry_ref=round(entry_ref, 2),
            risk_reward=rr.value if rr else None,
            risk_reward_capped=rr.capped if rr else False,
            market_regime=market_regime,
            rationale=rationale,
        )

    def _calc_confidence(
        self,
        strategies: list[str],
        vol_ratio: float,
        trend_bullish: bool,
        volatility_low: bool,
        volume_active: bool,
        near_support: bool,
        df: pd.DataFrame = None,
        market_regime: str = "",
        var_95: float = 0.05,
        atr_ratio: float = 0.0,
        recent_max_dd: float = 0.0,
    ) -> tuple[float, dict]:
        """形态分计算 v3.3 — 6 维度评分 + 风险控制 + 市场修正。

        参考 综合评分.docx 框架，加入市场状态修正。

        Args:
            market_regime: 市场状态标签（强牛/弱牛/震荡/弱熊/强熊），用于修正置信度。

        Returns:
            (总置信度 0~1.0, 各维度细分得分)
        """
        score = 0.0
        details: dict[str, float] = {}

        # 1. 多策略确认（满分 0.30）— 策略数量越多越可信
        strategy_count = len(strategies)
        if strategy_count >= 3:
            score += 0.30
            details["多策略共振"] = 0.30
        elif strategy_count == 2:
            score += 0.15
            details["多策略共振"] = 0.15
        else:
            score += 0.10
            details["多策略共振"] = 0.10

        # 2. 趋势强度（满分 0.20）— 均线多头排列
        if trend_bullish:
            score += 0.20
            details["趋势强度"] = 0.20
        else:
            details["趋势强度"] = 0.0

        # 3. 量价配合（满分 0.15）— 放量上涨
        if volume_active:
            score += 0.15
            details["量价配合"] = 0.15
        else:
            # 缩量回调也给一定分数（回调健康）
            if vol_ratio < 1.0 and vol_ratio > 0.5:
                score += 0.05
                details["量价配合"] = 0.05
            else:
                details["量价配合"] = 0.0

        # 4. 安全边际（满分 0.15）— 接近支撑位
        if near_support:
            score += 0.15
            details["安全边际"] = 0.15
        else:
            details["安全边际"] = 0.0

        # 5. 低波动环境（满分 0.10）— 走势稳定
        if volatility_low:
            score += 0.10
            details["低波动环境"] = 0.10
        else:
            details["低波动环境"] = 0.0

        # 6. 量价共振加分（满分 0.10）— 放量 + 多头趋势
        if volume_active and trend_bullish:
            score += 0.10
            details["量价共振"] = 0.10
        else:
            details["量价共振"] = 0.0

        # 7. 风险控制修正（-0.10 ~ +0.05）— 波动太大降信心
        risk_score = 0.0
        # ATR 比率过高 → 波动剧烈 → 扣分
        if atr_ratio > 0.05:
            risk_score -= 0.05
        elif atr_ratio > 0.03:
            risk_score -= 0.02
        elif atr_ratio < 0.015:
            risk_score += 0.03  # 低波动加分
        # VaR 过高 → 尾部风险大 → 扣分
        if var_95 > 0.06:
            risk_score -= 0.03
        elif var_95 < 0.025:
            risk_score += 0.02
        # 近期最大回撤过大 → 扣分
        if recent_max_dd < -0.10:
            risk_score -= 0.05
        elif recent_max_dd < -0.05:
            risk_score -= 0.02
        score += risk_score
        details["风险控制"] = round(risk_score, 3)

        # 8. 市场状态修正（±0.10）— 熊市降信心，牛市提信心
        regime_adjustment = self._calc_regime_adjustment(score, market_regime)
        score += regime_adjustment
        details["市场状态"] = regime_adjustment

        # ── 周线多周期过滤 (±0.10) — 参考 综合评分.docx ──
        if df is not None and len(df) >= 60:
            try:
                df_week = df.copy()
                df_week["date"] = pd.to_datetime(df_week["date"])
                weekly = df_week.resample("W-WED", on="date").agg({
                    "open": "first", "high": "max", "low": "min",
                    "close": "last", "volume": "sum",
                }).dropna()
                if len(weekly) >= 20:
                    w_close = weekly["close"]
                    w_ma5 = w_close.rolling(5).mean().iloc[-1]
                    w_ma20 = w_close.rolling(20).mean().iloc[-1]
                    if w_ma5 > w_ma20:
                        score = min(score + 0.10, 1.0)
                        details["周线过滤"] = 0.10
                    else:
                        score = max(score * 0.6, 0.05)
                        details["周线过滤"] = -0.10
            except Exception:
                details["周线过滤"] = 0.0
        else:
            details["周线过滤"] = 0.0

        return min(score, 1.0), details

    def _entry_condition_text(self, zone: EntryZone, current_price: float) -> str:
        """生成可读的买入提示（区间只作风险参考，一律按现价买）。"""
        if zone.status == "inside":
            return f"现价 {current_price:.2f} 在支撑区（{zone.low:.2f}~{zone.high:.2f}）内，可按现价买入"
        if zone.status == "outside":
            gap = (current_price / zone.high - 1) * 100 if zone.high > 0 else 0
            return (f"现价 {current_price:.2f} 高于支撑区（{zone.low:.2f}~{zone.high:.2f}）"
                    f"约 {gap:.1f}%，可按现价买入（追高）")
        return f"{zone.reason}，本期只观察"

    def _build_rationale(
        self,
        strategies: list[str],
        vol_ratio: float,
        atr_val: float,
        current_price: float,
        entry_low: float,
        entry_high: float,
        stop_loss: float,
        target_1: float,
    ) -> str:
        """生成选中理由。"""
        strategy_text = "+".join(strategies)
        risk_pct = round((current_price - stop_loss) / current_price * 100, 1)
        reward_pct = round((target_1 - current_price) / current_price * 100, 1)
        parts = [
            f"{strategy_text} 策略触发",
            f"当前价 {current_price:.2f}",
            f"买入区间 {entry_low:.2f}~{entry_high:.2f}",
            f"止损 {stop_loss:.2f}(-{risk_pct}%)",
            f"目标 {target_1:.2f}(+{reward_pct}%)",
        ]
        return " | ".join(parts)

    @staticmethod
    def _calc_regime_adjustment(base_score: float, market_regime: str) -> float:
        """市场状态修正：熊市降信心，牛市提信心。

        Args:
            base_score: 已计算的基准置信度 (0~1)。
            market_regime: 市场状态标签（强牛/弱牛/震荡/弱熊/强熊）。

        Returns:
            修正值 (-0.10 ~ +0.10)。
        """
        if "强牛" in market_regime:
            return +0.08  # 强牛市场，趋势策略成功率高
        elif "弱牛" in market_regime:
            return +0.04  # 弱牛市场，适当加分
        elif "震荡" in market_regime:
            return 0.00   # 震荡市，不加不减
        elif "弱熊" in market_regime:
            return -0.05  # 弱熊市场，降信心
        elif "强熊" in market_regime:
            return -0.10  # 强熊市场，大幅降信心
        return 0.0

    def batch_generate(
        self,
        symbols: list[str],
        strategy_map: dict[str, list[str]],  # {symbol: [strategy_names]}
        market_regime: str = "",
    ) -> list[TradeSignal]:
        """批量生成信号。

        Args:
            symbols: 股票代码列表。
            strategy_map: 每只股票对应的触发策略名称。
            market_regime: 当前市场状态。

        Returns:
            按置信度降序排列的 TradeSignal 列表。
        """
        signals = []
        for symbol in symbols:
            strats = strategy_map.get(symbol, ["通用"])
            try:
                signal = self.generate(symbol, strats, market_regime)
                if signal is not None:
                    signals.append(signal)
            except Exception as exc:
                logger.log(5, f"[{symbol}] 信号生成失败: {exc}")
                continue
        signals.sort(key=lambda x: x.confidence, reverse=True)
        logger.info(f"信号生成完成: {len(signals)}/{len(symbols)} 只生成成功")
        return signals
