"""资金流向分析模块：北向资金、融资融券、大宗交易。

数据来源: akshare
  - 北向资金: stock_hsgt_* 系列
  - 融资融券: stock_margin_* 系列
  - 大宗交易: stock_dzjy_* 系列

机构应用 (AQR 风格):
  - 北向资金持续流入的个股有 Alpha 信号
  - 融资余额快速上升 = 散户情绪过热，可能是反向信号
  - 大宗交易折价 = 机构减持信号
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class FlowSignal:
    """资金流向信号。"""
    symbol: str
    name: str = ""

    # 北向资金
    north_flow_5d: Optional[float] = None       # 近5日北向净买入（万元）
    north_flow_20d: Optional[float] = None      # 近20日北向净买入
    north_hold_pct: Optional[float] = None      # 北向持股比例

    # 融资融券
    margin_balance: Optional[float] = None       # 融资余额（万元）
    margin_balance_chg_5d: Optional[float] = None  # 近5日融资余额变化率
    short_balance: Optional[float] = None        # 融券余额

    # 大宗交易
    block_trade_discount: Optional[float] = None # 最近大宗交易折价率
    block_trade_volume: Optional[float] = None   # 大宗交易量占比

    # 综合资金流向评分 (0~1)
    flow_score: float = 0.0


class FlowAnalyzer:
    """资金流向分析器。"""

    def __init__(self):
        self._cache: dict[str, FlowSignal] = {}

    def analyze_stock(self, symbol: str) -> Optional[FlowSignal]:
        """分析单只股票的资金流向。

        Args:
            symbol: 股票代码（纯数字）。

        Returns:
            FlowSignal 或 None。
        """
        if symbol in self._cache:
            return self._cache[symbol]

        signal = FlowSignal(symbol=symbol)
        score_components = []
        valid_count = 0

        try:
            import akshare as ak

            # ── 北向资金 ──
            try:
                today = date.today()
                start = (today - timedelta(days=30)).strftime("%Y%m%d")
                end = today.strftime("%Y%m%d")

                hsgt_df = ak.stock_hsgt_individual_em(
                    symbol=symbol, start_date=start, end_date=end
                )
                if hsgt_df is not None and not hsgt_df.empty:
                    # 近期净买入
                    if "net_buy" in hsgt_df.columns or "成交量" in hsgt_df.columns:
                        # 尝试获取近5日/20日净买入
                        recent = hsgt_df.tail(5)
                        total_net = 0.0
                        for col in ["net_buy", "净买入", "买入金额"]:
                            if col in recent.columns:
                                total_net = recent[col].sum()
                                break

                        signal.north_flow_5d = round(float(total_net), 2)

                        # 北向持续净流入得正分
                        if signal.north_flow_5d and signal.north_flow_5d > 0:
                            score_components.append(min(signal.north_flow_5d / 1e8, 1.0))
                            valid_count += 1
            except Exception:
                pass

            # ── 融资融券 ──
            try:
                margin_df = ak.stock_margin_detail_sse(date=end)
                if margin_df is not None and not margin_df.empty:
                    stock_row = margin_df[margin_df["股票代码"] == symbol]
                    if not stock_row.empty:
                        signal.margin_balance = float(stock_row["融资余额"].iloc[0])
            except Exception:
                # 深交所
                try:
                    margin_df = ak.stock_margin_detail_szse(date=end)
                    if margin_df is not None and not margin_df.empty:
                        stock_row = margin_df[margin_df["股票代码"] == symbol]
                        if not stock_row.empty:
                            signal.margin_balance = float(stock_row["融资余额"].iloc[0])
                except Exception:
                    pass

            # ── 大宗交易 ──
            try:
                block_df = ak.stock_dzjy_mrmx(symbol=symbol, start_date=start, end_date=end)
                if block_df is not None and not block_df.empty:
                    # 计算平均折价率
                    if "折溢率" in block_df.columns or "折价率" in block_df.columns:
                        col = "折溢率" if "折溢率" in block_df.columns else "折价率"
                        discounts = pd.to_numeric(block_df[col], errors="coerce").dropna()
                        if not discounts.empty:
                            signal.block_trade_discount = round(float(discounts.mean()), 2)

                            # 大宗折价 < -5% 是减分项
                            if signal.block_trade_discount < -5:
                                score_components.append(-0.3)
                                valid_count += 1
                            elif signal.block_trade_discount > 0:
                                score_components.append(0.2)
                                valid_count += 1
            except Exception:
                pass

        except ImportError:
            logger.debug("akshare 未安装，跳过资金流向分析")
        except Exception as exc:
            logger.debug(f"[{symbol}] 资金流向分析失败: {exc}")

        # ── 综合评分 ──
        if valid_count > 0:
            signal.flow_score = round(np.clip(sum(score_components) / valid_count, -1, 1), 3)
        else:
            signal.flow_score = 0.0

        self._cache[symbol] = signal
        return signal

    def scan_market(self, symbols: list[str]) -> list[FlowSignal]:
        """批量扫描全市场资金流向。

        Returns:
            按 flow_score 降序排列的信号列表。
        """
        results = []
        for i, symbol in enumerate(symbols):
            try:
                signal = self.analyze_stock(symbol)
                if signal is not None:
                    results.append(signal)
            except Exception:
                continue

            if (i + 1) % 500 == 0:
                logger.info(f"资金流向扫描: {i + 1}/{len(symbols)}")

        results.sort(key=lambda x: x.flow_score, reverse=True)
        logger.info(
            f"资金流向扫描完成: {len(results)} 只 | "
            f"净流入 Top5: {[r.symbol for r in results[:5]]}"
        )
        return results

    def get_top_inflow(self, signals: list[FlowSignal], top_n: int = 20) -> list[FlowSignal]:
        """获取资金流入最多的前 N 只股票。"""
        return [s for s in signals if s.flow_score > 0][:top_n]

    def get_top_outflow(self, signals: list[FlowSignal], top_n: int = 20) -> list[FlowSignal]:
        """获取资金流出最多的前 N 只股票（flow_score 最低）。"""
        sorted_reverse = sorted(signals, key=lambda x: x.flow_score)
        return [s for s in sorted_reverse if s.flow_score < 0][:top_n]
