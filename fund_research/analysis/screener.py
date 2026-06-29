"""基金筛选器：全市场公募基金多维筛选。

数据源：akshare 东方财富基金排行
"""

from datetime import date
from typing import Optional

import pandas as pd

from fund_research.core.logger import get_logger

logger = get_logger(__name__)


class FundScreener:
    """基金筛选器。"""

    def screen(self,
               min_return_1y: float = 0.0,     # 近1年最低收益(%)
               max_fee: float = 1.5,             # 最高申购费(%)
               min_return_3y: float = 0.0,       # 近3年最低收益(%)
               top_n: int = 30,
               fund_type: str = "全部",
               ) -> list[dict]:
        """全市场基金筛选。"""
        logger.info("拉取基金排行数据...")

        raw = self._fetch_rankings(fund_type)
        if not raw:
            return []

        # 应用筛选
        filtered = []
        for f in raw:
            ret_1y = f.get("return_1y", -99)
            ret_3y = f.get("return_3y", -99)
            fee = f.get("fee", 99)

            if isinstance(fee, str):
                try:
                    fee = float(fee.replace("%", ""))
                except ValueError:
                    fee = 99

            if ret_1y < min_return_1y:
                continue
            if ret_3y < min_return_3y:
                continue
            if isinstance(fee, (int, float)) and fee > max_fee:
                continue

            filtered.append(f)

        # 按1年收益排序
        filtered.sort(key=lambda x: x.get("return_1y", 0), reverse=True)

        logger.info(f"筛选结果: {len(filtered)}/{len(raw)} 只通过")
        return filtered[:top_n]

    def _fetch_rankings(self, fund_type: str = "全部") -> list[dict]:
        """从天天基金拉取基金排名数据。"""
        import akshare as ak

        try:
            df = ak.fund_open_fund_rank_em()
            if df is None or df.empty:
                logger.warning("数据为空")
                return []
        except Exception as e:
            logger.warning(f"akshare 获取失败: {e}")
            return []

        col_map = {
            "基金代码": "code",
            "基金简称": "name",
            "基金类型": "fund_type",
            "近1月": "return_1m",
            "近3月": "return_3m",
            "近6月": "return_6m",
            "近1年": "return_1y",
            "近2年": "return_2y",
            "近3年": "return_3y",
            "今年来": "return_ytd",
            "成立来": "return_si",
            "手续费": "fee",
            "单位净值": "nav",
            "累计净值": "acc_nav",
            "日增长率": "daily_chg",
        }

        results = []
        for _, row in df.iterrows():
            item = {}
            for cn, en in col_map.items():
                val = row.get(cn)
                if val is not None:
                    item[en] = val
            # 收益字段转float
            for k in ["return_1m", "return_3m", "return_6m", "return_1y",
                      "return_2y", "return_3y", "return_ytd", "return_si"]:
                if k in item:
                    item[k] = self._to_float(item[k])
            results.append(item)

        return results

    @staticmethod
    def _to_float(v) -> float:
        try:
            return float(str(v).replace("%", "").replace(",", "").strip())
        except (ValueError, AttributeError):
            return 0.0

    def print_result(self, funds: list[dict]):
        """打印筛选结果。"""
        if not funds:
            print("无筛选结果")
            return

        print(f"\n{'='*85}")
        print(f"开放式基金筛选 Top {len(funds)}")
        print(f"{'='*85}")
        print(f"{'#':<3} {'名称':<24} {'代码':<8} {'近1年':<9} {'近3年':<9} {'近3月':<9} {'类型':<10} {'费率':<6}")
        print(f"{'-'*85}")

        total = len(funds)
        for i, f in enumerate(funds, 1):
            name = (f.get("name") or "?")[:16]
            code = f.get("code", "?")
            r1 = f.get("return_1y", 0)
            r3 = f.get("return_3y", 0)
            r1m = f.get("return_3m", 0)
            ft = (f.get("fund_type") or "?")[:8]
            fee = f.get("fee", "?")
            try:
                fee_s = f"{float(fee):.2f}%" if fee != "?" else "?"
            except (ValueError, TypeError):
                fee_s = str(fee)[:5] if fee != "?" else "?"

            print(f"{i:<3} {name:<24} {code:<8} "
                  f"{r1:<+8.1f}% {r3:<+8.1f}% {r1m:<+8.1f}% {ft:<10} {fee_s:<6}")
