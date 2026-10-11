"""efinance 全市场财务数据批量获取（带磁盘缓存）。

基于东方财富数据源：get_all_company_performance 一次拉取全市场业绩数据
（约 8s / 5500+ 只），字段比 baostock 逐股查询更全（含毛利率/营收增速）。

定位：fundamental 策略的财务数据源之一。调用方需在 efinance 不可用时
回退到 baostock。非官方接口，接口字段可能变更，已做容错。
"""

import json
import os
from datetime import date

from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)

# data/financial_efinance.json
_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "financial_efinance.json",
)

# 最新报告期尚无公司披露时（如 10 月初取 09-30 三季报），向前回退的报告期个数上限
_MAX_REPORT_DATE_LOOKBACK = 4
# 报告期刚开始披露时接口只返回少数几家公司（如 10 月初的三季报），
# 低于这个数量视为「这一期还没数据」并继续回退，避免用残缺数据覆盖上一次的完整缓存
_MIN_SYMBOLS_PER_REPORT = 1000

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


def _fetch_performance():
    """抓取全市场业绩表，返回 (DataFrame, 报告期)。

    efinance 默认取“最新报告期”，但该报告期刚开始时还没有公司披露，接口返回
    “返回数据为空”（result 为 None，efinance 内部会抛 TypeError）。此时逐个
    回退到更早的报告期取数。
    """
    import efinance as ef

    try:
        candidates = ef.stock.get_all_report_dates()["报告日期"].to_list()
        candidates = candidates[:_MAX_REPORT_DATE_LOOKBACK]
    except Exception as exc:
        logger.warning(f"获取报告期列表失败，改用 efinance 默认报告期: {exc}")
        candidates = [None]

    for report_date in candidates:
        try:
            df = ef.stock.get_all_company_performance(date=report_date)
        except Exception as exc:
            logger.warning(f"efinance 报告期 {report_date} 拉取失败: {exc}")
            continue
        if df is None or df.empty:
            continue
        if len(df) < _MIN_SYMBOLS_PER_REPORT:
            logger.info(f"报告期 {report_date} 仅 {len(df)} 只已披露，改用上一期")
            continue
        if report_date != candidates[0]:
            logger.info(
                f"最新报告期 {candidates[0]} 尚无披露，efinance 财务数据改用 "
                f"{report_date}（{len(df)} 只）"
            )
        return df, report_date
    return None, None


def load_financial_all(force: bool = False) -> dict[str, dict]:
    """返回 {symbol(6位): {roe, eps, profit_growth, net_profit_margin, cfo_to_np}}。

    优先用当天磁盘缓存；否则调 efinance 全市场拉取并落盘。
    拉取失败时回退本地缓存（财报按季更新，略旧也远优于改用 baostock 逐股查询）；
    连缓存都没有才返回空 dict，由调用方回退 baostock。
    """
    global _AVAILABLE
    if _AVAILABLE is False:
        return {}

    today = date.today().isoformat()
    cached = _read_cache()
    if not force and cached and cached.get("fetch_date") == today and cached.get("data"):
        return cached["data"]

    try:
        import efinance  # noqa: F401
    except Exception as exc:
        logger.warning(f"efinance 未安装，财务数据改用 baostock 逐股查询: {exc}")
        _AVAILABLE = False
        return {}

    df, report_date = _fetch_performance()
    if df is None:
        # 只在 efinance 不可用时永久禁用；单次网络/接口失败下次调用仍会重试
        if cached and cached.get("data"):
            logger.warning(
                f"efinance 全市场业绩拉取失败，回退本地缓存"
                f"（抓取于 {cached.get('fetch_date')}，报告期 {cached.get('report_date')}，"
                f"{len(cached['data'])} 只）"
            )
            return cached["data"]
        logger.warning("efinance 全市场业绩拉取失败且无本地缓存，财务数据改用 baostock 逐股查询")
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
    _write_cache({"fetch_date": today, "report_date": report_date, "data": data})
    return data
