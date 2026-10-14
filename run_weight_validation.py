"""评分权重验证：用 ic_history 的真实策略 IC，对比【生产权重】与【静态默认权重】的组合预测力。

生产权重只由市场状态（`apply_regime`）决定——2026-10-14 起不再叠加 IR 加权与 IC 衰减
（见 CHANGELOG 第十节、`rating/engine.py::apply_ir_weights`）。
组合 IC ≈ Σ(策略权重 × 策略IC)（total_score 为策略分加权和的 IC 线性近似）。
输出 output/weight_validation.json，打印各市场状态下最终权重与组合 IC。
"""

import json
import os

os.environ.setdefault("DB_PATH", "data/sequoia_v2.db")

from sequoia_x.rating.engine import RatingConfig

# 1) 从 ic_history 提取各策略 IC（lookforward=20，按日期去重）
d = json.load(open("output/ic_history.json", encoding="utf-8"))
hist = d.get("history", {})
ic20: dict[str, float] = {}
for strat, records in hist.items():
    seen = {}
    for r in records:
        seen[(r["date"], r["lookforward"])] = r
    vals = [r["ic"] for k, r in seen.items() if k[1] == 20 and r.get("ic") is not None]
    if vals:
        ic20[strat] = sum(vals) / len(vals)

print("策略 IC20:", {k: round(v, 4) for k, v in ic20.items()})

STRAT_ATTR = {
    "maVolume": "ma_volume", "turtleTrade": "turtle_trade", "meanReversion": "mean_reversion",
    "volumePrice": "volume_price", "atrChannel": "atr_channel", "macdRsi": "macd_rsi",
    "maCross": "ma_cross", "kdj": "kdj", "fundamental": "fundamental",
}

def combo_ic(cfg: RatingConfig) -> float:
    """计算当前权重的组合 IC。"""
    total = 0.0
    for ic_name, attr in STRAT_ATTR.items():
        if not getattr(cfg, attr + "_enabled", False):
            continue
        w = float(getattr(cfg, cfg.WEIGHT_ATTRS[attr]))
        ic = ic20.get(ic_name)
        if ic is not None:
            total += w * ic
    return total

def weights_str(cfg: RatingConfig) -> str:
    return ", ".join(f"{k}={getattr(cfg, cfg.WEIGHT_ATTRS[attr]):.3f}" for k, attr in STRAT_ATTR.items()
                     if getattr(cfg, attr + "_enabled", False))

results = {}
for regime in ["强牛", "弱牛", "震荡", "弱熊", "强熊"]:
    cfg = RatingConfig()
    cfg.apply_regime(regime)          # 生产唯一的权重来源
    old = RatingConfig()              # 静态默认权重（见 RatingConfig 的字段默认值）
    results[regime] = {
        "new_weights": weights_str(cfg),
        "combo_ic_new": round(combo_ic(cfg), 4),
        "combo_ic_old": round(combo_ic(old), 4),
    }
    print(f"\n[{regime}]")
    print(f"  生产权重: {results[regime]['new_weights']}")
    print(f"  组合IC: 生产={results[regime]['combo_ic_new']:+.4f}  默认={results[regime]['combo_ic_old']:+.4f}")

# 场景：启用 volumePrice（IC 正）对比
print("\n===== 场景对比：是否启用 volumePrice =====")
for enable_vp in (False, True):
    cfg = RatingConfig()
    if enable_vp:
        object.__setattr__(cfg, "volume_price_enabled", True)
        object.__setattr__(cfg, "weight_volume_price", 0.10)
    cfg.apply_regime("震荡")
    print(f"  volumePrice={'启用' if enable_vp else '禁用'}: 组合IC(震荡)={combo_ic(cfg):+.4f}")

out = {"ic20": {k: round(v, 4) for k, v in ic20.items()}, "regime": results}
json.dump(out, open("output/weight_validation.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n→ output/weight_validation.json")
