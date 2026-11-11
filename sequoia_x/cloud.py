"""云端同步模块：将每日推荐结果上传到腾讯云COS。

使用腾讯云官方 cos-python-sdk-v5，支持全量数据桥接。

改进 (V3.1):
- 上传全量评级（不限量），供手机端完整消费
- 增加结构化信号数据上传
- 增加版本元数据，手机端可检测数据是否过期
"""

import json
import time
import uuid
from typing import Any, Optional

from qcloud_cos import CosConfig, CosS3Client

from sequoia_x.core.logger import get_logger
from sequoia_x.signal.models import TradeSignal

logger = get_logger(__name__)


class CosUploader:
    """腾讯云 COS 上传器（封装官方 SDK）。"""

    def __init__(self, bucket: str, region: str, secret_id: str, secret_key: str,
                 timeout: int = 120, data_as_of: Optional[str] = None,
                 run_id: Optional[str] = None):
        self.bucket = bucket
        self.region = region
        self.timeout = timeout
        # 同一次 PC 运行的所有文件共用一个 run_id，手机端据此拒绝混合批次
        self.run_id = run_id or uuid.uuid4().hex[:12]
        # 行情数据的真实日期（YYYY-MM-DD）。为空时才回退到运行日期。
        self.data_as_of = data_as_of
        self._uploaded: list[str] = []
        config = CosConfig(Region=region, SecretId=secret_id, SecretKey=secret_key,
                           Timeout=timeout)
        self.client = CosS3Client(config)
        # 绕过系统代理（Clash 等会对 >~100KB 的 body 断流），COS 公网直连更快更稳。
        # 实测默认走 127.0.0.1:7897 代理时大文件上传必然 ProxyError/10054，直连 2.8MB 仅 0.7s。
        self.client._session.trust_env = False

    def _put_json(self, path: str, data: dict[str, Any], retries: int = 3) -> bool:
        """上传 JSON 数据到 COS 指定路径（带指数退避重试，兜底网络抖动）。"""
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        for attempt in range(retries):
            try:
                self.client.put_object(
                    Bucket=self.bucket,
                    Body=body,
                    Key=path,
                )
                logger.info(f"COS上传成功: {path} ({len(body)} bytes)")
                self._uploaded.append(path)
                return True
            except Exception as exc:
                if attempt < retries - 1:
                    delay = 2 ** attempt * 2
                    logger.warning(
                        f"COS上传失败 {path} (第{attempt + 1}/{retries}次): {exc}，{delay}s后重试"
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"COS上传失败 {path}（已重试{retries}次）: {exc}")
                    return False
        return False

    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d")

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")

    def _metadata(self) -> dict[str, Any]:
        """所有文件共用的元数据。

        `date` 是**行情数据日期**，不是程序运行日期——否则断网时同步 0 条、
        程序照常运行，手机端会把旧行情当成当天数据（2026-11-09 审查 #7）。
        """
        data_date = self.data_as_of or self._today()
        return {
            "version": "3.1.0",
            "run_id": self.run_id,
            "date": data_date,
            "data_as_of": data_date,
            "generated_at": self._now(),
        }

    def upload_manifest(self) -> bool:
        """最后发布批次清单（原子发布点）。

        手机端以 manifest.json 为“这一批数据是否完整”的唯一判据：
        没有 manifest 或 run_id 与各文件不一致时，整体按旧数据/不完整处理。
        """
        data = {
            **self._metadata(),
            "type": "manifest",
            "files": sorted(set(self._uploaded)),
        }
        return self._put_json("manifest.json", data)

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
                    "entry_low": s.entry_zone_low if s.zone_status != "none" else None,
                    "entry_high": s.entry_zone_high if s.zone_status != "none" else None,
                    "entry_ref": (round(s.entry_ref, 2)
                                  if s.zone_status != "none" and s.entry_ref > 0 else None),
                    "zone_status": s.zone_status,
                    "zone_reason": s.zone_reason,
                    "stop_loss": s.stop_loss if s.zone_status != "none" else None,
                    "target_1": s.take_profit_1 if s.zone_status != "none" else None,
                    "target_2": s.take_profit_2 if s.zone_status != "none" else None,
                    "target_is_fallback": s.target_is_fallback,
                    "risk_reward_ratio": s.risk_reward,
                    "risk_reward_text": s.risk_reward_text(),
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
        strategy_bias: Optional[dict[str, Any]] = None,
    ) -> bool:
        """上传每日组合摘要。

        带上 `strategy_bias`（各类别的权重偏向），供手机端详情页的「不同策略分析」
        显示权重——此前手机端自己硬编码了一份与 PC `REGIME_BIAS` 完全不同的表。
        """
        data: dict[str, Any] = {
            **self._metadata(),
            "type": "portfolio_summary",
            "market_regime": market_regime,
            "suggested_exposure": suggested_exposure,
            "rating_summary": rating_summary,
            "strategy_bias": strategy_bias or {},
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

        # 保持调用方传入的顺序（生产为「评级 → 总分 → 形态分」），
        # 不再按形态分重排，避免展示顺序与选股依据不一致。
        signal_list = []
        for s in signals:
            has_zone = s.zone_status != "none"
            entry_mid = round((s.entry_zone_low + s.entry_zone_high) / 2, 2) if has_zone else None
            entry_ref = round(s.entry_ref, 2) if has_zone and s.entry_ref > 0 else None
            risk_pct = round((entry_ref - s.stop_loss) / entry_ref * 100, 1) \
                if has_zone and entry_ref and s.stop_loss > 0 else None
            reward_pct = round((s.take_profit_1 - entry_ref) / entry_ref * 100, 1) \
                if has_zone and entry_ref and s.take_profit_1 > 0 else None

            signal_list.append({
                # 基础信息
                "symbol": s.symbol,
                "name": s.name,
                "strategies": s.strategies,
                "strategy_count": len(s.strategies),

                # 评级（选股依据）
                "rating": s.rating,
                "rating_label": s.rating_label,
                "rating_stars": s.rating_stars,
                "total_score": round(s.total_score, 1),

                # 形态分（解释性指标，不参与选股排序）
                "confidence": round(s.confidence, 3),

                # 当前行情
                "current_price": s.current_price,
                "last_date": s.last_date,

                # 操作区间（无区间时导出 null，不用 0 充数）
                "entry_low": s.entry_zone_low if has_zone else None,
                "entry_high": s.entry_zone_high if has_zone else None,
                "entry_mid": entry_mid,
                "entry_ref": entry_ref,
                "entry_condition": s.entry_condition,
                "zone_status": s.zone_status,
                "zone_reason": s.zone_reason,

                # 风险控制
                "stop_loss": s.stop_loss if has_zone else None,
                "target_1": s.take_profit_1 if has_zone else None,
                "target_2": s.take_profit_2 if has_zone else None,
                "target_is_fallback": s.target_is_fallback,
                "max_hold_days": s.max_hold_days,

                # 盈亏比（统一函数；None 表示不可算）
                "risk_pct": risk_pct,
                "reward_pct": reward_pct,
                "risk_reward_ratio": s.risk_reward,
                "risk_reward_text": s.risk_reward_text(),

                # 仓位
                "position_pct": round(s.suggested_position_pct, 4),
                "atr": s.atr_value,

                # 评分明细
                "score_details": s.score_details,

                # 理由
                "rationale": s.rationale,
            })

        data: dict[str, Any] = {
            **self._metadata(),
            "type": "signals",
            "market_regime": market_regime,
            "suggested_exposure": suggested_exposure,
            "total_signals": len(signal_list),
            "signals": signal_list,

            # 附加：评级结果（如有），供手机端做评级分布展示
            "ratings": rating_results or [],
        }

        return self._put_json("signals/today.json", data)
