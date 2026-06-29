"""ETF 轮动策略。

核心逻辑：
  1. 对 ETF 池中的每只标的计算多因子评分
  2. 按总分排序，选 Top N 持有
  3. 定期调仓（默认月频）

评分因子：
  - 动量因子：1月/3月/6月涨幅（加权合成）
  - 趋势因子：MA5/MA20 多头排列
  - 量价因子：成交量确认
  - 拥挤度惩罚：前一期已入选的ETF加分（减少换手率）
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from fund_research.core.logger import get_logger
from fund_research.data.etf_list import get_etf_map, EtfInfo

logger = get_logger(__name__)


@dataclass
class RotationConfig:
    """轮动策略配置。"""
    top_n: int = 5                    # 每期持有数量
    hold_days: int = 21               # 调仓间隔（约1个月）
    min_bars: int = 60                # 最少K线数

    # 动量权重（以月为单位）
    weight_mom_1m: float = 0.30
    weight_mom_3m: float = 0.35
    weight_mom_6m: float = 0.35

    # 技术面加分
    bonus_trend: float = 2.0          # 均线多头排列
    bonus_volume: float = 1.0         # 放量突破
    bonus_holding: float = 1.5        # 上期已持有（降低换手）

    # 拥挤度控制
    max_same_category: int = 2        # 同一行业最多入选几只


@dataclass
class RotationSignal:
    """单期调仓信号。"""
    date: str
    selected: list[str]               # 选中的ETF代码（买入）
    scores: dict[str, float]          # 各ETF评分
    reasons: dict[str, list[str]]     # 评分理由


class EtfRotationStrategy:
    """ETF 轮动策略引擎。"""

    def __init__(self, config: Optional[RotationConfig] = None):
        self.config = config or RotationConfig()
        self._prev_selected: list[str] = []

    def run(self, data: dict[str, pd.DataFrame], current_date: str) -> RotationSignal:
        """执行一期轮动选基。

        Args:
            data: {code: DataFrame(ohlcv)} 全ETF池数据。
            current_date: 当前交易日。

        Returns:
            RotationSignal 包含选中标的和评分。
        """
        scores: dict[str, float] = {}
        reasons: dict[str, list[str]] = {}

        for code, df in data.items():
            if len(df) < self.config.min_bars:
                continue
            score, sigs = self._score_single(df, code)
            if score is not None:
                scores[code] = score
                reasons[code] = sigs

        # 加入上期持有加分
        for code in self._prev_selected:
            if code in scores:
                scores[code] += self.config.bonus_holding
                reasons[code].append(f"上期持有+{self.config.bonus_holding:.0f}")

        # 按总分降序排列
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        # 行业分散：同一类别最多选 max_same_category 只
        from fund_research.data.etf_list import get_etf_map
        etf_map = get_etf_map()
        selected: list[str] = []
        cat_count: dict[str, int] = {}
        for code, score in ranked:
            if len(selected) >= self.config.top_n:
                break
            cat = getattr(etf_map.get(code), "category", "其他")
            if cat_count.get(cat, 0) >= self.config.max_same_category:
                continue
            selected.append(code)
            cat_count[cat] = cat_count.get(cat, 0) + 1

        self._prev_selected = selected
        logger.info(
            f"轮动结果 [{current_date}]: 选中{len(selected)}只 "
            f"{' '.join(etf_map.get(c, EtfInfo('','','')).name for c in selected)}"
        )
        return RotationSignal(
            date=current_date,
            selected=selected,
            scores=scores,
            reasons=reasons,
        )

    def _score_single(self, df: pd.DataFrame, code: str
                       ) -> tuple[Optional[float], list[str]]:
        """对单只ETF评分。"""
        signals: list[str] = []
        closes = df["close"].values
        n = len(closes)

        # ── 动量评分 ──
        cfg = self.config
        mom_score = 0.0
        mom_count = 0

        for months, weight, label in [
            (1, cfg.weight_mom_1m, "1月"),
            (3, cfg.weight_mom_3m, "3月"),
            (6, cfg.weight_mom_6m, "6月"),
        ]:
            period = months * 21  # 约21个交易日/月
            if n < period + 1:
                continue
            ret = (closes[-1] - closes[-period - 1]) / closes[-period - 1]
            # 将收益率映射到 [-3, +3] 分
            s = np.clip(ret * 15, -3, 3)
            mom_score += s * weight
            mom_count += weight
            signals.append(f"动量{label}={ret:.1%}→{s:.1f}分(×{weight:.2f})")

        if mom_count == 0:
            return None, signals

        total_score = mom_score / mom_count if mom_count > 0 else 0

        # ── 趋势加分 ──
        if n >= 21:
            ma5 = pd.Series(closes).rolling(5).mean().iloc[-1]
            ma20 = pd.Series(closes).rolling(20).mean().iloc[-1]
            ma20_prev = pd.Series(closes).rolling(20).mean().iloc[-2]
            last_close = closes[-1]

            if ma5 > ma20 and last_close > ma5:
                total_score += cfg.bonus_trend
                signals.append(f"趋势多头+{cfg.bonus_trend:.0f}")

            # MA20 方向向上
            if ma20 > ma20_prev:
                total_score += 1
                signals.append("MA20上翘+1")

        # ── 量价加分 ──
        if n >= 21:
            vol = df["volume"].values
            vol_ma20 = pd.Series(vol).rolling(20).mean().iloc[-1]
            if vol_ma20 > 0 and vol[-1] > vol_ma20 * 1.5:
                total_score += cfg.bonus_volume
                signals.append(f"放量突破+{cfg.bonus_volume:.0f}")

        return round(total_score, 2), signals

    def reset(self):
        """重置状态（新回测前调用）。"""
        self._prev_selected = []
