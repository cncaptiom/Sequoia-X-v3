"""基金筛选器：全市场公募基金多维筛选。

数据源：akshare 东方财富基金排行
"""

from dataclasses import dataclass
from typing import Optional

from fund_research.core.logger import get_logger

logger = get_logger(__name__)


class FundScreener:
    """基金筛选器。"""

    def screen(self,
               min_return_1y: float = 0.0,
               max_fee: float = 1.5,
               min_return_3y: float = 0.0,
               top_n: int = 30,
               fund_type: str = "全部",
               ) -> list[dict]:
        """全市场基金筛选（按条件过滤+近1年收益排序）。"""
        logger.info("拉取基金排行数据...")
        raw = self._fetch_rankings(fund_type)
        if not raw:
            return []

        filtered = []
        for f in raw:
            if f.get("return_1y", -99) < min_return_1y:
                continue
            if f.get("return_3y", -99) < min_return_3y:
                continue
            fee = f.get("fee", 99)
            if isinstance(fee, (int, float)) and fee > max_fee:
                continue
            filtered.append(f)

        filtered.sort(key=lambda x: x.get("return_1y", 0), reverse=True)
        logger.info(f"筛选结果: {len(filtered)}/{len(raw)} 只通过")
        return filtered[:top_n]

    def screen_satellite(self, top_n: int = 3) -> list[dict]:
        """卫星配置筛选：寻找有动量但不过热、有持续性的基金。

        原则：
          1. 已有验证的长期业绩（近3年>50%）
          2. 近期有动量但不过热（近3月 15-80%，避免追高）
          3. 非短期爆发型（避免近3月>80%的极端品种）
          4. 综合评分排序（动量+持续性+稳健性）
        """
        raw = self._fetch_rankings("全部")
        if not raw:
            return []

        scored = []
        for f in raw:
            r1 = f.get("return_1y", 0)
            r3 = f.get("return_3y", 0)
            r3m = f.get("return_3m", 0)
            r1m = f.get("return_1m", 0)
            r6m = f.get("return_6m", 0)

            # 硬性过滤条件
            if r3 < 50:       # 近3年收益<50%，没有长期验证
                continue
            if r1 < 20:       # 近1年收益<20%，动量太弱
                continue
            if r3m > 80:      # 近3月涨>80%，过热不追
                continue
            if r3m < 5:       # 近3月涨<5%，动量消失
                continue
            if r1m < 0:       # 近1月还在跌，不接飞刀
                continue

            # 综合评分
            score = self._satellite_score(f)
            scored.append((score, f))

        scored.sort(key=lambda x: x[0], reverse=True)
        logger.info(f"卫星筛选: {len(scored)} 只通过")

        result = []
        for score, f in scored[:top_n]:
            result.append({
                "name": f.get("name", "?"),
                "code": f.get("code", "?"),
                "return_1y": f.get("return_1y", 0),
                "return_3y": f.get("return_3y", 0),
                "return_3m": f.get("return_3m", 0),
                "return_1m": f.get("return_1m", 0),
                "fee": f.get("fee", "?"),
                "_score": round(score, 2),
            })

        return result

    def _satellite_score(self, f: dict) -> float:
        """卫星基金综合评分。

        维度：
          - 持续性：近3年收益（证明不是短期运气）
          - 近期动量：近3月+近1月（有上涨动力）
          - 稳健性：近1月不能大跌
          - 过热惩罚：近3月越接近80%越扣分
        """
        r3 = f.get("return_3y", 0)
        r1 = f.get("return_1y", 0)
        r3m = f.get("return_3m", 0)
        r1m = f.get("return_1m", 0)

        score = 0.0

        # 长期业绩（权重0.35）
        score += min(r3 / 100, 5) * 0.35

        # 近1年收益（权重0.25，但过热扣分）
        r1_score = min(r1 / 50, 4)
        if r1 > 200:
            r1_score *= 0.5  # 涨太多的打5折
        score += r1_score * 0.25

        # 近期动量（权重0.25）
        score += min(max(r3m - 10, 0) / 20, 3) * 0.25

        # 近1月表现（权重0.15，不能大跌）
        r1m_score = max(min(r1m / 10, 2), -2)
        score += r1m_score * 0.15

        # 过热惩罚
        if r3m > 60:
            penalty = (r3m - 60) / 20
            score -= penalty * 0.2

        return score

    def _fetch_rankings(self, fund_type: str = "全部") -> list[dict]:
        """从天天基金拉取基金排名数据。"""
        import akshare as ak

        try:
            df = ak.fund_open_fund_rank_em()
            if df is None or df.empty:
                return []
        except Exception as e:
            logger.warning(f"akshare 失败: {e}")
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
        print(f"开放式基金 Top {len(funds)}")
        print(f"{'='*85}")
        print(f"{'#':<3} {'名称':<24} {'代码':<8} {'近1年':<9} {'近3年':<9} {'近3月':<9} {'类型':<10}")
        print(f"{'-'*85}")
        for i, f in enumerate(funds, 1):
            name = (f.get("name") or "?")[:16]
            print(f"{i:<3} {name:<24} {f.get('code',''):<8} "
                  f"{f.get('return_1y',0):<+8.1f}% {f.get('return_3y',0):<+8.1f}% "
                  f"{f.get('return_3m',0):<+8.1f}% {str(f.get('fund_type','') or '')[:8]:<10}")

    def print_satellite_result(self, funds: list[dict]):
        """打印卫星配置筛选结果。"""
        if not funds:
            print("无卫星配置候选")
            return
        print(f"\n{'='*85}")
        print(f"卫星配置推荐 Top {len(funds)}（有动量但不过热）")
        print(f"{'='*85}")
        print(f"{'#':<3} {'名称':<24} {'代码':<8} {'近1年':<9} {'近3年':<9} {'近3月':<9} {'近1月':<9} {'评分':<7}")
        print(f"{'-'*85}")
        for i, f in enumerate(funds, 1):
            name = (f.get("name") or "?")[:16]
            print(f"{i:<3} {name:<24} {f.get('code',''):<8} "
                  f"{f.get('return_1y',0):<+8.1f}% {f.get('return_3y',0):<+8.1f}% "
                  f"{f.get('return_3m',0):<+8.1f}% {f.get('return_1m',0):<+8.1f}% "
                  f"{f.get('_score',0):<7.2f}")
