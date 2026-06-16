
"""多策略融合评分模块：策略权重+类型差异度计算综合置信度。"""

from dataclasses import dataclass, field

STRATEGY_CATEGORIES = {
    "trend": ["TurtleTradeStrategy", "MaVolumeStrategy", "RpsBreakoutStrategy"],
    "reversal": ["HighTightFlagStrategy", "LimitUpShakeoutStrategy", "UptrendLimitDownStrategy"],
}

STRATEGY_WEIGHTS = {
    "TurtleTradeStrategy": 1.0,
    "MaVolumeStrategy": 1.0,
    "RpsBreakoutStrategy": 0.9,
    "HighTightFlagStrategy": 0.8,
    "LimitUpShakeoutStrategy": 0.8,
    "UptrendLimitDownStrategy": 0.7,
}


@dataclass
class ConsensusScore:
    symbol: str = ""
    name: str = ""
    strategy_count: int = 0
    strategies: list[str] = field(default_factory=list)
    base_score: float = 0.0
    diversity_bonus: float = 0.0
    weight_score: float = 0.0
    final_score: float = 0.0
    categories_hit: list[str] = field(default_factory=list)


class ConsensusEngine:
    """多策略融合评分引擎。"""

    def score(self, strategy_map: dict[str, list[str]]) -> list[ConsensusScore]:
        results = []
        for symbol, strats in strategy_map.items():
            if not strats:
                continue
            unique_strats = list(set(strats))
            cs = ConsensusScore(symbol=symbol, strategies=unique_strats, strategy_count=len(unique_strats))

            # 基础分
            cs.base_score = min(len(unique_strats), 5) * 15

            # 加权分
            cs.weight_score = sum(STRATEGY_WEIGHTS.get(s, 0.5) for s in unique_strats) * 10

            # 类型差异
            categories_hit = set()
            for s in unique_strats:
                for cat, members in STRATEGY_CATEGORIES.items():
                    if s in members:
                        categories_hit.add(cat)
            cs.categories_hit = list(categories_hit)
            if len(categories_hit) >= 2:
                cs.diversity_bonus = 15
            elif len(categories_hit) == 1:
                cs.diversity_bonus = 5

            # 反转类加分
            if any(s in STRATEGY_CATEGORIES["reversal"] for s in unique_strats):
                cs.diversity_bonus += 5

            cs.final_score = min(cs.base_score + cs.weight_score + cs.diversity_bonus, 100.0)
            results.append(cs)

        results.sort(key=lambda x: x.final_score, reverse=True)
        return results

    @staticmethod
    def get_top_n(scores: list[ConsensusScore], n: int = 20, min_strategies: int = 1) -> list[ConsensusScore]:
        filtered = [s for s in scores if s.strategy_count >= min_strategies]
        return filtered[:n]
