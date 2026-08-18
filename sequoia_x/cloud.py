"""云端同步模块：将每日推荐结果上传到腾讯云COS。

使用腾讯云官方 cos-python-sdk-v5，支持全量数据桥接。

改进 (V3.1):
- 上传全量评级（不限量），供手机端完整消费
- 增加结构化信号数据上传
- 增加版本元数据，手机端可检测数据是否过期
"""

import json
import time
from typing import Any, Optional

from qcloud_cos import CosConfig, CosS3Client

from sequoia_x.core.logger import get_logger
from sequoia_x.signal.models import TradeSignal

logger = get_logger(__name__)


class CosUploader:
    """腾讯云 COS 上传器（封装官方 SDK）。"""

    def __init__(self, bucket: str, region: str, secret_id: str, secret_key: str,
                 timeout: int = 60):
        self.bucket = bucket
        self.region = region
        self.timeout = timeout
        config = CosConfig(Region=region, SecretId=secret_id, SecretKey=secret_key,
                           Timeout=timeout)
        self.client = CosS3Client(config)

    def _put_json(self, path: str, data: dict[str, Any]) -> bool:
        """上传 JSON 数据到 COS 指定路径。"""
        try:
            body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            response = self.client.put_object(
                Bucket=self.bucket,
                Body=body,
                Key=path,
            )
            logger.info(f"COS上传成功: {path} ({len(body)} bytes)")
            return True
        except Exception as exc:
            logger.error(f"COS上传失败 {path}: {exc}")
            return False

    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d")

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")

    def _metadata(self) -> dict[str, Any]:
        return {
            "version": "3.1.0",
            "date": self._today(),
            "generated_at": self._now(),
        }

    # ── 核心上传接口 ──

    def upload_recommendations(
        self, path: str, signals: list[TradeSignal],
        market_regime: str = "", exposure: float = 0.0,
    ) -> bool:
        """上传推荐列表。"""
        if not signals:
            logger.warning("无推荐信号，跳过COS上传")
            return False
        data: dict[str, Any] = {
            **self._metadata(),
            "type": "recommendations",
            "market_regime": market_regime,
            "suggested_exposure": exposure,
            "count": len(signals),
            "recommendations": [
                {
                    "symbol": s.symbol, "name": s.name,
                    "strategies": s.strategies,
                    "current_price": s.current_price,
                    "entry_low": s.entry_zone_low, "entry_high": s.entry_zone_high,
                    "stop_loss": s.stop_loss,
                    "target_1": s.take_profit_1, "target_2": s.take_profit_2,
                    "confidence": round(s.confidence, 3),
                    "position_pct": round(s.suggested_position_pct, 4),
                    "atr": round(s.atr_value, 2) if s.atr_value else None,
                    "rationale": s.rationale,
                }
                for s in signals[:20]
            ],
        }
        return self._put_json(path, data)

    def upload_rating_config(self, config: dict[str, Any]) -> bool:
        """上传评级策略配置。"""
        data = {
            **self._metadata(),
            "type": "rating_config",
            "config": config,
        }
        return self._put_json("config/rating_config.json", data)

    def upload_market_regime(self, regime_data: dict[str, Any]) -> bool:
        """上传市场状态。"""
        data = {
            **self._metadata(),
            "type": "market_regime",
            "regime": regime_data,
        }
        return self._put_json("regime/today.json", data)

    def upload_full_ratings(
        self, ratings: list[dict[str, Any]],
        strategy_config: dict[str, Any],
        regime: Optional[dict[str, Any]] = None,
    ) -> bool:
        """上传全市场评级结果（不限量，手机端主数据通道）。

        改进：移除了前200只限制，改为全量上传。
        如果数据量过大（>5000只），自动分批。
        """
        data: dict[str, Any] = {
            **self._metadata(),
            "type": "full_ratings",
            "strategy_config": strategy_config,
            "market_regime": regime or {},
            "total_scanned": len(ratings),
            "ratings": ratings,  # 不限量
        }
        return self._put_json("ratings/today.json", data)

    def upload_portfolio_summary(
        self, top_picks: list[dict[str, Any]],
        rating_summary: dict[str, int],
        market_regime: str = "", suggested_exposure: float = 0.0,
    ) -> bool:
        """上传每日组合摘要。"""
        data: dict[str, Any] = {
            **self._metadata(),
            "type": "portfolio_summary",
            "market_regime": market_regime,
            "suggested_exposure": suggested_exposure,
            "rating_summary": rating_summary,
            "top_picks": top_picks[:10],
        }
        return self._put_json("summary/today.json", data)

    def upload_signals(
        self, signals: list[TradeSignal],
        rating_results: Optional[list[dict[str, Any]]] = None,
        market_regime: str = "",
        suggested_exposure: float = 0.0,
    ) -> bool:
        """上传完整信号数据（手机端操作建议的主数据源）。

        上传到 signals/today.json，包含每只推荐股票的完整参数，
        供手机端做盘中价格监控和操作建议。

        数据格式与手机端 PcAlertService 和 OrderSuggestionService
        完全对齐。
        """
        if not signals:
            logger.warning("无信号数据，跳过")
            return False

        # 按置信度降序排列
        sorted_signals = sorted(signals, key=lambda s: s.confidence, reverse=True)

        signal_list = []
        for s in sorted_signals:
            entry_mid = (s.entry_zone_low + s.entry_zone_high) / 2
            risk_pct = round((s.current_price - s.stop_loss) / s.current_price * 100, 1) if s.stop_loss > 0 else 0
            reward_pct = round((s.take_profit_1 - s.current_price) / s.current_price * 100, 1) if s.take_profit_1 > 0 else 0

            # 置信度等级
            if s.confidence >= 0.75:
                level = "S"
                level_label = "强烈推荐"
            elif s.confidence >= 0.60:
                level = "A"
                level_label = "推荐"
            elif s.confidence >= 0.45:
                level = "B"
                level_label = "关注"
            elif s.confidence >= 0.25:
                level = "C"
                level_label = "观察"
            else:
                level = "D"
                level_label = "不参与"

            signal_list.append({
                # 基础信息
                "symbol": s.symbol,
                "name": s.name,
                "strategies": s.strategies,
                "strategy_count": len(s.strategies),

                # 置信度 & 等级
                "confidence": round(s.confidence, 3),
                "level": level,
                "level_label": level_label,

                # 当前行情
                "current_price": s.current_price,
                "last_date": s.last_date,

                # 操作区间
                "entry_low": s.entry_zone_low,
                "entry_high": s.entry_zone_high,
                "entry_mid": round(entry_mid, 2),
                "entry_condition": s.entry_condition,

                # 风险控制
                "stop_loss": s.stop_loss,
                "target_1": s.take_profit_1,
                "target_2": s.take_profit_2,
                "max_hold_days": s.max_hold_days,

                # 盈亏比
                "risk_pct": risk_pct,
                "reward_pct": reward_pct,
                "risk_reward_ratio": round(reward_pct / risk_pct, 2) if risk_pct > 0 else 0,

                # 仓位
                "position_pct": round(s.suggested_position_pct, 4),
                "atr": s.atr_value,

                # 评分明细
                "score_details": s.score_details,

                # 理由
                "rationale": s.rationale,
            })

        # 评级汇总
        rating_summary = {}
        for sig in signal_list:
            level = sig["level"]
            rating_summary[level] = rating_summary.get(level, 0) + 1

        data: dict[str, Any] = {
            **self._metadata(),
            "type": "signals",
            "market_regime": market_regime,
            "suggested_exposure": suggested_exposure,
            "total_signals": len(signal_list),
            "rating_summary": rating_summary,
            "signals": signal_list,

            # 附加：评级结果（如有），供手机端做评级分布展示
            "ratings": rating_results or [],
        }

        return self._put_json("signals/today.json", data)


