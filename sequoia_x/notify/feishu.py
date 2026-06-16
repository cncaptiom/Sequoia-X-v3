"""飞书通知模块：将选股结果通过 Webhook 推送至飞书群。"""

import json
from datetime import date

import requests

from sequoia_x.core.config import Settings
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)


class FeishuNotifier:
    STRATEGY_LABELS = {
        "maVolume": "均线金叉放量",
        "turtleTrade": "海龟突破",
        "meanReversion": "均值回归",
        "volumePrice": "量价关系",
        "atrChannel": "ATR通道",
        "macdRsi": "MACD+RSI共振",
        "maCross": "MA20/60金叉",
    }

    """飞书 Webhook 推送器。

    根据策略的 webhook_key 路由到对应的飞书机器人。
    若 webhook_key 未在 Settings.strategy_webhooks 中配置，
    则 fallback 到 Settings.feishu_webhook_url。
    """

    def __init__(self, settings: Settings) -> None:
        """
        初始化 FeishuNotifier。

        Args:
            settings: Settings 实例，提供 Webhook URL 配置。
        """
        self.settings = settings

    @staticmethod
    def _to_xueqiu_code(code: str) -> str:
        """将纯数字代码转为雪球格式：6开头→SH，4/8开头→BJ，其余→SZ。"""
        if code.startswith("6"):
            return f"SH{code}"
        elif code.startswith(("4", "8")):
            return f"BJ{code}"
        return f"SZ{code}"

    @staticmethod
    def _get_stock_names(symbols: list[str]) -> dict[str, str]:
        """通过 baostock 批量查询股票名称，返回 {code: name} 映射。

        使用无参 query_stock_basic() 一次拉取全量，避免逐只查询导致4000+次API调用。
        """
        import baostock as bs
        bs.login()
        all_names = {}
        rs = bs.query_stock_basic(code_name="", code="")
        while rs.next():
            row = rs.get_row_data()
            raw_code = row[0]      # "sh.600000" 或 "sz.000001"
            name = row[1]          # 股票名称
            status = row[4]        # "1" = 上市
            stock_type = row[5]    # "1" = 股票
            if status == "1" and stock_type == "1":
                pure_code = raw_code.split(".")[1]
                all_names[pure_code] = name
        bs.logout()
        # 只返回需要的股票名称
        return {c: all_names.get(c, c) for c in symbols}

    def _build_card(self, symbols: list[str], strategy_name: str) -> dict:
        today = date.today().strftime("%Y-%m-%d")
        names = self._get_stock_names(symbols)

        links: list[str] = []
        for code in symbols:
            xq_code = self._to_xueqiu_code(code)
            name = names.get(code, xq_code)
            links.append(f"[{name}](https://xueqiu.com/S/{xq_code})")

        symbol_text = " ".join(links) if links else "（无选股结果）"

        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"📈 Sequoia-X 选股播报 | {strategy_name}",
                    },
                    "template": "blue",
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**日期：** {today}\n**策略：** {strategy_name}\n**选股数量：** {len(symbols)}",
                        },
                    },
                    {"tag": "hr"},
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**选股列表：**\n{symbol_text}",
                        },
                    },
                ],
            },
        }

    def send(
        self,
        symbols: list[str],
        strategy_name: str,
        webhook_key: str = "default",
    ) -> None:
        """
        将选股结果格式化为飞书卡片消息并 POST 至对应 Webhook。

        根据 webhook_key 从 Settings 中查找专属 URL；
        若未配置，则 fallback 到 feishu_webhook_url。

        Args:
            symbols: 选股结果代码列表。
            strategy_name: 策略名称，用于卡片标题。
            webhook_key: 策略标识，用于路由到对应飞书机器人。

        Raises:
            不抛出异常，HTTP 失败时记录 ERROR 日志。
        """
        url = self.settings.get_webhook_url(webhook_key)
        payload = self._build_card(symbols, strategy_name)

        try:
            resp = requests.post(
                url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            # 解析飞书真正的返回体
            resp_json = resp.json()

            # 飞书真正的成功标志是内部的 code == 0
            if resp.status_code != 200 or resp_json.get("code") != 0:
                logger.error(
                    f"飞书推送失败 [{webhook_key}] "
                    f"HTTP状态={resp.status_code} 飞书响应={resp.text}"
                )
            else:
                logger.info(f"飞书推送成功 [{webhook_key}]，共 {len(symbols)} 只股票")

        except requests.RequestException as exc:
            logger.error(f"飞书推送请求异常 [{webhook_key}]：{exc}")

    def send_detailed_analysis(
        self,
        top_stocks: list[dict],
        analysis_results: dict[str, dict],
        stock_names: dict[str, str] | None = None,
        summary: str | None = None,
    ) -> None:
        """将精选股票的详细技术分析推送至飞书。

        Args:
            top_stocks: scorer 返回的 top N 股票列表。
            analysis_results: {symbol: analysis_dict}。
            stock_names: {symbol: name} 映射，为 None 时会自动查询。
            summary: 可选概述文本。
        """
        url = self.settings.get_webhook_url("ma_volume")
        today = date.today().strftime("%Y-%m-%d")

        elements: list[dict] = []

        # ── 概览 ──
        overview = (
            f"**精选数量：** {len(top_stocks)} 只\n"
            f"**日期：** {today}\n"
        )
        if summary:
            overview += f"\n{summary}"
        elements.append(
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": overview},
            }
        )

        # 批量查询名称
        if stock_names is None:
            symbols = [item["symbol"] for item in top_stocks]
            stock_names = self._get_stock_names(symbols)

        for i, item in enumerate(top_stocks, 1):
            symbol = item["symbol"]
            analysis = analysis_results.get(symbol, {})

            xq_code = self._to_xueqiu_code(symbol)
            name = stock_names.get(symbol, symbol)

            title_line = (
                f"**{i}. {name} ({symbol})** "
                f"⭐ 评分: {item['score']}/20 | "
                f"涨幅: {item['pct_chg']:+.2f}% | "
                f"价格: {item['close']}"
            )

            if not analysis:
                elements.append(
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": f"{title_line}\n（技术分析数据不足）"},
                    }
                )
                elements.append({"tag": "hr"})
                continue

            trend_emoji = "📈" if "上升" in analysis["trend"] else "📉" if "下降" in analysis["trend"] else "➡️"
            support_text = " | ".join(
                f"{k} {v}" for k, v in analysis["supports"].items()
            )
            resistance_text = " | ".join(
                f"{k} {v}" for k, v in analysis["resistances"].items()
            )
            strategy = analysis["strategy"]
            buy_zone = f"{strategy['buy_zone'][0]} ~ {strategy['buy_zone'][1]}"
            targets = " → ".join(str(t) for t in strategy["targets"])

            detail = (
                f"{title_line}\n"
                f"{trend_emoji} **趋势：** {analysis['trend']} | "
                f"**量比：** {analysis['vol_ratio']}\n"
                f"**均线：** MA5 {analysis['ma5']} | "
                f"MA10 {analysis['ma10']} | "
                f"MA20 {analysis['ma20']} | "
                f"MA60 {analysis['ma60'] or '-'}\n"
                f"**🛡️ 支撑位：** {support_text}\n"
                f"**🔴 压力位：** {resistance_text}\n"
                f"**📥 买入区间：** {buy_zone}\n"
                f"**🎯 目标价位：** {targets}\n"
                f"**🛑 止损位：** {strategy['stop_loss']}\n"
                f"**💡 建议：** {strategy['suggestion']}\n"
                f"**📊 仓位：** {strategy['position']}\n"
                f"[查看雪球](https://xueqiu.com/S/{xq_code})"
            )

            elements.append(
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": detail},
                }
            )

            if i < len(top_stocks):
                elements.append({"tag": "hr"})

        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"📊 Sequoia-X 精选分析 | {today}",
                    },
                    "template": "blue",
                },
                "elements": elements,
            },
        }

        try:
            resp = requests.post(
                url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            resp_json = resp.json()
            if resp.status_code != 200 or resp_json.get("code") != 0:
                logger.error(
                    f"详细分析推送失败 HTTP状态={resp.status_code} 飞书响应={resp.text}"
                )
            else:
                logger.info(f"详细分析推送成功，共 {len(top_stocks)} 只股票")

        except requests.RequestException as exc:
            logger.error(f"详细分析推送请求异常：{exc}")

    def send_rating_results(
        self,
        results: list,
        total_scanned: int = 0,
        summary: str | None = None,
        webhook_key: str = "rating",
    ) -> None:
        """将评级结果推送至飞书。

        Args:
            results: RatingEngine.scan_market() 返回的 AnalysisResult 列表。
            total_scanned: 本次扫描的股票总数。
            summary: 可选概述文本。
            webhook_key: 飞书机器人标识，默认 'rating'。
        """
        url = self.settings.get_webhook_url(webhook_key)
        today = date.today().strftime("%Y-%m-%d")

        # 按评级分组
        groups: dict[str, list] = {"strong_buy": [], "buy": [], "watch": [], "attention": [], "avoid": []}
        for r in results:
            g = groups.setdefault(r.rating, [])
            g.append(r)

        elements: list[dict] = []

        # ── 概览 ──
        strong_buy_count = len(groups.get("strong_buy", []))
        buy_count = len(groups.get("buy", []))
        watch_count = len(groups.get("watch", []))
        attention_count = len(groups.get("attention", []))
        avoid_count = len(groups.get("avoid", []))

        overview = (
            f"**扫描日期：** {today}\n"
            f"**扫描范围：** {total_scanned} 只\n"
            f"**强烈买入：** {strong_buy_count} | **买入：** {buy_count} | "
            f"**观望：** {watch_count} | **关注：** {attention_count} | **回避：** {avoid_count}\n"
        )
        if summary:
            overview += f"\n{summary}"

        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": overview},
        })
        elements.append({"tag": "hr"})

        # ── 强烈买入 / 买入 / 观望 ──
        for rating_key, label, color in [
            ("strong_buy", "🔥 强烈买入", "green"),
            ("buy", "⭐ 买入", "blue"),
            ("watch", "👀 观望", "orange"),
        ]:
            items = groups.get(rating_key, [])
            if not items:
                continue
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**{label}（{len(items)} 只）**",
                },
            })
            for r in items[:10]:  # 最多展示 10 只
                scores_str = " | ".join(
                    f"{self.STRATEGY_LABELS.get(k, k)}: {v}" for k, v in r.strategy_scores.items()
                )
                cat_str = " | ".join(
                    f"{k}: {v}" for k, v in r.category_scores.items()
                )
                content = (
                    f"**{r.name or r.symbol} ({r.symbol})**  "
                    f"总分: {r.total_score}  |  盈亏比: {r.risk_reward_ratio}:1\n"
                    f"现价: {r.current_price}  |  止损: {r.stop_loss_price or '-'}  |  "
                    f"止盈: {r.take_profit_price or '-'}\n"
                    f"策略得分: {scores_str}\n"
                    f"三类评分: {cat_str}\n"
                    f"趋势: {r.trend_description}"
                )
                if r.buy_range:
                    content += f"\n买入区间: {r.buy_range}"
                elements.append({
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": content},
                })
            elements.append({"tag": "hr"})

        # ── 关注（紧凑格式） ──
        attention_items = groups.get("attention", [])[:20]
        if attention_items:
            lines = [f"**📌 关注（{len(groups.get('attention', []))} 只，展示前 20）**"]
            for r in attention_items:
                scores_summary = " ".join(
                    f"{k}={v}" for k, v in r.strategy_scores.items()
                )
                lines.append(
                    f"**{r.name or r.symbol} ({r.symbol})**  "
                    f"总分 {r.total_score}  RR {r.risk_reward_ratio}:1  |  {scores_summary}"
                )
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": "\n".join(lines)},
            })

        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"📊 Sequoia-X A股评级报告 | {today}",
                    },
                    "template": "blue",
                },
                "elements": elements,
            },
        }

        try:
            resp = requests.post(
                url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            resp_json = resp.json()
            if resp.status_code != 200 or resp_json.get("code") != 0:
                logger.error(
                    f"评级推送失败 HTTP状态={resp.status_code} 飞书响应={resp.text}"
                )
            else:
                logger.info(f"评级推送成功，共 {len(results)} 只")
        except requests.RequestException as exc:
            logger.error(f"评级推送请求异常：{exc}")

    def send_regime_alert(self, regime_result, webhook_key: str = "regime") -> None:
        """推送市场状态检测结果到飞书。"""
        url = self.settings.get_webhook_url(webhook_key)
        today = date.today().strftime("%Y-%m-%d")
        regime_emoji = {
            "strong_bull": "\U0001f7e2",
            "weak_bull": "\U0001f7e1",
            "range": "\u26aa",
            "weak_bear": "\U0001f7e0",
            "strong_bear": "\U0001f534",
        }
        emoji = regime_emoji.get(
            regime_result.regime.value if hasattr(regime_result.regime, 'value') else str(regime_result.regime),
            "\u26aa",
        )
        if regime_result.suggested_exposure >= 0.7:
            risk_tag = "\U0001f7e2 低风险"
        elif regime_result.suggested_exposure >= 0.4:
            risk_tag = "\U0001f7e1 中风险"
        else:
            risk_tag = "\U0001f534 高风险"
        bias = regime_result.strategy_bias
        bias_lines = [
            f"趋势: {bias.get('trend', 0):.0%}",
            f"动量: {bias.get('momentum', 0):.0%}",
            f"反转: {bias.get('reversal', 0):.0%}",
        ]
        content = (
            f"**日期：** {today}\n"
            f"**市场状态：** {emoji} {regime_result.label}\n"
            f"**风险等级：** {risk_tag}\n"
            f"**建议总仓位：** {regime_result.suggested_exposure:.0%}\n"
            f"**趋势强度：** {regime_result.trend_strength:+.2f}\n"
            f"**波动状态：** {regime_result.volatility_regime} (P{regime_result.volatility_pct:.0%})\n"
            f"**策略偏向：** {' / '.join(bias_lines)}\n"
            f"**详情：** {regime_result.details}"
        )
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"{emoji} Sequoia-X 市场状态 | {today}",
                    },
                    "template": "blue",
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": content}},
                ],
            },
        }
        try:
            resp = requests.post(
                url, data=json.dumps(payload),
                headers={"Content-Type": "application/json"}, timeout=10,
            )
            resp_json = resp.json()
            if resp.status_code != 200 or resp_json.get("code") != 0:
                logger.error(f"市场状态推送失败 HTTP={resp.status_code}")
            else:
                logger.info(f"市场状态推送成功 [{webhook_key}]: {regime_result.label}")
        except requests.RequestException as exc:
            logger.error(f"市场状态推送异常 [{webhook_key}]：{exc}")

    def send_factor_results(
        self, ranked_results: list, total_scanned: int = 0,
        top_n: int = 20, webhook_key: str = "factors",
    ) -> None:
        """将多因子选股结果推送至飞书。"""
        url = self.settings.get_webhook_url(webhook_key)
        today = date.today().strftime("%Y-%m-%d")
        display = ranked_results[:top_n]
        if not display:
            logger.info("因子选股无结果，跳过推送")
            return
        symbols = [r.symbol for r in display]
        names = self._get_stock_names(symbols)
        elements: list[dict] = []
        overview = (
            f"**日期：** {today}\n"
            f"**扫描范围：** {total_scanned} 只\n"
            f"**因子体系：** 价值 / 质量 / 动量 / 低波动 / 规模\n"
            f"**展示数量：** Top {len(display)}"
        )
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": overview}})
        elements.append({"tag": "hr"})
        for i, r in enumerate(display, 1):
            name = names.get(r.symbol, r.symbol)
            xq_code = self._to_xueqiu_code(r.symbol)
            fp = r.factor_percentiles
            factor_lines = [
                f"价值:{fp.get('value',0):.3f} 质量:{fp.get('quality',0):.3f}",
                f"动量:{fp.get('momentum',0):.3f} 低波:{fp.get('low_vol',0):.3f} 规模:{fp.get('size',0):.3f}",
            ]
            fund_parts = []
            if r.pe is not None: fund_parts.append(f"PE:{r.pe:.1f}")
            if r.pb is not None: fund_parts.append(f"PB:{r.pb:.1f}")
            if r.roe is not None: fund_parts.append(f"ROE:{r.roe:.1f}%")
            content = (
                f"**{i}. {name} ({r.symbol})** | 总分:{r.composite_score:.4f}\n"
                f"{' / '.join(factor_lines)}\n"
            )
            if fund_parts:
                content += f"{' | '.join(fund_parts)}\n"
            if r.momentum_12m1 is not None:
                content += f"12-1月动量:{r.momentum_12m1:+.1%}  "
            content += f"[雪球](https://xueqiu.com/S/{xq_code})"
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})
            if i < len(display):
                elements.append({"tag": "hr"})
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"\U0001f4ca Sequoia-X 多因子选股 | {today}",
                    },
                    "template": "blue",
                },
                "elements": elements,
            },
        }
        try:
            resp = requests.post(
                url, data=json.dumps(payload),
                headers={"Content-Type": "application/json"}, timeout=10,
            )
            resp_json = resp.json()
            if resp.status_code != 200 or resp_json.get("code") != 0:
                logger.error(f"因子结果推送失败 HTTP={resp.status_code}")
            else:
                logger.info(f"因子结果推送成功 [{webhook_key}]，共{len(display)}只")
        except requests.RequestException as exc:
            logger.error(f"因子结果推送异常 [{webhook_key}]：{exc}")
    def _get_single_name(self, code: str) -> str:
        """查询单只股票名称（带缓存）。"""
        if not hasattr(self, "_name_cache"):
            self._name_cache: dict[str, str] = {}
        if code in self._name_cache:
            return self._name_cache[code]

        import baostock as bs
        bs.login()
        prefix = "sh" if code.startswith(("6", "9")) else "sz"
        rs = bs.query_stock_basic(code=f"{prefix}.{code}")
        name = code
        while rs.next():
            row = rs.get_row_data()
            name = row[1]
        bs.logout()
        self._name_cache[code] = name
        return name
