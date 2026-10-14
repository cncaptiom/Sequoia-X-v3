"""验证：诊断脚本「用快照 code 当股票池」是否会改变评级结果。

背景：`run_d5_weights_ab.py` 早先的版本用 `rating_result_*.json` 里的 code 当股票池，
但那是 `scan_market` 【过滤后】的结果（4993 只），而生产的输入池是
`get_local_symbols()` 去 ST 后的 5039 只。截面标准化（winsorize + Z-score）的样本不同，
所有股票的 Z 分都会变，于是 Top15 对不上——这不是生产不可复现。

本脚本用生产口径（正确的池）重扫一次，看能否复现生产快照的 Top15。
只读：ic_tracker=None（不写库），不推飞书，不传 COS。

用法: python check_pool_parity.py
"""

from __future__ import annotations

import glob
import json
import os
import sys
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RATING_ORDER = ["strong_buy", "buy", "watch", "attention", "avoid"]


def topn(recs, get) -> list[str]:
    rank = {k: i for i, k in enumerate(RATING_ORDER)}
    return [r["code"] for r in sorted(
        recs, key=lambda r: (rank.get(get(r, "rating"), 9), -get(r, "total_score")))[:15]]


def main() -> None:
    from sequoia_x.core.config import get_settings
    from sequoia_x.data.engine import DataEngine
    from sequoia_x.rating.engine import RatingConfig, RatingEngine
    from sequoia_x.regime import RegimeDetector
    from sequoia_x.utils.name_resolver import get_name_resolver

    path = sorted(glob.glob(os.path.join("output", "rating_result_*.json")))[-1]
    with open(path, encoding="utf-8") as fh:
        snap = json.load(fh)
    snap_top = topn(snap, lambda r, k: r.get(k))

    e = DataEngine(get_settings())
    local = e.get_local_symbols()
    names = get_name_resolver().get_names(local)
    pool = [s for s in local if "ST" not in names.get(s, s).upper()]
    print(f"生产输入池: get_local_symbols {len(local)} − ST = {len(pool)}")
    print(f"快照(过滤后): {len(snap)}")

    regime = RegimeDetector(e).detect("000905")
    cfg = RatingConfig()
    cfg.apply_regime(regime.label)
    print(f"市场状态: {regime.label}")

    print("用【生产口径的池】重扫...")
    eng = RatingEngine(config=cfg, data_engine=e)
    res = eng.scan_market(
        symbols=pool,
        data_loader=lambda s: e.get_ohlcv_rating_safe(s),
        top_n=99999, min_bars=60, ic_tracker=None,
    )
    print(f"  完成 {len(res)} 只")

    out = [{"code": r.symbol, "rating": str(r.rating), "total_score": r.total_score}
           for r in res]
    scanned_top = topn(out, lambda r, k: r.get(k))
    print(f"\n快照 Top15    : {' '.join(snap_top)}")
    print(f"重扫 Top15    : {' '.join(scanned_top)}")
    print(f"重叠 {len(set(snap_top) & set(scanned_top))}/15")

    s1, s2 = {r["code"] for r in snap}, {r["code"] for r in out}
    print(f"\n结果集相同? {s1 == s2} | 仅在快照 {len(s1 - s2)} | 仅在重扫 {len(s2 - s1)}")

    d1 = Counter(r["rating"] for r in snap)
    d2 = Counter(r["rating"] for r in out)
    print(f"\n{'评级':<14}{'快照':>8}{'重扫':>8}{'差':>8}")
    for k in RATING_ORDER:
        print(f"  {k:<12}{d1.get(k, 0):>8}{d2.get(k, 0):>8}{d2.get(k, 0) - d1.get(k, 0):>+8}")


if __name__ == "__main__":
    main()
