"""飞书机器人推送。复用 Sequoia-X 的 webhook 格式，推送ETF轮动信号。"""

import json
from datetime import datetime
from typing import Optional

import requests

from fund_research.core.logger import get_logger

logger = get_logger(__name__)

# 默认webhook（可通过环境变量 FEISHU_ETF_WEBHOOK 覆盖）
DEFAULT_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/"


class FeishuNotifier:
    """飞书机器人推送。"""

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url
        self._session = requests.Session()

    def send_rotation_signal(self, signal, etf_map: dict,
                             regime_label: str = "",
                             suggested_exposure: float = 0.0) -> bool:
        """推送ETF轮动信号到飞书。"""
        if not self.webhook_url:
            logger.info("未配置飞书webhook，跳过推送")
            return False

        title = f"ETF轮动信号 ({signal.date})"
        if regime_label:
            title += f" · {regime_label}"

        # 选中标的表格
        lines = [f"**本期选中 Top {len(signal.selected)}**\n"]
        lines.append(f"| # | 名称 | 代码 | 评分 |")
        lines.append(f"|---|------|------|------|")

        etf_info = ""
        for i, code in enumerate(signal.selected, 1):
            info = etf_map.get(code)
            name = info.name if info else code
            score = signal.scores.get(code, 0)
            lines.append(f"| {i} | {name} | {code} | {score:+.1f} |")
            cat = info.category if info else ""
            etf_info += f"\n{i}. **{name}** ({code}) — {cat}，评分 {score:+.1f}"

        # 评分排名
        ranked = sorted(signal.scores.items(), key=lambda x: x[1], reverse=True)
        lines.append(f"\n**全市场评分排名**\n")
        lines.append(f"| 排名 | 名称 | 代码 | 评分 |")
        lines.append(f"|------|------|------|------|")
        for i, (code, score) in enumerate(ranked[:12], 1):
            info = etf_map.get(code)
            name = info.name if info else code
            lines.append(f"| {i} | {name} | {code} | {score:+.1f} |")

        # 操作建议
        lines.append(f"\n**操作建议**")
        if suggested_exposure > 0:
            lines.append(f"- 建议仓位: {suggested_exposure:.0%}")
        lines.append(f"- 调仓频率: 月度")
        lines.append(f"- 调仓方式: 收盘价等权买入")
        lines.append(f"- 行业分散: 同行业最多2只")
        lines.append(f"\n⚠️ 以上为量化模型信号，仅供参考，不构成投资建议。")

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
                            {
                                "tag": "plain_text",
                                "content": f"Sequoia Fund · {signal.date}"
                            }
                        ],
                    },
                ],
            },
        }

        try:
            resp = self._session.post(
                self.webhook_url,
                json=payload,
                timeout=10,
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
