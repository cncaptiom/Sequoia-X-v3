"""信号质量监控模块：IC追踪、信号衰减检测、权重自动调整。

IC = Information Coefficient = Spearman 秩相关系数（策略评分与未来收益之间）。

流程:
  1. 每日评分后，记录所有股票的各策略评分
  2. N 日后，计算每只股票 N 日收益率
  3. 对每个策略，计算评分与收益率的 Spearman 秩相关 → 当日 IC
  4. 维护 IC 时间序列，检测持续衰减（连续 20 日 IC < 0）
  5. 根据 IC 历史自动调整策略权重
"""

import json
import os
from datetime import date, timedelta
from typing import Optional

import numpy as np


def _rank_ic(x: list[float], y: list[float]) -> tuple[float, float]:
    """Spearman 秩相关。有 scipy 就用，没有就纯 numpy 近似（无 p-value）。"""
    try:
        from scipy.stats import spearmanr
        return spearmanr(x, y)
    except ImportError:
        pass
    n = len(x)
    if n < 3:
        return 0.0, 1.0
    x_rank = np.argsort(np.argsort(x)).astype(float)
    y_rank = np.argsort(np.argsort(y)).astype(float)
    d = x_rank - y_rank
    rho = 1.0 - (6.0 * np.sum(d ** 2)) / (n * (n ** 2 - 1.0))
    return rho, 0.0


