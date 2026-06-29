"""飞书机器人推送。推送ETF轮动信号（核心+卫星配置），含止损止盈价位。"""

from typing import Optional

import requests

from fund_research.core.logger import get_logger

logger = get_logger(__name__)


class FeishuNotifier:
    """飞书机器人推送。"""

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url
        self._session = requests.Session()

    def send_rotation_signal(self, signal, etf_map: dict,
                             regime_label: str = "",
                             suggested_exposure: float = 0.0,
                             satellite_funds: Optional[list[dict]] = None) -> bool:
        """推送ETF轮动信号到飞书（核心+卫星配置）。"""
        if not self.webhook_url:
            logger.info("未配置飞书webhook，跳过推送")
            return False

        title = f"ETF轮动信号 ({signal.date})"

        lines: list[str] = []

        # ── 核心配置（80%资金）：ETF轮动 ──
        lines.append(f"**核心配置（80%资金）— ETF轮动 Top {len(signal.selected)}**\n")
        lines.append(f"| # | 名称 | 代码 | 现价 | 止损 | 止盈1 | 止盈2 | 评分 |")
        lines.append(f"|---|------|------|------|------|------|------|------|")
        for i, code in enumerate(signal.selected, 1):
            info = etf_map.get(code)
            name = info.name if info else code
            score = signal.scores.get(code, 0)
            pos = signal.positions_info.get(code, {})
            price = pos.get("price", 0)
            stop = pos.get("stop_loss", 0)
            t1 = pos.get("target_1", 0)
            t2 = pos.get("target_2", 0)
            lines.append(f"| {i} | {name} | {code} | "
                         f"{f'{price:.3f}' if price else '-'} | "
                         f"{f'{stop:.3f}' if stop else '-'} | "
                         f"{f'{t1:.3f}' if t1 else '-'} | "
                         f"{f'{t2:.3f}' if t2 else '-'} | {score:+.1f} |")

        # ── 卫星配置（20%资金）：高弹性基金 ──
        if satellite_funds:
            lines.append(f"\n**卫星配置（20%资金）— 高弹性基金**\n")
            lines.append(f"| # | 名称 | 代码 | 近1年 | 近3月 |")
            lines.append(f"|---|------|------|-------|-------|")
            for i, f in enumerate(satellite_funds[:3], 1):
                name = (f.get("name") or "?")[:16]
                code = f.get("code", "?")
                r1 = f.get("return_1y", 0)
                r3 = f.get("return_3m", 0)
                lines.append(f"| {i} | {name} | {code} | {r1:+.1f}% | {r3:+.1f}% |")
            lines.append(f"\n> 高风险卫星仓位：严格止损 -15%，翻倍后卖一半锁利")

        # ── 全市场ETF评分排名（前12） ──
        ranked = sorted(signal.scores.items(), key=lambda x: x[1], reverse=True)
        lines.append(f"\n**全市场ETF评分排名（前12）**\n")
        lines.append(f"| 排名 | 名称 | 代码 | 评分 |")
        lines.append(f"|------|------|------|------|")
        for i, (code, score) in enumerate(ranked[:12], 1):
            info = etf_map.get(code)
            name = info.name if info else code
            lines.append(f"| {i} | {name} | {code} | {score:+.1f} |")

        # ── 操作建议 ──
        lines.append(f"\n**操作建议**\n")
        lines.append(f"- **资金分配**：80%核心（上表ETF） + 20%卫星（高弹性基金）")
        lines.append(f"- **调仓频率**：核心月度调仓，卫星季度评估")
        lines.append(f"- **核心止损**：单只ETF跌破止损价 → 立即卖出，不等月终")
        lines.append(f"- **核心止盈**：触及止盈1卖一半，触及止盈2全部卖出")
        lines.append(f"- **卫星止损**：单只基金亏损超15% → 无条件卖出")
        lines.append(f"- **卫星止盈**：翻倍后卖一半锁利，剩余持有到翻2倍或跌破MA60")
        if suggested_exposure > 0:
            lines.append(f"- **建议总仓位**：{suggested_exposure:.0%}")
        lines.append(f"\nℹ️ ETF止损止盈基于14日ATR计算")
        lines.append(f"⚠️ 以上为量化模型信号，仅供参考，不构成投资建议。")

        content = "\n".join(lines)

        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "blue",
                },
                "elements": [
                    {"tag": "markdown", "content": content},
                    {"tag": "hr"},
                    {
                        "tag": "note",
                        "elements": [
                            {"tag": "plain_text",
                             "content": f"Sequoia Fund · {signal.date}  |  核心月频调仓，卫星季度评估"}
                        ],
                    },
                ],
            },
        }

        try:
            resp = self._session.post(
                self.webhook_url, json=payload, timeout=10,
            )
            if resp.status_code == 200:
                logger.info("飞书推送成功")
                return True
            else:
                logger.warning(f"飞书推送失败: {resp.status_code} {resp.text}")
                return False
        except requests.RequestException as e:
            logger.warning(f"飞书推送异常: {e}")
            return False
