"""买入区间、目标、止损、盈亏比的唯一定义。

generator / rating.engine / cloud 与评估脚本共用本模块，避免同一字段
在不同端出现不同数值（见 REVIEW-2026-11-03 的 S1/S2）。
本模块只做纯计算，不读数据库、不做 IO。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# 锚（支撑）距现价的最远距离，超过则视为「下方支撑过远」，不给区间
ATR_DISTANCE_CAP = 3.0
# 区间半宽：L = A - k*ATR, U = A + k*ATR
ZONE_HALF_WIDTH_ATR = 0.5
# 止损在区间下轨下方留出的缓冲
STOP_BUFFER_ATR = 0.5
# 目标至少高出区间上轨一个 ATR
TARGET_MIN_ATR = 1.0
# 上方没有合格阻力时的兜底目标
TARGET_FALLBACK_ATR = 2.0
# 支撑必须低于现价 0.5%，避免把「贴着现价」的均线当成支撑
MIN_SUPPORT_GAP_PCT = 0.005
# 风险距离下限（占入场价），低于此视为止损过近
MIN_RISK_PCT = 0.01
# 盈亏比展示上限
RR_CAP = 10.0
# 原 ATR 止损的参数（与区间下轨取更深者）
STOP_ATR_MULT = 2.0
STOP_MA20_MULT = 0.97
STOP_LOW10_MULT = 0.98
STOP_MAX_PCT = 0.95


@dataclass(frozen=True)
class EntryZone:
    status: str          # inside / outside / below / none
    low: float = 0.0
    high: float = 0.0
    anchor: float = 0.0
    anchor_name: str = ""
    reason: str = ""


def support_candidates_from_bars(close: pd.Series, low: pd.Series) -> dict[str, float]:
    """从K线取候选支撑（与评估脚本、技术分析模块同一套定义）。"""
    price = float(close.iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    std20 = float(close.rolling(20).std().iloc[-1])
    out = {
        "布林下轨": ma20 - 2 * std20,
        "MA20": ma20,
        "近10日低点": float(low.tail(10).min()),
        "近20日低点": float(low.tail(20).min()),
    }
    if len(close) >= 60:
        out["MA60"] = float(close.rolling(60).mean().iloc[-1])
    return {k: v for k, v in out.items() if v > 0 and v < price}


def resistance_candidates_from_bars(high: pd.Series) -> list[float]:
    """从K线取候选阻力（近10/20日高点）。"""
    return [float(high.tail(10).max()), float(high.tail(20).max())]


@dataclass(frozen=True)
class RiskReward:
    value: float | None = None
    capped: bool = False
    reason: str = ""

    def text(self) -> str:
        if self.value is None:
            return "—"
        if self.capped:
            return f"≥{self.value:g}"
        return f"{self.value:.2f}"


@dataclass(frozen=True)
class Target:
    value: float
    is_fallback: bool = False   # True 表示没有合格阻力，按 ATR 兜底


def build_entry_zone(
    price: float,
    atr: float,
    supports: dict[str, float],
    k: float = ZONE_HALF_WIDTH_ATR,
    distance_cap: float = ATR_DISTANCE_CAP,
) -> EntryZone:
    """以现价下方最近支撑为锚，构造 A ± k*ATR 的买入区间。

    Args:
        price: 当前收盘价。
        atr: 当日 ATR。
        supports: {名称: 价位}，低于现价的候选支撑。
        k: 半宽（ATR 倍数）。
        distance_cap: 锚距现价的最大 ATR 倍数。

    Returns:
        EntryZone。status 为 none 时 low/high 无意义，reason 说明原因。
    """
    if price <= 0 or atr <= 0:
        return EntryZone(status="none", reason="数据不足")

    min_support = price * (1 - MIN_SUPPORT_GAP_PCT)
    below = {n: v for n, v in supports.items() if 0 < v < min_support}
    if not below:
        has_support = any(v > 0 for v in supports.values())
        return EntryZone(
            status="none",
            reason="已跌破全部支撑" if has_support else "无有效支撑",
        )

    near = {n: v for n, v in below.items() if price - v <= distance_cap * atr}
    if not near:
        return EntryZone(status="none", reason="下方支撑过远")

    name, anchor = max(near.items(), key=lambda kv: kv[1])
    low = max(anchor - k * atr, 0.01)
    high = anchor + k * atr

    if price > high:
        status = "outside"
    elif price < low:
        status = "below"
    else:
        status = "inside"

    return EntryZone(
        status=status,
        low=round(low, 2),
        high=round(high, 2),
        anchor=round(anchor, 2),
        anchor_name=name,
    )


def entry_reference(zone: EntryZone, price: float) -> float | None:
    """给出实际入场价。

    区间只作风险参考，可买状态（区间内/高于区间）都按现价成交；
    已跌破或不设区间时不可买，返回 None。
    """
    if zone.status in ("inside", "outside"):
        return price
    return None


def pick_target(resistances: list[float], ref: float, atr: float) -> Target:
    """取 ref 上方第一个至少高出 1×ATR 的阻力；没有则用 ref + 2×ATR 兜底。"""
    floor = ref + TARGET_MIN_ATR * atr
    for r in sorted(v for v in resistances if v > ref):
        if r >= floor:
            return Target(round(r, 2), is_fallback=False)
    return Target(round(ref + TARGET_FALLBACK_ATR * atr, 2), is_fallback=True)


def original_stop_price(price: float, atr: float, ma20: float, low10: float) -> float:
    """原 ATR 止损：max(价-2ATR, MA20×0.97, 近10日低点×0.98)，再钳到价×0.95。"""
    stop = max(
        price - STOP_ATR_MULT * atr,
        ma20 * STOP_MA20_MULT,
        low10 * STOP_LOW10_MULT,
    )
    return round(min(stop, price * STOP_MAX_PCT), 2)


def stop_price(low: float, atr: float, original_stop: float | None = None) -> float:
    """止损取「区间下轨 - 0.5×ATR」与原有止损中更深的那个。"""
    stop = low - STOP_BUFFER_ATR * atr
    if original_stop and original_stop > 0:
        stop = min(stop, original_stop)
    return round(max(stop, 0.01), 2)


def plan_risk_ok(upper: float, stop: float, min_pct: float = MIN_RISK_PCT) -> bool:
    """计划风险距离是否足够（用于「波动过小 → 只观察」的准入判断）。"""
    return upper > 0 and (upper - stop) >= upper * min_pct


def compute_risk_reward(entry: float | None, stop: float, target: float) -> RiskReward:
    """盈亏比 = (目标 - 入场) / (入场 - 止损)，不可算时 value=None。"""
    if not entry or entry <= 0 or stop <= 0 or target <= 0:
        return RiskReward(reason="数据不足")
    risk = entry - stop
    if risk <= entry * MIN_RISK_PCT:
        return RiskReward(reason="止损过近")
    reward = target - entry
    if reward <= 0:
        return RiskReward(reason="目标低于入场")
    rr = reward / risk
    if rr > RR_CAP:
        return RiskReward(RR_CAP, capped=True)
    return RiskReward(round(rr, 2))
