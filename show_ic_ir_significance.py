"""只读：IC 序列的重叠校正标准误，给「噪声阈值 θ」一个真实依据。

背景（docs/code-review-2026-10-13.md 的 D5）：
  `apply_ir_weights`（rating/engine.py:149）用 IR = avg_ic / ic_std 的【符号】决定
  策略权重是否归零。而 `ICTracker.get_summary`（signal/quality.py:384）的
  `entries[-60:]` 把同一天的两条 IC（lookforward=5 与 lookforward=20）【混在一个】
  均值和标准差里。

  要回答「|IR| 多小算噪声」，需要 IR 的标准误。教科书的 SE(IR) = 1/sqrt(n) 假定 IC
  观测相互独立，但 h 日前瞻收益在连续交易日上是【重叠】的：相邻两条 IC 共享
  (h-1)/h 的收益窗口，序列强自相关，真实 SE 远大于 1/sqrt(n)。

本脚本做三件事：
  1. 按 lookforward 拆开每个策略的 IC 序列（点名 get_summary 的混编问题）
  2. 对每条序列算三种 SE(IR)：
       naive   = 1/sqrt(n)                     ← 现在隐含在用的口径
       NW(h-1) = Newey-West，滞后 L = h-1      ← 重叠窗口的理论滞后阶
       NW(auto)= Newey-West，L = floor(4*(n/100)^(2/9))  ← 数据驱动，作对照
  3. 打印 θ = k * SE(IR)，即「|IR| 低于多少应视为噪声」

纯只读：不写文件、不联网、不连库。用法: python show_ic_ir_significance.py [--k 2]
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys

HISTORY = "output/ic_history.json"

# Windows 控制台默认 GBK，希腊字母/箭头之外的符号会抛 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("history") or {}


def nw_var_mean(x: list[float], lag: int) -> float:
    """Newey-West(Bartlett) 校正后的【均值方差】。

    Var(mean) = (1/n) * [ γ0 + 2 * Σ_{j=1..L} (1 - j/(L+1)) γ_j ]
    """
    n = len(x)
    mean = sum(x) / n
    d = [v - mean for v in x]
    gamma0 = sum(v * v for v in d) / n
    acc = gamma0
    for j in range(1, min(lag, n - 1) + 1):
        gamma_j = sum(d[t] * d[t - j] for t in range(j, n)) / n
        acc += 2.0 * (1.0 - j / (lag + 1.0)) * gamma_j
    return acc / n


def se_of_ir(x: list[float], lag: int) -> tuple[float, float, float]:
    """返回 (SE(IR) 的 NW 值, SE(IR) 的 naive 值, 滞后阶)。"""
    n = len(x)
    std = statistics.stdev(x) if n > 1 else 0.0
    if std <= 1e-12:
        return float("nan"), float("nan"), lag
    var_mean = nw_var_mean(x, lag)
    if var_mean <= 0:
        return float("nan"), 1.0 / math.sqrt(n), lag
    return math.sqrt(var_mean) / std, 1.0 / math.sqrt(n), lag


def autocorr1(x: list[float]) -> float:
    """滞后 1 自相关——重叠程度的直接观测量。"""
    n = len(x)
    if n < 3:
        return float("nan")
    mean = sum(x) / n
    d = [v - mean for v in x]
    gamma0 = sum(v * v for v in d) / n
    if gamma0 <= 1e-15:
        return float("nan")
    gamma1 = sum(d[t] * d[t - 1] for t in range(1, n)) / n
    return gamma1 / gamma0


def auto_lag(n: int) -> int:
    """Newey-West 的经验滞后阶。"""
    return max(1, int(math.floor(4.0 * (n / 100.0) ** (2.0 / 9.0))))


def ir_of(x: list[float]) -> float:
    std = statistics.stdev(x) if len(x) > 1 else 0.0
    if std <= 1e-12:
        return 0.0
    return (sum(x) / len(x)) / std


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=float, default=2.0, help="θ = k * SE(IR)，默认 2")
    args = ap.parse_args()

    hist = load(HISTORY)
    if not hist:
        raise SystemExit(f"找不到 {HISTORY} 的 history")

    print("=" * 100)
    print(f"IC 序列显著性分析   θ = {args.k:g} × SE(IR)     数据: {HISTORY}")
    print("=" * 100)

    for strategy in sorted(hist):
        entries = hist[strategy]
        if not entries:
            continue

        # ── 现状复刻：get_summary 的口径（混编 + entries[-60:]）──
        mixed_all = [e["ic"] for e in entries]
        mixed_60 = [e["ic"] for e in entries[-60:]]
        ir_prod = ir_of(mixed_60)
        se_prod, _, _ = se_of_ir(mixed_60, 19)
        theta_prod = args.k * se_prod

        print()
        print(f"── {strategy}  ({len(entries)} 条记录) ──")

        # ── 按 horizon 拆开 ──
        by_h: dict[int, list[tuple[str, float]]] = {}
        for e in entries:
            by_h.setdefault(int(e.get("lookforward", 0)), []).append(
                (str(e.get("date", "")), float(e["ic"]))
            )

        rows = []
        for h in sorted(by_h):
            series = [v for _, v in sorted(by_h[h])]
            n = len(series)
            if n < 3:
                continue
            ir = ir_of(series)
            se_nw, se_naive, lag = se_of_ir(series, max(h - 1, 1))
            se_auto, _, lag_auto = se_of_ir(series, auto_lag(n))
            rows.append({
                "h": h, "n": n, "dates": len({d for d, _ in by_h[h]}),
                "std": statistics.stdev(series),
                "ac1": autocorr1(series),
                "ir": ir, "se_naive": se_naive,
                "se_nw": se_nw, "lag": lag,
                "se_auto": se_auto, "lag_auto": lag_auto,
            })

        if not rows:
            print("  (每个 horizon 的样本都不足 3 条，跳过)")
            continue

        print(f"  {'horizon':>8}{'n':>5}{'天数':>6}{'IC标准差':>10}{'rho1':>8}"
              f"{'IR':>9}{'naive SE':>10}{'NW SE':>9}{'自动L':>7}{'NW SE':>9}")
        for r in rows:
            print(f"  {str(r['h']) + '日':>8}{r['n']:>5}{r['dates']:>6}"
                  f"{r['std']:>10.4f}{r['ac1']:>8.3f}{r['ir']:>+9.3f}"
                  f"{r['se_naive']:>10.3f}{r['se_nw']:>9.3f}"
                  f"{r['lag_auto']:>7}{r['se_auto']:>9.3f}")

        print(f"  混编口径(现状, entries[-60:], n={len(mixed_60)})："
              f"IR={ir_prod:+.3f}  NW SE≈{se_prod:.3f}  含 {len(by_h)} 种 horizon 混算")

        # ── 判定 ──
        def verdict(ir: float, se: float) -> str:
            if math.isnan(se):
                return "n/a"
            return "过关" if abs(ir) >= args.k * se else "噪声"

        print(f"  {'':>8}  θ(naive)={args.k * rows[0]['se_naive']:.3f}   "
              + "  ".join(
                  f"{r['h']}日: IR={r['ir']:+.3f} "
                  f"θ_nw={args.k * r['se_nw']:.3f}→{verdict(r['ir'], r['se_nw'])}"
                  for r in rows
              ))

        print(f"  {'':>8}  θ(混编,现状)={theta_prod:.3f} → "
              f"{'过关' if abs(ir_prod) >= theta_prod else '噪声'}")

    print()
    print("=" * 100)
    print("读法：naive SE 是现状隐含口径；NW SE 是重叠校正后的真实口径。")
    print("      ρ₁ 越接近 1 说明重叠越严重，naive SE 低估越多。")
    print("=" * 100)


if __name__ == "__main__":
    main()
