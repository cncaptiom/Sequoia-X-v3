"""选股评分模块：对策略交集股票进行综合评分，选出最佳 N 只。"""

from dataclasses import dataclass
from typing import Optional

from datetime import date, timedelta

from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine

logger = get_logger(__name__)


@dataclass
class ScorerConfig:
    """评分器权重配置，可通过环境变量 SCORER_WEIGHTS 覆盖。

    格式: SCORER_WEIGHTS="1.0,1.2,0.8,1.0" 对应 vol, momentum, trend, stability。
    """
    vol_weight: float = 1.0        # 成交量评分权重
    momentum_weight: float = 1.0   # 动量评分权重
    trend_weight: float = 1.0      # 趋势评分权重
    stability_weight: float = 1.0  # 稳定性评分权重

    @classmethod
    def from_env(cls) -> "ScorerConfig":
        """从环境变量读取权重配置。"""
        import os
        raw = os.environ.get("SCORER_WEIGHTS", "")
        if raw:
            try:
                parts = [float(x.strip()) for x in raw.split(",")]
                if len(parts) == 4:
                    return cls(*parts)
            except ValueError:
                pass
        return cls()


def get_top_stocks(
    symbols: list[str],
    engine: DataEngine,
    top_n: int = 5,
    config: Optional[ScorerConfig] = None,
    max_staleness_days: int = 5,
) -> list[dict]:
    """对股票列表进行多维度综合评分，返回得分最高的 top_n 只。

    评分维度（各 0~5 分，可配置权重）:
      1. 成交量评分：当日量 / 20日均量，放大越多分越高
      2. 动量评分：近 5 日涨幅，越大分越高
      3. 趋势评分：均线多头排列加分
      4. 稳定性评分：20 日波动率越低分越高

    Args:
        symbols: 待评分的股票代码列表。
        engine: 数据引擎。
        top_n: 返回前 N 只，默认 5。
        config: 可选，自定义权重配置（默认等权）。

    Returns:
        按总分降序排列的列表，每项含 symbol / score / close / pct_chg / vol_ratio / details / last_date。
        数据超过 max_staleness_days 个自然日的股票会被跳过。
    """
    cfg = config or ScorerConfig()
    scored: list[dict] = []

    for symbol in symbols:
        try:
            df = engine.get_ohlcv(symbol)
            if len(df) < 20:
                continue

            # 数据新鲜度检查
            last_date_str = str(df["date"].iloc[-1])
            last_date = date.fromisoformat(last_date_str)
            if (date.today() - last_date).days > max_staleness_days:
                logger.log(5, f"[{symbol}] 数据过期 ({last_date_str})，跳过评分")
                continue

            last = df.iloc[-1]
            prev = df.iloc[-2] if len(df) >= 2 else last

            # 1. 成交量评分 (0-5)
            vol_ma20 = df["volume"].tail(20).mean()
            vol_ratio = last["volume"] / vol_ma20 if vol_ma20 > 0 else 1
            vol_score = min(vol_ratio * 2, 5) * cfg.vol_weight

            # 2. 动量评分 (0-5)
            chg_5d = (
                (last["close"] - df.iloc[-5]["close"])
                / df.iloc[-5]["close"]
                * 100
                if len(df) >= 5
                else 0
            )
            momentum_score = min(max(chg_5d * 0.3, 0), 5) * cfg.momentum_weight

            # 3. 趋势评分 (0-5)
            ma5 = df["close"].rolling(5).mean().iloc[-1]
            ma10 = df["close"].rolling(10).mean().iloc[-1]
            ma20_val = df["close"].rolling(20).mean().iloc[-1]
            if ma5 > ma10 > ma20_val:
                trend_score = 4 * cfg.trend_weight
            elif ma5 > ma20_val:
                trend_score = 2 * cfg.trend_weight
            else:
                trend_score = 0

            # 4. 稳定性评分 (0-5)
            volatility = (
                df["close"].tail(20).std() / df["close"].tail(20).mean()
            )
            stability_score = max(5 - volatility * 25, 0) * cfg.stability_weight

            total = vol_score + momentum_score + trend_score + stability_score
            pct_chg = (
                (last["close"] - prev["close"]) / prev["close"] * 100
            )

            scored.append(
                {
                    "symbol": symbol,
                    "score": round(total, 1),
                    "close": round(last["close"], 2),
                    "pct_chg": round(pct_chg, 2),
                    "vol_ratio": round(vol_ratio, 2),
                    "last_date": last_date_str,
                    "details": {
                        "成交量": round(vol_score, 1),
                        "动量": round(momentum_score, 1),
                        "趋势": round(trend_score, 1),
                        "稳定性": round(stability_score, 1),
                    },
                }
            )

        except Exception as exc:
            logger.warning(f"[{symbol}] 评分失败: {exc}")
            continue

    scored.sort(key=lambda x: x["score"], reverse=True)
    logger.info(f"评分完成: 共 {len(scored)} 只，Top {top_n} 已选出")
    return scored[:top_n]