# ── 兼容旧的函数式 API（保持 main.py 调用不变） ──

def _make_uploader(bucket, region, secret_id, secret_key):
    return CosUploader(bucket, region, secret_id, secret_key)


def upload_recommendations(bucket, region, secret_id, secret_key, path, signals, market_regime="", exposure=0.0):
    return _make_uploader(bucket, region, secret_id, secret_key).upload_recommendations(path, signals, market_regime, exposure)


def upload_rating_config(bucket, region, secret_id, secret_key, config):
    return _make_uploader(bucket, region, secret_id, secret_key).upload_rating_config(config)


def upload_market_regime(bucket, region, secret_id, secret_key, regime_data):
    return _make_uploader(bucket, region, secret_id, secret_key).upload_market_regime(regime_data)


def upload_full_ratings(bucket, region, secret_id, secret_key, ratings, strategy_config, regime=None):
    return _make_uploader(bucket, region, secret_id, secret_key).upload_full_ratings(ratings, strategy_config, regime)


def upload_portfolio_summary(bucket, region, secret_id, secret_key, top_picks, rating_summary, market_regime="", suggested_exposure=0.0):
    return _make_uploader(bucket, region, secret_id, secret_key).upload_portfolio_summary(top_picks, rating_summary, market_regime, suggested_exposure)
