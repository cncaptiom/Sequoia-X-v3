"""对照：IC 历史去重前后的 IR 与「归零」判定。

`apply_ir_weights`（rating/engine.py:149）的逻辑是：读 ICTracker 摘要里的 IR，
**IR <= 0 的策略权重直接清零**。而 IR = avg_ic / ic_std，来自
`ICTracker.get_summary`（signal/quality.py:372）。

本脚本按 get_summary 的算法，分别读：
  - output/ic_history.json              （去重后，当前）
  - output/ic_history.json.bak-20261013 （去重前，若存在）
对比每个策略的 IR 符号与归零判定有没有变化。

只读，不写任何文件。用法: python show_ic_ir.py
"""

from __future__ import annotations

import json
import os

import numpy as np

AFTER = "output/ic_history.json"
BEFORE = "output/ic_history.json.bak-20261013"


def summarize(history: dict) -> dict:
    """复刻 ICTracker.get_summary 的 IR 计算。"""
    out = {}
    for s, entries in history.items():
        if not entries:
            continue
        n = len(entries)
        recent = entries[-min(n, 20):]
        avg_ic = float(np.mean([e["ic"] for e in recent]))
        ic_std = float(np.std([e["ic"] for e in recent])) if len(recent) > 1 else 0.0

        if n >= 60:
            tail = entries[-60:]
            avg_60d = float(np.mean([e["ic"] for e in tail]))
            ic_std_60d = float(np.std([e["ic"] for e in tail]))
        else:
            avg_60d, ic_std_60d = avg_ic, ic_std
        ic_std_60d = ic_std_60d if ic_std_60d > 1e-10 else 0.0

        ir_20 = avg_ic / ic_std if ic_std > 1e-10 else 0.0
        ir_60 = avg_60d / ic_std_60d if ic_std_60d > 1e-10 else 0.0
        # apply_ir_weights 里写的是 `info.get("ir_60d", 0) or info.get("ir_20d", 0)`
        ir_used = ir_60 if ir_60 else ir_20

        out[s] = {
            "n": n,
            "avg_ic": avg_ic,
            "ir_20": ir_20,
            "ir_60": ir_60,
            "ir_used": ir_used,
            "zeroed": ir_used <= 0,
        }
    return out


def load(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("history") or {}


def main() -> None:
    after = load(AFTER)
    before = load(BEFORE)
    if after is None:
        raise SystemExit(f"找不到 {AFTER}")

    sa = summarize(after)
    sb = summarize(before) if before else None

    if sb is None:
        print(f"（未找到 {BEFORE}，只显示当前）")
        print(f"{'策略':<15}{'条数':>6}{'avg_ic':>10}{'ir_20':>9}{'ir_60':>9}  判定")
        for s in sorted(sa):
            d = sa[s]
            print(
                f"{s:<15}{d['n']:>6}{d['avg_ic']:>+10.4f}{d['ir_20']:>+9.3f}"
                f"{d['ir_60']:>+9.3f}  {'归零' if d['zeroed'] else '保留'}"
            )
        return

    print(f"{'策略':<15}{'去重前':<26}{'去重后':<26}判定变化")
    print(f"{'':<15}{'条数':>6}{'ir_60':>9}{'判定':>10}{'条数':>6}{'ir_60':>9}{'判定':>10}")
    changed = []
    keep_b = keep_a = 0
    for s in sorted(set(sa) | set(sb)):
        b, a = sb.get(s), sa.get(s)
        if b is None or a is None:
            continue
        keep_b += 0 if b["zeroed"] else 1
        keep_a += 0 if a["zeroed"] else 1
        flip = "← 变了" if b["zeroed"] != a["zeroed"] else ""
        if flip:
            changed.append(s)
        print(
            f"{s:<15}{b['n']:>6}{b['ir_60']:>+9.3f}{'归零' if b['zeroed'] else '保留':>10}"
            f"{a['n']:>6}{a['ir_60']:>+9.3f}{'归零' if a['zeroed'] else '保留':>10}  {flip}"
        )

    print()
    print(f"去重前保留 {keep_b} 个策略 | 去重后保留 {keep_a} 个策略")
    if changed:
        print(f"判定翻转的策略: {', '.join(changed)}")
    else:
        print("判定未变 —— 说明归零不是重复条目造成的，那些策略的 IC 本来就是负的。")

    total = keep_a
    if total >= 3:
        kept = {s: sa[s]["ir_used"] for s in sa if not sa[s]["zeroed"]}
        tot = sum(kept.values())
        print()
        print("按当前（去重后）IR，apply_ir_weights 会给出:")
        for s, v in sorted(kept.items(), key=lambda kv: -kv[1]):
            print(f"  {s:<15} {v / tot:.4f}")
    else:
        print()
        print(f"去重后只有 {total} 个正 IR 策略 —— 少于 3，apply_ir_weights 会提前返回，")
        print("保留 apply_regime 设置的权重（不再清零）。")


if __name__ == "__main__":
    main()
