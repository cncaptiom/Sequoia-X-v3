"""一次性迁移：清理 output/ic_history.json 里重复的 IC 条目。

背景（详见 docs/code-review-2026-10-13.md）：
  `ICTracker._eval_record` 原先不持久化，于是每个尚未完成的 pending 快照在**每次运行**
  都被重新评估，向 `history` 重复追加同一天同一 lookforward 的条目。
  实测：turtleTrade 774 条却只有 41 个不同日期，同一日期最多重复 30 次；
  而 `detect_decay` 是「从末尾数连续 IC<0 的条数」——数的是**条目**不是日期，
  所以 20 条重复就能满足「连续 20 天衰减」，进而触发 IC 衰减降权。

  该 bug 本体已在 `sequoia_x/signal/quality.py` 修复（不再产生新重复）；
  本脚本负责清理**已经写进文件**的旧重复。

规则：
  - 每个 (策略, 日期, lookforward) 只保留**最后一条**（越晚算的，用的库数据越全）
  - 结果按 (日期, lookforward) **排序**——原顺序是「首次评估顺序」，
    `entries[-20:]` 取的是「最近 20 条」，必须让顺序真正对应日期
  - **不改动 pending**

用法:
  python dedup_ic_history.py            # 预览，不写文件
  python dedup_ic_history.py --apply    # 备份后实际写入
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from collections import OrderedDict

PATH = "output/ic_history.json"
BACKUP = "output/ic_history.json.bak-20261013"


def main() -> None:
    parser = argparse.ArgumentParser(description="清理 ic_history.json 的重复条目")
    parser.add_argument("--apply", action="store_true", help="备份后实际写入；默认只预览")
    args = parser.parse_args()

    if not os.path.exists(PATH):
        raise SystemExit(f"找不到 {PATH}")

    with open(PATH, encoding="utf-8") as fh:
        data = json.load(fh)

    history = data.get("history") or {}
    new_history: dict[str, list[dict]] = {}
    before_total = after_total = 0

    print(f"{'策略':<16}{'去重前':>8}{'去重后':>8}{'不同日期':>10}{'最大重复':>10}")
    for s in sorted(history):
        entries = history[s]
        seen: OrderedDict = OrderedDict()
        repeats: dict = {}
        for e in entries:
            key = (e.get("date"), e.get("lookforward"))
            seen[key] = e                       # 后出现的覆盖先出现的 → 保留最后一条
            repeats[key] = repeats.get(key, 0) + 1
        deduped = sorted(
            seen.values(), key=lambda e: (e.get("date", ""), e.get("lookforward", 0))
        )
        new_history[s] = deduped
        dates = len({e.get("date") for e in deduped})
        before_total += len(entries)
        after_total += len(deduped)
        print(
            f"{s:<16}{len(entries):>8}{len(deduped):>8}{dates:>10}"
            f"{max(repeats.values()) if repeats else 0:>10}"
        )
    print(f"{'合计':<16}{before_total:>8}{after_total:>8}")

    if not args.apply:
        print()
        print("（预览模式，未写入。确认无误后加 --apply）")
        return

    if os.path.exists(BACKUP):
        print(f"备份已存在，未覆盖: {BACKUP}")
    else:
        shutil.copy2(PATH, BACKUP)
        print(f"已备份: {BACKUP}")

    data["history"] = new_history
    data.setdefault("eval_record", {})
    with open(PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    print(f"已写入: {PATH}（pending 未改动，共 {len(data.get('pending') or {})} 个快照）")


if __name__ == "__main__":
    main()
