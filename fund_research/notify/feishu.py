"""飞书机器人推送。推送ETF轮动信号，含止损止盈价位。"""

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
                             suggested_exposure: float = 0.0) -> bool:
        """推送ETF轮动信号到飞书（含止损止盈价位）。"""
        if not self.webhook_url:
            logger.info("未配置飞书webhook，跳过推送")
            return False

        title = f"ETF轮动信号 ({signal.date})"

        lines: list[str] = []

        # ── 选中标的详表（含止损止盈） ──
        lines.append(f"**本期入选 Top {len(signal.selected)}**\n")
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

            price_s = f"{price:.3f}" if price else "-"
            stop_s = f"{stop:.3f}" if stop else "-"
            t1_s = f"{t1:.3f}" if t1 else "-"
            t2_s = f"{t2:.3f}" if t2 else "-"
            lines.append(f"| {i} | {name} | {code} | {price_s} | {stop_s} | {t1_s} | {t2_s} | {score:+.1f} |")

        # ── 全市场评分排名（前12） ──
        ranked = sorted(signal.scores.items(), key=lambda x: x[1], reverse=True)
        lines.append(f"\n**全市场评分排名（前12）**\n")
        lines.append(f"| 排名 | 名称 | 代码 | 评分 |")
        lines.append(f"|------|------|------|------|")
        for i, (code, score) in enumerate(ranked[:12], 1):
            info = etf_map.get(code)
            name = info.name if info else code
            lines.append(f"| {i} | {name} | {code} | {score:+.1f} |")

        # ── 操作建议 ──
        lines.append(f"\n**操作建议**\n")
        lines.append(f"- **调仓频率**：每月执行一次")
        lines.append(f"- **买入方式**：所选5只等权分配资金，临近收盘买入")
        lines.append(f"- **止损规则**：单只ETF跌破止损价 → 立即卖出，不等月终")
        lines.append(f"- **止盈规则**：触及止盈1 → 卖一半锁利；触及止盈2 → 全部卖出")
        lines.append(f"- **行业分散**：同行业最多2只，避免过度集中")
        if suggested_exposure > 0:
            lines.append(f"- **建议总仓位**：{suggested_exposure:.0%}")
        lines.append(f"\nℹ️ 止损止盈基于14日ATR计算（止损=现价-2ATR，止盈=现价+2/3ATR）")
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
                             "content": f"Sequoia Fund · {signal.date}  |  跌破止损立即卖出，不必等月终"}
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