class ICTracker:
    """追踪每个策略的 IC 时间序列，检测信号衰减，调整权重。"""

    STRATEGIES = ["maVolume", "turtleTrade", "meanReversion", "volumePrice",
                  "atrChannel", "macdRsi", "maCross", "kdj"]

    def __init__(self, history_path: str = "output/ic_history.json"):
        self.history_path = history_path
        # strategy -> list of {date, ic, p_value, sample_size, lookforward}
        self.history: dict[str, list[dict]] = {s: [] for s in self.STRATEGIES}
        # 待评估的评分快照: {date: {symbol: {strategy: score}}}
        self._pending: dict[str, dict[str, dict[str, float]]] = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.history_path):
            return
        try:
            with open(self.history_path) as f:
                data = json.load(f)
            self.history = {s: data.get("history", {}).get(s, [])
                            for s in self.STRATEGIES}
            self._pending = data.get("pending", {})
        except Exception:
            pass

    def save(self):
        out_dir = os.path.dirname(self.history_path) or "."
        os.makedirs(out_dir, exist_ok=True)
        with open(self.history_path, "w") as f:
            json.dump({
                "history": self.history,
                "pending": self._pending,
            }, f, ensure_ascii=False, indent=2)

    # ── 评分记录 ──

    def record_scores(self, date_str: str,
                      scores_by_symbol: dict[str, dict[str, float]]):
        """记录当日全市场各策略评分。

        Args:
            date_str: "YYYY-MM-DD"
            scores_by_symbol: {symbol: {strategy: score, ...}, ...}
        """
        # 去重：已有记录的日期不再覆盖
        if date_str not in self._pending:
            self._pending[date_str] = scores_by_symbol
            self.save()

    # ── N 日 IC 计算（交易日） ──

    NEEDED_PERIODS = [5, 20]  # 需要计算的 lookforward 天数

    def eval_forward_returns(self, engine,
                             lookforward_days: int = 5,
                             batch_size: int = 200):
        """对 pending 快照计算 forward IC（使用交易日而非日历日）。

        非破坏性：快照在所有 NEEDED_PERIODS 都计算后才移除。
        交易日查找：基于已评估股票的实际交易数据。

        Args:
            engine: DataEngine
            lookforward_days: N 个交易日后的收益率
            batch_size: 每次批量取多少只股票
        """
        today = date.today()
        from sequoia_x.core.logger import get_logger
        log = get_logger("ICTracker")

        # 已评估记录: {date_str: {lookforward: True}}
        evaluated: dict[str, dict[int, bool]] = {}
        self._eval_record = getattr(self, "_eval_record", evaluated)
        if not hasattr(self, "_eval_record") or self._eval_record is None:
            self._eval_record = {}

        # 确定哪些 pending 快照可评估
        evaluable = []
        for d_str in list(self._pending.keys()):
            try:
                d = date.fromisoformat(d_str)
            except ValueError:
                continue
            if (today - d).days >= lookforward_days * 1.5:  # 宽松检查
                # 检查是否已算过此 lookforward
                rec = self._eval_record.setdefault(d_str, {})
                if not rec.get(lookforward_days, False):
                    evaluable.append(d_str)

        if not evaluable:
            return

        for d_str in evaluable:
            scores_by_symbol = self._pending[d_str]
            log.info(f"IC计算 [{d_str}] lookforward={lookforward_days}d, "
                     f"股票数={len(scores_by_symbol)}")

            symbols = list(scores_by_symbol.keys())
            if not symbols:
                continue

            # 用第一个有数据的股票构建交易日历
            trade_dates: list[str] = []
            for sym in symbols[:10]:
                df = engine.get_ohlcv(sym)
                if df is not None and not df.empty:
                    trade_dates = sorted(df[df["date"] >= d_str]["date"].tolist())
                    break
            if not trade_dates:
                continue

            try:
                d_idx = trade_dates.index(d_str)
            except ValueError:
                continue
            fwd_idx = d_idx + lookforward_days
            if fwd_idx >= len(trade_dates):
                continue
            target_str = trade_dates[fwd_idx]

            # 批量加载 OHLCV 数据
            ohlcv_map = engine.get_bulk_ohlcv(symbols)

            # 对每个策略计算 IC
            strategy_scores: dict[str, dict[str, float]] = {s: {} for s in self.STRATEGIES}
            strategy_rets: dict[str, dict[str, float]] = {s: {} for s in self.STRATEGIES}

            for sym in symbols:
                sym_scores = scores_by_symbol.get(sym, {})
                df = ohlcv_map.get(sym)
                if df is None or df.empty:
                    continue

                start_rows = df[df["date"] <= d_str]
                if start_rows.empty:
                    continue
                start_price = float(start_rows["close"].iloc[-1])

                end_rows = df[df["date"] <= target_str]
                if end_rows.empty:
                    continue
                end_price = float(end_rows["close"].iloc[-1])

                if start_price <= 0:
                    continue
                fwd_return = (end_price - start_price) / start_price

                for s in self.STRATEGIES:
                    score = sym_scores.get(s)
                    if score is not None:
                        strategy_scores[s][sym] = score
                        strategy_rets[s][sym] = fwd_return

            # 计算每个策略的 IC
            for s in self.STRATEGIES:
                scores = strategy_scores[s]
                rets = strategy_rets[s]
                common = set(scores.keys()) & set(rets.keys())
                if len(common) < 10:
                    continue

                sym_list = list(common)
                score_arr = [scores[sym] for sym in sym_list]
                ret_arr = [rets[sym] for sym in sym_list]
                ic, p_val = _rank_ic(score_arr, ret_arr)

                sorted_pairs = sorted(zip(score_arr, ret_arr), key=lambda x: x[0])
                n = len(sorted_pairs)
                k = max(n // 5, 1)
                top = sorted_pairs[-k:]
                bot = sorted_pairs[:k]
                top_ret = float(np.mean([r for _, r in top]))
                bot_ret = float(np.mean([r for _, r in bot]))
                spread = top_ret - bot_ret

                entry = {
                    "date": d_str,
                    "lookforward": lookforward_days,
                    "ic": round(float(ic), 4),
                    "p_value": round(float(p_val), 4),
                    "sample_size": len(common),
                    "top_quintile_return": round(top_ret, 4),
                    "bottom_quintile_return": round(bot_ret, 4),
                    "spread_return": round(spread, 4),
                }
                self.history[s].append(entry)

            # 标记此 lookforward 已计算
            self._eval_record[d_str][lookforward_days] = True

            # 所有 NEEDED_PERIODS 都算完了才移除快照
            all_done = all(
                self._eval_record[d_str].get(f, False)
                for f in self.NEEDED_PERIODS
            )
            if all_done:
                self._pending.pop(d_str, None)
                self._eval_record.pop(d_str, None)

            log.info(f"IC计算完成 [{d_str}] lookforward={lookforward_days}d")

        self.save()

    # ── 衰减检测 ──

    def detect_decay(self, strategy: str,
                     threshold: float = 0.0,
                     consecutive_days: int = 20) -> tuple[bool, int]:
        """检测策略信号是否持续衰减（连续 N 天 IC 低于阈值）。

        Returns:
            (是否衰减, 连续异常天数)
        """
        entries = self.history.get(strategy, [])
        neg_run = 0
        for entry in reversed(entries):
            if entry.get("ic", 0) < threshold:
                neg_run += 1
            else:
                break
        return neg_run >= consecutive_days, neg_run

    # ── 权重调整 ──

    def adjust_weights(self, base_weights: dict[str, float],
                       decay_penalty: float = 0.5) -> dict[str, float]:
        """根据 IC 历史调整权重。衰减策略权重打折，重新归一化。

        Args:
            base_weights: {strategy: weight}
            decay_penalty: 衰减策略的权重乘数

        Returns:
            调整后的权重 {strategy: weight}
        """
        adjusted = {}
        for s, w in base_weights.items():
            is_decayed, _ = self.detect_decay(s)
            adjusted[s] = w * (decay_penalty if is_decayed else 1.0)
        total = sum(adjusted.values())
        if total > 0:
            adjusted = {k: round(v / total, 4) for k, v in adjusted.items()}
        return adjusted

    # ── 报告 ──

    def get_summary(self) -> dict:
        """生成信号质量摘要报告。"""
        summary = {}
        for s in self.STRATEGIES:
            entries = self.history.get(s, [])
            if not entries:
                continue
            recent = entries[-min(len(entries), 20):]
            avg_ic = float(np.mean([e["ic"] for e in recent]))
            ic_std = float(np.std([e["ic"] for e in recent])) if len(recent) > 1 else 0

            entries_all = entries
            avg_60d = float(np.mean([e["ic"] for e in entries_all[-60:]])) if len(entries_all) >= 60 else avg_ic
            ic_std_60d = float(np.std([e["ic"] for e in entries_all[-60:]])) if len(entries_all) >= 60 else ic_std

            ir_20 = avg_ic / ic_std if ic_std > 0 else 0
            ir_60 = avg_60d / ic_std_60d if ic_std_60d > 0 else 0
            latest_spread = recent[-1].get("spread_return", 0) if recent else 0
            is_decayed, neg_run = self.detect_decay(s)

            summary[s] = {
                "avg_ic_20d": round(avg_ic, 4),
                "avg_ic_60d": round(avg_60d, 4),
                "ir_20d": round(ir_20, 4),
                "ir_60d": round(ir_60, 4),
                "latest_spread": round(latest_spread, 4),
                "neg_consecutive": neg_run,
                "decayed": is_decayed,
                "samples": len(entries),
            }
        return summary

    def print_summary(self):
        """打印信号质量报告到控制台。"""
        summary = self.get_summary()
        if not summary:
            print("无 IC 数据")
            return
        print(f"\n{'='*90}")
        print(f"  信号质量报告 (IC追踪)")
        print(f"{'='*90}")
        print(f"{'策略':<20} {'IC-20d':<10} {'IC-60d':<10} {'IR-20d':<10} {'IR-60d':<10} {'价差':<8} {'衰减':<8}")
        print(f"{'-'*90}")
        for s in self.STRATEGIES:
            info = summary.get(s)
            if not info:
                continue
            label = s
            decay_mark = "⚠" if info["decayed"] else "✓"
            print(f"{label:<20} {info['avg_ic_20d']:<+10.4f} {info['avg_ic_60d']:<+10.4f} "
                  f"{info['ir_20d']:<+10.4f} {info['ir_60d']:<+10.4f} "
                  f"{info['latest_spread']:<+8.4f} {decay_mark:<8}")
        print(f"{'='*90}")
