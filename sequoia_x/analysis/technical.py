"""技术分析模块：计算支撑位、压力位、买卖点及投资策略。"""

import pandas as pd


def analyze_stock(df: pd.DataFrame) -> dict:
    """对单只股票进行完整技术分析。

    Args:
        df: OHLCV DataFrame，需包含 open, high, low, close, volume 列。

    Returns:
        包含当前价格、涨跌幅、均线、支撑位、压力位、趋势判断和策略建议的字典。
    """
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else None
    price = last["close"]

    # ── 均线系统 ──
    ma5 = df["close"].rolling(5).mean().iloc[-1]
    ma10 = df["close"].rolling(10).mean().iloc[-1]
    ma20 = df["close"].rolling(20).mean().iloc[-1]
    ma60 = df["close"].rolling(60).mean().iloc[-1] if len(df) >= 60 else None
    ma120 = df["close"].rolling(120).mean().iloc[-1] if len(df) >= 120 else None
    ma250 = df["close"].rolling(250).mean().iloc[-1] if len(df) >= 250 else None

    # ── 布林带 (20, 2) ──
    std20 = df["close"].rolling(20).std().iloc[-1]
    bb_upper = ma20 + 2 * std20
    bb_lower = ma20 - 2 * std20

    # ── 涨跌幅 ──
    pct_chg = ((last["close"] - prev["close"]) / prev["close"] * 100) if prev is not None else 0

    # ── 成交量分析 ──
    vol_ma20 = df["volume"].tail(20).mean()
    vol_ratio = last["volume"] / vol_ma20 if vol_ma20 > 0 else 1

    # ── 趋势判断 ──
    trend = _judge_trend(ma5, ma10, ma20, ma60)

    # ── 构建支撑/压力位（只显示有意义的值） ──
    recent_low10 = df["low"].tail(10).min()
    recent_low20 = df["low"].tail(20).min()
    recent_high10 = df["high"].tail(10).max()
    recent_high20 = df["high"].tail(20).max()

    # 支撑位：只保留低于当前价的（才有支撑意义）
    supports: dict[str, float] = {}
    for name, val in [("布林下轨", bb_lower), ("MA20", ma20),
                      ("MA60", ma60), ("近10日低点", recent_low10),
                      ("近20日低点", recent_low20)]:
        if val is not None and val < price * 0.98:
            supports[name] = round(val, 2)
    if not supports:
        supports["近期低点"] = round(min(recent_low10, recent_low20), 2)

    # 压力位：只保留高于当前价的（才有压力意义）
    resistances: dict[str, float] = {}
    for name, val in [("近10日高点", recent_high10), ("近20日高点", recent_high20),
                      ("布林上轨", bb_upper), ("MA120", ma120), ("MA250", ma250)]:
        if val is not None and val > price * 1.02:
            resistances[name] = round(val, 2)
    if not resistances:
        resistances["上方压力"] = round(max(recent_high10, recent_high20), 2)

    # ── 买卖策略 ──
    strategy = _generate_strategy(
        price=price,
        ma20=ma20,
        supports=supports,
        resistances=resistances,
        vol_ratio=vol_ratio,
        trend=trend,
    )

    return {
        "current_price": round(price, 2),
        "pct_chg": round(pct_chg, 2),
        "ma5": round(ma5, 2),
        "ma10": round(ma10, 2),
        "ma20": round(ma20, 2),
        "ma60": round(ma60, 2) if ma60 else None,
        "supports": supports,
        "resistances": resistances,
        "vol_ratio": round(vol_ratio, 2),
        "trend": trend,
        "strategy": strategy,
    }


def _judge_trend(ma5: float, ma10: float, ma20: float, ma60: float | None) -> str:
    """根据均线排列判断趋势。"""
    if ma5 > ma10 > ma20 and (ma60 is None or ma20 > ma60):
        return "上升趋势"
    elif ma5 < ma10 < ma20 and (ma60 is None or ma20 < ma60):
        return "下降趋势"
    else:
        return "震荡整理"


def _generate_strategy(
    price: float,
    ma20: float,
    supports: dict,
    resistances: dict,
    vol_ratio: float,
    trend: str,
) -> dict:
    """根据技术指标生成买卖策略建议。

    原则：
      - 买入区间：在支撑位附近，不高于当前价
      - 目标价位：在压力位附近，不低于当前价
      - 止损位：在最强支撑下方
    """
    # 只取低于当前价的支撑位，从高到低排列（最靠近现价的优先）
    support_vals = sorted(
        set(v for v in supports.values() if v < price), reverse=True
    )
    # 只取高于当前价的压力位，从低到高排列（最容易触碰的优先）
    resistance_vals = sorted(
        set(v for v in resistances.values() if v > price)
    )

    # ── 买入区间 ──
    if support_vals:
        buy_low = support_vals[0]  # 最近支撑位
    else:
        buy_low = round(price * 0.95, 2)

    # 买入上限不高于当前价，也不高于MA20（如果MA20低于现价）
    buy_high_candidates = [price]
    if ma20 < price:
        buy_high_candidates.append(ma20)
    buy_high = max(buy_low, min(buy_high_candidates))

    # ── 目标价位 ──
    targets = resistance_vals[:2]
    if not targets:
        targets = [round(price * 1.05, 2), round(price * 1.10, 2)]

    # ── 止损位 ──
    if support_vals:
        stop_loss = round(support_vals[0] * 0.97, 2)
    elif ma20 < price:
        stop_loss = round(ma20 * 0.97, 2)
    else:
        stop_loss = round(price * 0.95, 2)

    # ── 投资建议 ──
    above_ma20_pct = float((price - ma20) / ma20 * 100)
    if "上升" in trend and above_ma20_pct < 3:
        suggestion = "趋势向上，价格在均线附近，可逢低布局"
        position = "可适当加仓（5-7成）"
    elif "上升" in trend and above_ma20_pct < 8:
        suggestion = f"趋势向上，当前价高于MA20约{above_ma20_pct:.1f}%，可等回调至MA20附近介入"
        position = "轻仓参与（3-5成）"
    elif "上升" in trend:
        suggestion = f"趋势向上但偏离MA20较远({above_ma20_pct:.1f}%)，等待回调后再参与"
        position = "轻仓参与（3成）"
    elif "下降" in trend:
        suggestion = "处于下降趋势，建议观望"
        position = "空仓或轻仓（1成以下）"
    else:
        suggestion = "震荡行情，控制仓位，高抛低吸"
        position = "中等仓位（3成）"

    return {
        "buy_zone": (round(float(buy_low), 2), round(float(buy_high), 2)),
        "targets": [round(float(t), 2) for t in targets],
        "stop_loss": round(float(stop_loss), 2),
        "suggestion": suggestion.strip(),
        "position": position,
    }
