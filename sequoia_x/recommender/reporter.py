"""推荐报告生成器：将 TradeSignal 列表转换为飞书卡片/终端文本。"""

from datetime import date
from typing import Optional

from sequoia_x.core.logger import get_logger
from sequoia_x.signal.models import TradeSignal, SignalResult
from sequoia_x.notify.feishu import FeishuNotifier

logger = get_logger(__name__)

# 展示口径：飞书卡片展示的条数。
# 追踪库写入的条数必须与之一致，否则「命中率」统计的分母会和用户看到的名单不符。
DISPLAY_TOP_N = 15

# 盈亏比配色阈值：按 2026-11-04 全市场分布标定
# （P50=1.06 / P75=1.46 / P90=1.93），旧的 2.0/1.0 会让绝大多数卡片变红。
RR_GREEN = 1.5
RR_YELLOW = 1.0


class ReportGenerator:
    """推荐报告生成器，支持终端打印和飞书推送。"""

    def __init__(self, notifier: Optional[FeishuNotifier] = None):
        self.notifier = notifier

    def print_console(self, result: SignalResult) -> None:
        """在终端打印推荐报告。"""
        print()
        print("=" * 78)
        print(f"  Sequoia-X 量化选股推荐报告")
        print(f"  生成时间: {result.generated_at}")
        print(f"  扫描范围: {result.total_scanned} 只股票")
        print(f"  市场状态: {result.market_regime}")
        print(f"  建议总仓位: {result.suggested_total_exposure:.0%}")
        print("=" * 78)

        if not result.signals:
            print("  [无推荐] 当前没有符合条件的交易信号")
            print("=" * 78)
            return

        for i, sig in enumerate(result.signals[:20], 1):
            rr = sig.risk_reward_text()
            print()
            print(f"  ┌─ #{i}  {sig.name} ({sig.symbol}) {sig.rating_stars}")
            print(f"  │  策略: {', '.join(sig.strategies)}")
            if sig.rating_label:
                print(f"  │  评级: {sig.rating_label}")
            print(f"  │  现价: {sig.current_price:.2f}")
            print(f"  │  ─────────────────────────────────────────────")
            if sig.zone_status == "none":
                print(f"  │  本期只观察: {sig.zone_reason}")
            else:
                print(f"  │  买入区间: {sig.entry_zone_low:.2f} ~ {sig.entry_zone_high:.2f}")
                print(f"  │  触发条件: {sig.entry_condition}")
            print(f"  │  目标价1: {sig.take_profit_1:.2f}", end="")
            if sig.target_is_fallback:
                print("（ATR 兜底）", end="")
            if sig.take_profit_2:
                print(f"  目标价2: {sig.take_profit_2:.2f}", end="")
            print()
            print(f"  │  止损价: {sig.stop_loss:.2f}  (盈亏比: {rr})")
            if sig.zone_status != "none":
                print(f"  │  建议仓位: {sig.suggested_position_pct:.1%}（按可买标的均分总仓位）")
            if sig.atr_value:
                print(f"  │  ATR波动: {sig.atr_value:.2f}")
            print(f"  │  形态分: {sig.confidence:.0%}")
            print(f"  │  理由: {sig.rationale}")
            print(f"  └─")

        print()
        print("=" * 78)
        print(f"  共推荐 {len(result.signals)} 只股票")
        print("=" * 78)
        print()

    def push_feishu(self, result: SignalResult, webhook_key: str = "default") -> None:
        """推送推荐报告到飞书。"""
        if not self.notifier:
            logger.warning("未配置飞书通知器，跳过推送")
            return

        top = result.signals[:DISPLAY_TOP_N]
        if not top:
            # 无推荐时仍然推送简版报告
            logger.info("无推荐信号，推送空报告")
            payload = self._build_empty_card(result)
        else:
            payload = self._build_feishu_card(result, top)

        url = self.notifier.settings.get_webhook_url(webhook_key)

        import requests, json
        try:
            resp = requests.post(
                url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            resp_json = resp.json()
            if resp.status_code == 200 and resp_json.get("code") == 0:
                logger.info(f"推荐报告推送成功: {len(top)} 只")
            else:
                logger.error(f"推送失败 HTTP={resp.status_code} body={resp.text[:200]}")
        except Exception as exc:
            logger.error(f"推送异常: {exc}")

    def _build_feishu_card(self, result: SignalResult, signals: list[TradeSignal]) -> dict:
        """构建飞书交互式卡片消息（含名称、支撑压力、操作建议）。"""
        today = date.today().strftime("%Y-%m-%d")
        elements = []

        # 从 generator 获取股票名称
        from sequoia_x.utils.name_resolver import get_name_resolver
        resolver = get_name_resolver()

        # 头部概览
        rating_line = ""
        if result.rating_summary:
            parts = []
            label_map = {"strong_buy": "🔥强烈买入", "buy": "⭐买入", "watch": "👀观望", "attention": "📌关注", "avoid": "回避"}
            for k in ["strong_buy", "buy", "watch", "attention"]:
                v = result.rating_summary.get(k, 0)
                if v > 0:
                    parts.append(f"{label_map.get(k, k)} {v}")
            if parts:
                rating_line = "\n全市场评级分布: " + " | ".join(parts)

        overview = (
            f"**{today}** | 市场状态: {result.market_regime}"
            f" | 建议总仓位: {result.suggested_total_exposure:.0%}"
            f"{rating_line}\n"
            f"扫描 {result.total_scanned} 只 | 推荐 {len(signals)} 只"
        )
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": overview}})
        elements.append({"tag": "hr"})

        for i, sig in enumerate(signals, 1):
            name = resolver.get_name(sig.symbol) if not sig.name else sig.name
            rr = sig.risk_reward_ratio()
            emoji = "⚪" if rr is None else "🟢" if rr >= RR_GREEN else "🟡" if rr >= RR_YELLOW else "🔴"

            # 评级标签：选股依据。--daily（备用路径）不产生评级，留空。
            rating_tag = f"  [{sig.rating_label}]" if sig.rating_label else ""

            # 策略标签。⚠️ 这个字段会被两条流水线写入，名字来自【两套不同的策略体系】：
            #   · --rating（生产）→ rating/strategies.py，键为 camelCase（maVolume…kdj）
            #   · --daily（备用）→ strategy/*.py，键为类名（MaVolumeStrategy…）
            # 两套实现互不相干、名字部分重叠，所以两张表**分开列**，不再合成一个 dict
            # 让人以为是一套。也去掉了 `s.replace("Strategy","")` 兜底——它会把类名
            # 剥成 camelCase 的样子，看起来像另一套体系的策略。
            _RATING_LABELS = {
                "maVolume": "均线放量", "turtleTrade": "海龟突破", "meanReversion": "均值回归",
                "volumePrice": "量价关系", "atrChannel": "ATR通道", "macdRsi": "MACD+RSI",
                "maCross": "MA金叉", "kdj": "KDJ", "fundamental": "基本面", "rating": "综合评级",
            }
            _SELECTION_LABELS = {
                "MaVolumeStrategy": "均线放量", "TurtleTradeStrategy": "海龟突破",
                "HighTightFlagStrategy": "高紧旗", "LimitUpShakeoutStrategy": "涨停洗盘",
                "UptrendLimitDownStrategy": "上涨跌停", "RpsBreakoutStrategy": "RPS突破",
            }
            strat_labels = []
            for s in sig.strategies:
                lbl = _RATING_LABELS.get(s) or _SELECTION_LABELS.get(s) or s
                strat_labels.append(f"#{lbl}")
            strat_tags = " | ".join(strat_labels)

            content = (
                f"{emoji} **{i}. {name} ({sig.symbol})**{rating_tag}\n"
                f"{strat_tags}\n"
                f"━━━━━━━━━━━━━━━━━\n"
            )
            if sig.zone_status == "none":
                content += f"**👀 本期只观察：** {sig.zone_reason}\n"
            else:
                if sig.zone_status == "inside":
                    zone_hint = "现价在支撑区内，可按现价买入"
                else:
                    gap = (sig.current_price / sig.entry_zone_high - 1) * 100 \
                        if sig.entry_zone_high > 0 else 0
                    zone_hint = f"现价高于支撑区约 {gap:.1f}%，可按现价买入（追高）"
                content += (
                    f"**📥 买入区间：** {sig.entry_zone_low:.2f} ~ {sig.entry_zone_high:.2f}"
                    f"（{zone_hint}）\n"
                    f"**🎯 目标价：** {sig.take_profit_1:.2f}"
                )
                if sig.target_is_fallback:
                    content += "（ATR 兜底，无明确阻力）"
                if sig.take_profit_2:
                    content += f" → {sig.take_profit_2:.2f}"
                content += f"\n**🛑 止损价：** {sig.stop_loss:.2f}"
            content += f"\n━━━━━━━━━━━━━━━━━\n"
            if sig.zone_status != "none":
                content += f"盈亏比 {sig.risk_reward_text()} | 仓位 {sig.suggested_position_pct:.0%}"
            else:
                content += "仓位 0%（只观察）"
            content += f" | 形态分 {sig.confidence:.0%}\n"
            if sig.atr_value:
                content += f"ATR波动 {sig.atr_value:.2f} | "
            content += f"现价 {sig.current_price:.2f}\n"

            # 选股理由（增强版）
            reasons = []
            if sig.rationale:
                reasons.append(sig.rationale)
            if sig.rating_label:
                reasons.append(f"评级 {sig.rating_label}")
            reasons.append(f"形态分 {sig.confidence:.0%}：走势质量，不参与选股排序")
            # 市场环境
            if result.market_regime:
                reasons.append(f"当前市场: {result.market_regime}")
            if reasons:
                content += f"\n**💡 分析：** {' | '.join(reasons)}"

            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})
            if i < len(signals):
                elements.append({"tag": "hr"})

        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": f"📈 Sequoia-X 操作建议 | {today}"},
                    "template": "blue",
                },
                "elements": elements,
            },
        }

    def _build_empty_card(self, result: SignalResult) -> dict:
        """构建无推荐时的飞书卡片。"""
        today = date.today().strftime("%Y-%m-%d")
        elements = [
            {"tag": "div", "text": {"tag": "lark_md", "content": (
                f"**{today}** | 市场状态: {result.market_regime}"
                f" | 建议总仓位: {result.suggested_total_exposure:.0%}\n"
                f"扫描 {result.total_scanned} 只 | 暂无符合条件的操作建议"
            )}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": (
                "当前没有满足买入条件的股票，可能原因：\n"
                "1. 市场整体处于调整或下跌趋势\n"
                "2. 各策略未触发买入信号\n"
                "3. 评级扫描范围不足\n\n"
                "建议保持观望，等待市场回暖后再做决策。"
            )}},
        ]
        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": f"📈 Sequoia-X 操作建议 | {today}"},
                    "template": "grey",
                },
                "elements": elements,
            },
        }
