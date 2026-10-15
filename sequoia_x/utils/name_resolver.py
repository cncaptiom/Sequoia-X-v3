"""股票名称解析器：带缓存的股票名查询（mootdx 主用，baostock 备选）。"""

class NameResolver:
    """股票名称解析器，批量查询并缓存结果。"""

    def __init__(self):
        self._names: dict[str, str] = {}
        self._loaded = False

    # mootdx 走 TCP，数据源不可达时不是报错而是**挂住**（它的 K 线接口已经死过一次），
    # 所以必须放进超时里调用。
    _MOOTDX_TIMEOUT = 8.0

    def _load_all(self) -> None:
        if self._loaded:
            return

        # 优先使用 mootdx（TCP 协议，不封 IP）
        names = self._try_mootdx()
        if names:
            self._names = names
            self._loaded = True
            return

        # 降级到 baostock（带超时保护，防止卡死）
        try:
            import baostock as bs
            import threading
            result = [None]
            def _bs_login():
                lg = bs.login()
                if lg.error_code == "0":
                    rs = bs.query_stock_basic(code_name="", code="")
                    names = {}
                    while rs.next():
                        row = rs.get_row_data()
                        raw_code = row[0]
                        name = row[1]
                        status = row[4]
                        stock_type = row[5]
                        if status == "1" and stock_type == "1":
                            pure_code = raw_code.split(".")[1]
                            names[pure_code] = name
                    result[0] = names
                    bs.logout()
            t = threading.Thread(target=_bs_login, daemon=True)
            t.start()
            t.join(timeout=5)
            if t.is_alive():
                return
            if result[0]:
                self._names = result[0]
                self._loaded = True
        except Exception:
            pass

    def _try_mootdx(self) -> dict[str, str] | None:
        """mootdx 取全市场股票名；超时或失败返回 None。

        此前这一支没有超时——数据源不可达时会**挂住整个进程**，而它每只股票都会被
        调到（`generator.py` 的信号生成链路）。后试的 baostock 反而有超时，顺序是反的。
        """
        import threading

        box: dict = {}

        def _run():
            try:
                from mootdx.quotes import Quotes
                client = Quotes.factory(market='std')
                picked: dict[str, str] = {}
                for market in [1, 0]:
                    stocks = client.stocks(market=market)
                    if stocks is not None:
                        for _, row in stocks.iterrows():
                            code = str(row['code']).zfill(6)
                            name = str(row.get('name', ''))
                            if code and name and not any(
                                    name.startswith(p)
                                    for p in ('上证', '深证', '创业', '中小', '基金', 'ETF', '国债')):
                                picked[code] = name
                box["names"] = picked
            except Exception:
                pass

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=self._MOOTDX_TIMEOUT)
        if t.is_alive():
            return None
        return box.get("names") or None

    def get_name(self, symbol: str) -> str:
        self._load_all()
        return self._names.get(symbol, symbol)

    def get_names(self, symbols: list[str]) -> dict[str, str]:
        self._load_all()
        return {s: self._names.get(s, s) for s in symbols}

    def filter_st(self, symbols: list[str]) -> list[str]:
        """过滤名称含 ST / *ST（退市风险警示）的股票。

        ST 股有 ±5% 涨跌幅限制和退市风险，不应混入常规选股池。
        """
        self._load_all()
        return [s for s in symbols if "ST" not in self._names.get(s, s).upper()]

    def get_industries(self, symbols: list[str]) -> dict[str, str]:
        result: dict[str, str] = {}
        try:
            import baostock as bs
            lg = bs.login()
            if lg.error_code == "0":
                for symbol in symbols:
                    prefix = "sh" if symbol.startswith(("6", "9")) else "sz"
                    bs_code = f"{prefix}.{symbol}"
                    rs = bs.query_stock_industry(bs_code)
                    while rs.next():
                        row = rs.get_row_data()
                        industry = row[2] if row[2] else row[1] if row[1] else "未知"
                        result[symbol] = industry
                bs.logout()
        except Exception:
            pass
        for sym in symbols:
            if sym not in result:
                result[sym] = "未知"
        return result


_resolver: NameResolver | None = None


def get_name_resolver() -> NameResolver:
    global _resolver
    if _resolver is None:
        _resolver = NameResolver()
    return _resolver
