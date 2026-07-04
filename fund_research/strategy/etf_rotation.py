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

    # 波动率控制（惩罚高波动ETF）
    vol_penalty_threshold: float = 0.80  # 波动率超过此百分位时惩罚
    vol_penalty_max: float = 1.0         # 最大波动率扣分

    # 趋势过滤（MA60线下降分）
    trend_filter_penalty: float = 1.0    # 价格在MA60之下时扣分（偏离越远扣越多）

    # 拥挤度控制
    max_same_category: int = 2        # 同一行业最多入选几只


@dataclass
class RotationSignal:
    """单期调仓信号。"""
    date: str
    selected: list[str]               # 选中的ETF代码（买入）
    scores: dict[str, float]          # 各ETF评分
    reasons: dict[str, list[str]]     # 评分理由
    positions_info: dict[str, dict] = field(default_factory=dict)  # code → {price, atr, stop, target}


class EtfRotationStrategy:
    """ETF 轮动策略引擎。"""

    def __init__(self, config: Optional[RotationConfig] = None):
        self.config = config or RotationConfig()
        self._prev_selected: list[str] = []

    def run(self, data: dict[str, pd.DataFrame], current_date: str) -> RotationSignal:
        """执行一期轮动选基。"""
        cfg = self.config

        # ── 横截面波动率计算（用于波动率控制） ──
        vol_list: list[float] = []
        for code, df in data.items():
            if len(df) < 21:
                continue
            ret = df["close"].pct_change().dropna().tail(20)
            vol_list.append(float(ret.std()))

        vol_threshold = 0.0
        if vol_list:
            vol_sorted = sorted(vol_list)
            n = len(vol_sorted)
            threshold_idx = min(int(n * cfg.vol_penalty_threshold), n - 1)
            vol_threshold = vol_sorted[threshold_idx]

        # ── 逐只评分 ──
        scores: dict[str, float] = {}
        reasons: dict[str, list[str]] = {}

        for code, df in data.items():
            if len(df) < cfg.min_bars:
                continue
            score, sigs = self._score_single(df, code, vol_threshold)
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

        # ── 止损止盈计算（基于ATR） ──
        positions_info: dict[str, dict] = {}
        for code in selected:
            df = data.get(code)
            if df is None or len(df) < 20:
                continue
            closes = df["close"].values
            current_price = float(closes[-1])
            atr = self._calc_atr(df, 14)
            if atr <= 0:
                continue
            positions_info[code] = {
                "price": current_price,
                "atr": atr,
                "stop_loss": round(current_price - 2.0 * atr, 3),
                "target_1": round(current_price + 2.0 * atr, 3),
                "target_2": round(current_price + 3.0 * atr, 3),
            }

        return RotationSignal(
            date=current_date,
            selected=selected,
            scores=scores,
            reasons=reasons,
            positions_info=positions_info,
        )

    def _score_single(self, df: pd.DataFrame, code: str,
                       vol_threshold: float = 0.0
                       ) -> tuple[Optional[float], list[str]]:
        """对单只ETF评分。"""
        signals: list[str] = []
        closes = df["close"].values
        n = len(closes)
        cfg = self.config

        # ── 动量评分 ──
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

        # 固定权重总和归一化：缺失数据时评分自然降低，不被人为抬高
        total_expected_weight = cfg.weight_mom_1m + cfg.weight_mom_3m + cfg.weight_mom_6m
        total_score = mom_score / total_expected_weight

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

        # ── 波动率控制 ──
        if vol_threshold > 0 and n >= 21:
            ret = pd.Series(closes).pct_change().dropna().tail(20)
            current_vol = float(ret.std())
            if current_vol > vol_threshold:
                # 波动率超过阈值，按超出比例扣分
                vol_ratio = current_vol / vol_threshold
                penalty = min((vol_ratio - 1) * cfg.vol_penalty_max, cfg.vol_penalty_max)
                total_score -= penalty
                signals.append(f"波动率{current_vol:.4f}惩罚-{penalty:.1f}")

        # ── MA60 趋势过滤 ──
        if n >= 60:
            ma60 = pd.Series(closes).rolling(60).mean().iloc[-1]
            last_close = closes[-1]
            if last_close < ma60:
                # 偏离MA60越远扣分越多
                deviation = (ma60 - last_close) / ma60
                penalty = min(deviation * 10, cfg.trend_filter_penalty)
                total_score -= penalty
                signals.append(f"MA60线下-{penalty:.1f}")

        return round(total_score, 2), signals

    def reset(self):
        """重置状态（新回测前调用）。"""
        self._prev_selected = []

    @staticmethod
    def _calc_atr(df: pd.DataFrame, period: int = 14) -> float:
        """计算ATR（Average True Range）。"""
        if len(df) < period + 1:
            return 0.0
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values
        tr = np.maximum(high[-1] - low[-1],
                        np.maximum(abs(high[-1] - close[-2]),
                                   abs(low[-1] - close[-2])))
        # 取最近period天的平均TR
        tr_list = []
        for i in range(-period, 0):
            tr_list.append(max(high[i] - low[i],
                               abs(high[i] - close[i - 1]),
                               abs(low[i] - close[i - 1])))
        return float(np.mean(tr_list)) if tr_list else 0.0
