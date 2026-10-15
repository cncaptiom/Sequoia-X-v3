"""多因子模块：价值/质量/规模/动量/低波 因子计算与横截面排名。

基于 Fama-French 五因子模型 (2015) + AQR 质量因子 (Asness, Frazzini & Pedersen, 2019):
  - 价值: P/E, P/B, P/S
  - 质量: ROE, 毛利率, 资产负债率
  - 规模: 总市值
  - 动量: 12-1月动量（扣除最近1月以避免短期反转, Jegadeesh & Titman 1993）
  - 低波动: 60日波动率

用法:
    from sequoia_x.factors import FactorCalculator, CrossSectionalRanker

    calc = FactorCalculator(data_engine)
    factors = calc.compute_all(symbols)                # 计算原始因子值
    scores = CrossSectionalRanker.rank_and_combine(factors)  # 横截面排名 + 加权合成
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)


# ── 因子配置 ──

@dataclass
class FactorConfig:
    """因子权重配置。权重归一化后应用。"""
    weight_value: float = 0.25        # 价值因子
    weight_quality: float = 0.20      # 质量因子
    weight_momentum: float = 0.25     # 动量因子
    weight_low_vol: float = 0.15      # 低波动因子
    weight_size: float = 0.15         # 规模因子

    def normalized(self) -> dict[str, float]:
        total = self.weight_value + self.weight_quality + self.weight_momentum + self.weight_low_vol + self.weight_size
        if total == 0:
            return {}
        return {
            "value": self.weight_value / total,
            "quality": self.weight_quality / total,
            "momentum": self.weight_momentum / total,
            "low_vol": self.weight_low_vol / total,
            "size": self.weight_size / total,
        }


# ── 因子结果 ──

@dataclass
class FactorResult:
    """单只股票的因子计算结果。"""
    symbol: str

    # 价值因子
    pe: Optional[float] = None           # 市盈率 (越低越好)
    pb: Optional[float] = None           # 市净率 (越低越好)
    ps: Optional[float] = None           # 市销率 (越低越好)

    # 质量因子
    roe: Optional[float] = None          # ROE (越高越好)
    gross_margin: Optional[float] = None # 毛利率 (越高越好)
    debt_ratio: Optional[float] = None   # 资产负债率 (越低越好)

    # 规模因子
    market_cap: Optional[float] = None   # 总市值 (越小越好——小盘股溢价)

    # 动量因子
    momentum_12m1: Optional[float] = None  # 12-1月价格动量

    # 低波动因子
    vol_60d: Optional[float] = None        # 60日波动率 (越低越好)
    beta: Optional[float] = None           # Beta (越低越好)

    # 合成得分
    composite_score: float = 0.0
    percentile_rank: float = 0.0
    factor_percentiles: dict[str, float] = field(default_factory=dict)


# ── 因子计算器 ──

class FactorCalculator:
    """计算 A 股多因子值。

    数据来源:
      - 价格/成交量: baostock (已有 DataEngine)
      - 财务数据 / 市值: baostock（此前文档写的 akshare 是旧实现的残留，
        主包已无 akshare 引用）
    """

    def __init__(self, data_engine):
        self.engine = data_engine
        self._fundamental_cache: dict[str, dict] = {}

    def compute_all(self, symbols: list[str], progress: bool = True) -> list[FactorResult]:
        """批量计算所有股票的因子值。

        Args:
            symbols: 股票代码列表。
            progress: 是否打印进度。

        Returns:
            FactorResult 列表。
        """
        results: list[FactorResult] = []
        total = len(symbols)

        for i, symbol in enumerate(symbols):
            try:
                result = self._compute_single(symbol)
                if result is not None:
                    results.append(result)
            except Exception as exc:
                logger.log(5, f"[{symbol}] 因子计算失败: {exc}")
                continue

            if progress and (i + 1) % 500 == 0:
                logger.info(f"因子计算进度: {i + 1}/{total}")

        logger.info(f"因子计算完成: {len(results)}/{total} 只股票")
        return results

    def _compute_single(self, symbol: str) -> Optional[FactorResult]:
        """计算单只股票的因子值。"""
        df = self.engine.get_ohlcv(symbol)
        if df.empty or len(df) < 60:
            return None

        closes = df["close"].values
        last_close = float(closes[-1])

        result = FactorResult(symbol=symbol)

        # ── 价值因子（从价格数据可推的部分） ──
        # 注意：P/E, P/B, P/S 需要财务数据，此处用 akhare 获取
        try:
            fundamental = self._get_fundamental(symbol)
            if fundamental:
                result.pe = fundamental.get("pe")
                result.pb = fundamental.get("pb")
                result.ps = fundamental.get("ps")
                result.roe = fundamental.get("roe")
                result.gross_margin = fundamental.get("gross_margin")
                result.debt_ratio = fundamental.get("debt_ratio")
                result.market_cap = fundamental.get("market_cap")
        except Exception:
            pass

        # ── 动量因子: 12-1月动量（扣除最近1个月） ──
        # Jegadeesh & Titman (1993) 经典方法
        if len(df) >= 252:
            # T-12 到 T-1 个月的收益
            price_12m_ago = float(closes[-min(252, len(closes))])
            price_1m_ago = float(closes[-min(21, len(closes))])
            if price_12m_ago > 0:
                result.momentum_12m1 = (price_1m_ago - price_12m_ago) / price_12m_ago
            else:
                result.momentum_12m1 = 0.0
        elif len(df) >= 60:
            price_3m_ago = float(closes[-min(60, len(closes))])
            price_1m_ago = float(closes[-min(21, len(closes))])
            if price_3m_ago > 0:
                result.momentum_12m1 = (price_1m_ago - price_3m_ago) / price_3m_ago
            else:
                result.momentum_12m1 = 0.0

        # ── 低波动因子: 60日波动率 ──
        if len(df) >= 60:
            returns_60d = pd.Series(closes[-60:]).pct_change().dropna()
            result.vol_60d = float(returns_60d.std())

        # ── Beta（简化：个股对沪深300的敏感度） ──
        # 完整版需要基准指数数据，此处用简化方法：个股波动 / 市场波动近似
        if result.vol_60d and result.vol_60d > 0:
            result.beta = min(result.vol_60d / 0.015, 3.0)  # 0.015 为 A 股日均波动近似值

        return result

    # baostock 网络类错误码：连接被服务端断开/未登录等（与 RatingEngine 一致）
    _BS_NETWORK_ERRORS = frozenset({
        "10001001", "10002001", "10002002", "10002003", "10002004",
        "10002005", "10002006", "10002007", "10002008",
    })

    def _bs_query_with_reconnect(self, query_fn, *args, **kwargs):
        """执行 baostock 查询；连接被断开（WinError 10054 等）时 logout+login 重试。

        网络类错误码或抛异常才重连；"无数据"等正常错误码直接返回不重试。
        """
        import baostock as bs
        import time
        rs = None
        for attempt in range(3):
            try:
                rs = query_fn(*args, **kwargs)
            except Exception:
                rs = None
            if rs is not None and str(getattr(rs, "error_code", "0")) == "0":
                return rs
            err = getattr(rs, "error_code", None) if rs is not None else None
            is_network = rs is None or str(err) in self._BS_NETWORK_ERRORS
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

    def _get_fundamental(self, symbol: str) -> Optional[dict]:
        """通过 baostock 获取基本面数据（带缓存）。"""
        if symbol in self._fundamental_cache:
            return self._fundamental_cache[symbol]

        try:
            import baostock as bs

            bs.login()
            try:
                # 获取股票基本资料
                bs_code = self._to_bs_code(symbol)
                if not bs_code:
                    self._fundamental_cache[symbol] = None
                    return None

                # 获取最近4个季度的财务报表（取最新季度）
                import datetime
                today = datetime.date.today()
                year = today.year
                latest_data = {}

                # 利润表数据（ROE, EPS等）
                for q in range(1, 5):
                    rs = self._bs_query_with_reconnect(bs.query_profit_data, code=bs_code, year=year, quarter=q)
                    if rs.error_code == "0":
                        while rs.next():
                            row = rs.get_row_data()
                            fields = rs.fields
                            data = dict(zip(fields, row))
                            if data.get("roeAvg"):
                                latest_data["roe"] = float(data["roeAvg"])
                            if data.get("epsTTM"):
                                latest_data["eps"] = float(data["epsTTM"])
                            if data.get("npMargin"):
                                latest_data["net_profit_margin"] = float(data["npMargin"])
                            break
                if not latest_data.get("roe"):
                    # 尝试上一年
                    for q in range(1, 5):
                        rs = self._bs_query_with_reconnect(bs.query_profit_data, code=bs_code, year=year - 1, quarter=q)
                        if rs.error_code == "0":
                            while rs.next():
                                row = rs.get_row_data()
                                data = dict(zip(rs.fields, row))
                                if data.get("roeAvg"):
                                    latest_data["roe"] = float(data["roeAvg"])
                                latest_data["eps"] = float(data.get("epsTTM", 0) or 0)
                                break

                # 成长性数据
                for q in range(1, 5):
                    rs = self._bs_query_with_reconnect(bs.query_growth_data, code=bs_code, year=year, quarter=q)
                    if rs.error_code == "0":
                        while rs.next():
                            row = rs.get_row_data()
                            data = dict(zip(rs.fields, row))
                            if data.get("YOYNI"):
                                latest_data["profit_growth"] = float(data["YOYNI"])
                            if data.get("YOYEquity"):
                                latest_data["equity_growth"] = float(data["YOYEquity"])
                            break

                # 现金流数据
                for q in range(1, 5):
                    rs = self._bs_query_with_reconnect(bs.query_cash_flow_data, code=bs_code, year=year, quarter=q)
                    if rs.error_code == "0":
                        while rs.next():
                            row = rs.get_row_data()
                            data = dict(zip(rs.fields, row))
                            if data.get("CFOToNP"):
                                latest_data["cfo_to_np"] = float(data["CFOToNP"])
                            break

                # 获取最新价格计算 PE/PB
                df = self.engine.get_ohlcv(symbol)
                if df is not None and not df.empty and latest_data.get("eps"):
                    last_close = float(df["close"].iloc[-1])
                    eps = latest_data["eps"]
                    if eps > 0:
                        latest_data["pe"] = round(last_close / eps, 2)
                    # PB 需要每股净资产，可从 balance 获取
                    for q in range(1, 5):
                        rs = self._bs_query_with_reconnect(bs.query_balance_data, code=bs_code, year=year, quarter=q)
                        if rs.error_code == "0":
                            while rs.next():
                                row = rs.get_row_data()
                                data = dict(zip(rs.fields, row))
                                if data.get("assetToEquity"):
                                    latest_data["asset_to_equity"] = float(data["assetToEquity"])
                                break

                if latest_data:
                    self._fundamental_cache[symbol] = latest_data
                    return latest_data

            finally:
                bs.logout()

        except ImportError:
            logger.log(5, "baostock 未安装，跳过基本面因子")
        except Exception as exc:
            logger.log(5, f"[{symbol}] 基本面数据获取失败: {exc}")

        self._fundamental_cache[symbol] = None
        return None

    @staticmethod
    def _to_bs_code(symbol: str) -> str:
        """转换 '000001' 为 'sh.600000' 或 'sz.000001'。"""
        sym = symbol.strip().zfill(6)
        if sym.startswith(("6", "9")):
            return f"sh.{sym}"
        else:
            return f"sz.{sym}"


# ── 横截面排名器 ──

class CrossSectionalRanker:
    """横截面因子排名与加权合成。

    核心方法:
      1. 对每个因子，将所有股票值做百分位排名（0~1）
      2. 根据因子方向调整排名（如 PE 越低越好 → 取反向排名）
      3. 按配置权重加权求和得到复合分
    """

    FACTOR_DIRECTION = {
        # value factors: 越低越好
        "pe": -1, "pb": -1, "ps": -1,
        # quality factors: 越高越好
        "roe": 1, "gross_margin": 1,
        # debt_ratio: 越低越好
        "debt_ratio": -1,
        # size: 越小越好（小盘溢价）
        "market_cap": -1,
        # momentum: 越高越好
        "momentum_12m1": 1,
        # low vol: 越低越好
        "vol_60d": -1, "beta": -1,
    }

    @classmethod
    def rank_and_combine(
        cls,
        factor_results: list[FactorResult],
        config: Optional[FactorConfig] = None,
    ) -> list[FactorResult]:
        """横截面排名 + 加权合成。

        Args:
            factor_results: 因子计算结果列表。
            config: 因子权重配置。

        Returns:
            更新了 composite_score 和 percentile_rank 的列表，按总分降序排列。
        """
        if not factor_results:
            return []

        cfg = config or FactorConfig()
        weights = cfg.normalized()
        if not weights:
            return factor_results

        # 收集各因子值
        factor_data: dict[str, list[tuple[int, Optional[float]]]] = {
            "pe": [],
            "pb": [],
            "roe": [],
            "gross_margin": [],
            "debt_ratio": [],
            "market_cap": [],
            "momentum_12m1": [],
            "vol_60d": [],
        }

        for idx, r in enumerate(factor_results):
            factor_data["pe"].append((idx, r.pe))
            factor_data["pb"].append((idx, r.pb))
            factor_data["roe"].append((idx, r.roe))
            factor_data["gross_margin"].append((idx, r.gross_margin))
            factor_data["debt_ratio"].append((idx, r.debt_ratio))
            factor_data["market_cap"].append((idx, r.market_cap))
            factor_data["momentum_12m1"].append((idx, r.momentum_12m1))
            factor_data["vol_60d"].append((idx, r.vol_60d))

        # 按大类计算百分位排名
        category_percentiles: dict[int, dict[str, float]] = {i: {} for i in range(len(factor_results))}

        # 价值子因子
        cls._rank_subfactors(factor_results, factor_data, category_percentiles,
                            ["pe", "pb"], "value", weights.get("value", 0))

        # 质量子因子
        cls._rank_subfactors(factor_results, factor_data, category_percentiles,
                            ["roe", "gross_margin", "debt_ratio"], "quality", weights.get("quality", 0))

        # 动量
        cls._rank_subfactors(factor_results, factor_data, category_percentiles,
                            ["momentum_12m1"], "momentum", weights.get("momentum", 0))

        # 低波动
        cls._rank_subfactors(factor_results, factor_data, category_percentiles,
                            ["vol_60d"], "low_vol", weights.get("low_vol", 0))

        # 规模
        cls._rank_subfactors(factor_results, factor_data, category_percentiles,
                            ["market_cap"], "size", weights.get("size", 0))

        # 加权合成
        for i, r in enumerate(factor_results):
            r.factor_percentiles = category_percentiles.get(i, {})
            r.composite_score = sum(r.factor_percentiles.values())
            r.percentile_rank = r.composite_score  # 0~1 之间，越大越好

        # 按总分降序
        factor_results.sort(key=lambda x: x.composite_score, reverse=True)

        logger.info(
            f"横截面排名完成: Top5 总分={[round(r.composite_score, 3) for r in factor_results[:5]]}"
        )
        return factor_results

    @classmethod
    def _rank_subfactors(
        cls,
        results: list[FactorResult],
        factor_data: dict,
        percentile_map: dict[int, dict[str, float]],
        factor_names: list[str],
        category: str,
        category_weight: float,
    ) -> None:
        """对一组子因子计算百分位排名并合并到对应大类。"""
        if category_weight <= 0:
            return

        sub_scores: dict[int, float] = {i: 0.0 for i in range(len(results))}
        valid_count = 0

        for fname in factor_names:
            direction = cls.FACTOR_DIRECTION.get(fname, 1)
            entries = factor_data.get(fname, [])
            valid_entries = [(idx, v) for idx, v in entries if v is not None and not np.isnan(v)]

            if len(valid_entries) < 5:
                continue

            values = np.array([v for _, v in valid_entries])
            # 百分位排名: 0~1
            ranks = (pd.Series(values).rank(pct=True) - 0.5).values  # center at 0

            for (idx, _), rank in zip(valid_entries, ranks):
                if direction < 0:
                    rank = -rank  # 反向（值越低排名越高）
                sub_scores[idx] += rank

            valid_count += 1

        if valid_count == 0:
            return

        # 平均子因子得分，映射到 0~1（clip 到 0~1）
        for i in range(len(results)):
            raw = sub_scores[i] / valid_count
            # map from [-0.5, 0.5] to [0, 1]
            normalized = np.clip(raw + 0.5, 0.0, 1.0)
            percentile_map[i][category] = normalized * category_weight

    @classmethod
    def get_top_quantile(
        cls,
        factor_results: list[FactorResult],
        top_fraction: float = 0.20,
    ) -> list[FactorResult]:
        """获取排名前 top_fraction 的股票（如 20% = 五分位最高组）。

        Fama-French 方法中，通常取 top 30% 做多，bottom 30% 做空。
        """
        if not factor_results:
            return []
        cutoff = max(1, int(len(factor_results) * top_fraction))
        return factor_results[:cutoff]
