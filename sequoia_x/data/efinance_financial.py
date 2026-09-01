"""efinance 全市场财务数据批量获取（带磁盘缓存）。

基于东方财富数据源：get_all_company_performance 一次拉取全市场业绩数据
（约 8s / 5500+ 只），字段比 baostock 逐股查询更全（含毛利率/营收增速）。

定位：fundamental 策略的财务数据源之一。调用方需在 efinance 不可用时
回退到 baostock。非官方接口，接口字段可能变更，已做容错。
"""

import json
import os
from datetime import date

# data/financial_efinance.json
_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "financial_efinance.json",
)

# efinance 业绩表中文列 → fundamental 字段
CODE_COL = "股票代码"
ROE_COL = "净资产收益率"
EPS_COL = "每股收益"
GROWTH_COL = "净利润同比增长"
NP_COL = "净利润"
REV_COL = "营业收入"
CFOPS_COL = "每股经营现金流量"

# 模块级标记：本次进程内 efinance 不可用时置 False，避免重复尝试拉取
_AVAILABLE: bool | None = None


def _read_cache() -> dict | None:
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_cache(payload: dict) -> None:
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        pass


def _num(v) -> float | None:
    """安全转 float，NaN/None 返回 None。"""
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def load_financial_all(force: bool = False) -> dict[str, dict]:
    """返回 {symbol(6位): {roe, eps, profit_growth, net_profit_margin, cfo_to_np}}。

    优先用当天磁盘缓存；否则调 efinance 全市场拉取并落盘。
    efinance 不可用/拉取失败时返回空 dict（由调用方回退 baostock）。
    """
    global _AVAILABLE
    if _AVAILABLE is False:
        return {}

    today = date.today().isoformat()
    if not force:
        cached = _read_cache()
        if cached and cached.get("fetch_date") == today and cached.get("data"):
            return cached["data"]

    try:
        import efinance as ef
    except Exception:
        _AVAILABLE = False
        return {}

    try:
        df = ef.stock.get_all_company_performance()
    except Exception:
        _AVAILABLE = False
        return {}
    if df is None or df.empty:
        _AVAILABLE = False
        return {}

    data: dict[str, dict] = {}
    for _, row in df.iterrows():
        sym = str(row.get(CODE_COL, "")).strip()
        if not sym or len(sym) < 6:
            continue
        sym = sym.zfill(6)
        fin: dict[str, float] = {}
        roe = _num(row.get(ROE_COL))
        if roe is not None:
            fin["roe"] = round(roe, 2)
        eps = _num(row.get(EPS_COL))
        if eps is not None:
            fin["eps"] = round(eps, 3)
        growth = _num(row.get(GROWTH_COL))
        if growth is not None:
            fin["profit_growth"] = round(growth, 2)
        np_ = _num(row.get(NP_COL))
        rev = _num(row.get(REV_COL))
        if np_ is not None and rev and rev > 0:
            fin["net_profit_margin"] = round(np_ / rev * 100, 2)
        cfops = _num(row.get(CFOPS_COL))
        if cfops is not None and eps and eps > 0:
            fin["cfo_to_np"] = round(cfops / eps, 2)
        if fin:
            data[sym] = fin

    _AVAILABLE = True
    _write_cache({"fetch_date": today, "data": data})
    return data
