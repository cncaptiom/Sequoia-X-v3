"""新浪财经批量快照：一次请求拿多个代码的最新交易日行情。

日常增量同步的主数据源。相比逐股日K接口（如腾讯 qfqday，5500 只股票要发 5500 个
请求），新浪的 ``hq.sinajs.cn/list=`` 一次可带 300 个代码，全市场只需约 20 个请求，
既快又不容易触发风控。

返回字段实测（2026-10-09），成交量单位是**股**、成交额单位是**元**，与 stock_daily
表口径一致；价格与库内前复权序列在最新交易日逐字相同（前复权只回溯调整历史，
最新一天的复权价就等于原始成交价）。

局限：只提供**最新一个交易日**。若某只股票的历史缺口超过一天，需要调用方另行走
逐股接口补齐。
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)

_URL = "https://hq.sinajs.cn/list="
# 必须带 Referer，否则 403
_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.sina.com.cn",
}
_TIMEOUT = 20
# 实测单次 300 个真实代码 0 丢失、约 0.9s；留些余量取 250
_BATCH_SIZE = 250
_MAX_WORKERS = 4
_LOG_INTERVAL = 3

# 快照字段下标（逗号分隔）
_IDX_OPEN = 1
_IDX_CLOSE = 3
_IDX_HIGH = 4
_IDX_LOW = 5
_IDX_VOLUME = 8
_IDX_TURNOVER = 9
_IDX_DATE = 30
_MIN_FIELDS = 32

_local = threading.local()


def to_sina_code(symbol: str) -> str | None:
    """6 位代码 → 新浪代码。北交所（4/8 开头）新浪无行情，返回 None。"""
    code = str(symbol).strip().zfill(6)
    if code[0] in ("4", "8"):
        return None
    return f"sh{code}" if code[0] in ("6", "9") else f"sz{code}"


def _session() -> requests.Session:
    """每线程一个 Session，复用连接（Session 不保证跨线程安全）。"""
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def _parse_line(line: str) -> list | None:
    """解析 ``var hq_str_sh600000="..."`` 一行，返回 stock_daily 格式的一行。

    停牌/无成交（成交量为 0）返回 None。
    """
    if '="' not in line:
        return None
    head, _, rest = line.partition('="')
    code = head.rsplit("_", 1)[-1].strip()
    if len(code) < 8:
        return None
    fields = rest.rsplit('"', 1)[0].split(",")
    if len(fields) < _MIN_FIELDS:
        return None

    date_str = fields[_IDX_DATE].strip()
    if len(date_str) != 10 or date_str[4] != "-":
        return None
    try:
        open_ = float(fields[_IDX_OPEN])
        close = float(fields[_IDX_CLOSE])
        high = float(fields[_IDX_HIGH])
        low = float(fields[_IDX_LOW])
        volume = float(fields[_IDX_VOLUME])
        turnover = float(fields[_IDX_TURNOVER])
    except (ValueError, IndexError):
        return None
    if volume <= 0 or close <= 0 or open_ <= 0 or high <= 0 or low <= 0:
        return None
    return [code[2:], date_str, open_, high, low, close, volume, turnover]


def _fetch_batch(codes: list[str]) -> list[list]:
    try:
        resp = _session().get(_URL + ",".join(codes), headers=_HEADERS, timeout=_TIMEOUT)
    except Exception as exc:
        logger.warning(f"新浪快照批次失败: {type(exc).__name__}: {exc}")
        return []
    if resp.status_code != 200:
        logger.warning(f"新浪快照批次返回 {resp.status_code}")
        return []
    return [row for row in (_parse_line(line) for line in resp.text.splitlines()) if row]


def fetch_snapshot(symbols: list[str]) -> tuple[list[list], list[str]]:
    """批量抓取最新交易日快照。

    Args:
        symbols: 6 位股票代码列表。

    Returns:
        (rows, failed)。rows 每行为
        [symbol, date, open, high, low, close, volume(股), turnover(元)]；
        failed 是没取到快照的代码（含新浪不支持的代码段）。
    """
    code_map = {}
    failed: list[str] = []
    for symbol in symbols:
        code = to_sina_code(symbol)
        if code is None:
            failed.append(symbol)
        else:
            code_map[code] = symbol

    codes = list(code_map)
    if not codes:
        return [], failed

    batches = [codes[i : i + _BATCH_SIZE] for i in range(0, len(codes), _BATCH_SIZE)]
    rows: list[list] = []
    seen: set[str] = set()
    started = time.time()

    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(batches))) as pool:
        futures = {pool.submit(_fetch_batch, b): b for b in batches}
        for done, future in enumerate(as_completed(futures), 1):
            for row in future.result():
                rows.append(row)
                seen.add(row[0])
            if done % _LOG_INTERVAL == 0:
                logger.info(f"新浪快照进度: {done}/{len(batches)} 批，已取 {len(rows)} 条")

    failed.extend(s for s in (code_map[c] for c in codes) if s not in seen)
    logger.info(
        f"新浪快照完成: {len(rows)} 条 / {len(codes)} 只，未取到 {len(failed)} 只，"
        f"{len(batches)} 个批次 / {time.time() - started:.1f}s"
    )
    return rows, failed
