"""信号质量监控模块：IC追踪、信号衰减检测。

IC = Information Coefficient = Spearman 秩相关系数（策略评分与未来收益之间）。

流程:
  1. 每日评分后，记录所有股票的各策略评分
  2. N 日后，计算每只股票 N 日收益率
  3. 对每个策略，计算评分与收益率的 Spearman 秩相关 → 当日 IC
  4. 维护 IC 时间序列，检测持续衰减（同一前瞻期内连续 20 日 IC < 0）
  5. 报告信号质量（`get_summary` / `print_summary`）；`adjust_weights` 保留但
     **不在生产路径上**——权重只由市场状态决定，理由见 rating/engine.py。
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


# ── 因子预处理 ──


def winsorize(arr: list[float], lower_pct: float = 1.0, upper_pct: float = 99.0) -> list[float]:
    """去极值：百分位法截断极端值。

    将低于 lower_pct 百分位的值提升到该分位值，
    将高于 upper_pct 百分位的值降低到该分位值。
    """
    if not arr:
        return arr
    a = np.array(arr)
    lo = float(np.percentile(a, lower_pct))
    hi = float(np.percentile(a, upper_pct))
    return np.clip(a, lo, hi).tolist()


def standardize(arr: list[float]) -> list[float]:
    """标准化：Z-score，使均值为0、标准差为1。"""
    if not arr:
        return arr
    a = np.array(arr, dtype=float)
    mu = float(np.mean(a))
    sigma = float(np.std(a))
    if sigma < 1e-10:
        return [0.0] * len(a)
    return ((a - mu) / sigma).tolist()


class ICTracker:
    """追踪每个策略的 IC 时间序列，检测信号衰减，调整权重。"""

    STRATEGIES = ["maVolume", "turtleTrade", "meanReversion", "volumePrice",
                  "atrChannel", "macdRsi", "maCross", "kdj", "fundamental"]

    # 窗口统计（last-20 / last-60 条）所依据的前瞻期。
    # 5 日与 20 日 IC 量纲不同，混进同一个序列会把组间方差算进 ic_std，
    # 使 |IR| 被系统性压小、符号还可能翻转（实测 turtleTrade 拆分后
    # +0.096 / +0.121，混编后 −0.070）。任何窗口统计都必须先按期限拆开。
    PRIMARY_LOOKFORWARD = 20

    def __init__(self, history_path: str = "output/ic_history.json"):
        self.history_path = history_path
        # strategy -> list of {date, ic, p_value, sample_size, lookforward}
        self.history: dict[str, list[dict]] = {s: [] for s in self.STRATEGIES}
        # 待评估的评分快照: {date: {symbol: {strategy: score}}}
        self._pending: dict[str, dict[str, dict[str, float]]] = {}
        # 已评估记录: {date: {lookforward: True}}。**必须持久化**——
        # 否则每个尚未完成的快照会在每次运行时被重新评估，向 history 重复追加
        # 同一天同一 lookforward 的 IC 条目（实测同一日期最多重复 30 次）。
        self._eval_record: dict[str, dict[int, bool]] = {}
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
            # lookforward 在 JSON 里是字符串键，读回时转回 int
            self._eval_record = {
                d: {int(k): bool(v) for k, v in m.items()}
                for d, m in (data.get("eval_record") or {}).items()
            }
        except Exception:
            pass

    def save(self):
        out_dir = os.path.dirname(self.history_path) or "."
        os.makedirs(out_dir, exist_ok=True)
        with open(self.history_path, "w") as f:
            json.dump({
                "history": self.history,
                "pending": self._pending,
                "eval_record": self._eval_record,
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

    # ── 因子预处理 ──

    @staticmethod
    def preprocess_scores(scores_by_symbol: dict[str, dict[str, float]],
                          strategy_names: list[str],
                          winsorize_pct: float = 1.0) -> dict[str, dict[str, float]]:
        """截面预处理：对每个策略的评分做去极值 + 标准化。

        步骤：
          1. 收集每个策略在全市场所有股票的原始评分
          2. 去极值（百分位截断）
          3. Z-score 标准化
          4. 放回原结构

        Args:
            scores_by_symbol: {symbol: {strategy: score}}
            strategy_names: 需要处理的策略名称列表
            winsorize_pct: 截断百分位（默认1%/99%）

        Returns:
            预处理后的相同结构
        """
        # 收集每个策略的原始值
        raw: dict[str, list[tuple[str, float]]] = {s: [] for s in strategy_names}
        for sym, ss in scores_by_symbol.items():
            for s in strategy_names:
                v = ss.get(s)
                if v is not None:
                    raw[s].append((sym, v))

        # 对每个策略做预处理
        processed: dict[str, dict[str, float]] = {}
        for s in strategy_names:
            pairs = raw.get(s, [])
            if not pairs:
                continue
            symbols = [p[0] for p in pairs]
            values = [p[1] for p in pairs]

            # 去极值
            trimmed = winsorize(values, lower_pct=winsorize_pct,
                                upper_pct=100.0 - winsorize_pct)
            # 标准化
            normalized = standardize(trimmed)

            for sym, val in zip(symbols, normalized):
                if sym not in processed:
                    processed[sym] = {}
                processed[sym][s] = val

        return processed

    # ── N 日 IC 计算（交易日） ──

    NEEDED_PERIODS = [5, 20]  # 需要计算的 lookforward 天数

    def _maybe_cleanup(self, d_str: str) -> None:
        """NEEDED_PERIODS 全部评估完成后移除该快照。"""
        rec = self._eval_record.get(d_str)
        if rec and all(rec.get(f, False) for f in self.NEEDED_PERIODS):
            self._pending.pop(d_str, None)
            self._eval_record.pop(d_str, None)

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

        # 注意：`self._eval_record` 由 __init__ 初始化、_load 从磁盘恢复，
        # 这里**不能**重置它——重置会让每个未完成快照每次运行都被重算并重复追加。

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

            # 因子预处理：去极值 + 标准化
            scores_by_symbol = self.preprocess_scores(
                scores_by_symbol, self.STRATEGIES, winsorize_pct=1.0
            )

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
                # d_str 不在交易日序列里 —— 周末/节假日手工运行产生的快照，
                # 永远无法评估。必须标记为已处理，否则它会永久卡在 pending 里、
                # 每次运行都被重算一遍（这正是原先快照卡死的原因）。
                self._eval_record.setdefault(d_str, {})[lookforward_days] = True
                self._maybe_cleanup(d_str)
                log.info(f"IC跳过 [{d_str}]：非交易日，已清理待评估快照")
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
            self._maybe_cleanup(d_str)

            log.info(f"IC计算完成 [{d_str}] lookforward={lookforward_days}d")

        self.save()

    # ── IC 序列 ──

    def _series(self, strategy: str, lookforward: int) -> list[dict]:
        """某策略在指定前瞻期上的 IC 序列，按日期排序、同日重复只保留最后一条。

        绝不跨期限合并——见 PRIMARY_LOOKFORWARD 的注释。
        """
        by_date: dict[str, dict] = {}
        for e in self.history.get(strategy, []):
            if int(e.get("lookforward", 0)) != lookforward:
                continue
            by_date[e.get("date", "")] = e
        return [by_date[d] for d in sorted(by_date)]

    # ── 衰减检测 ──

    def detect_decay(self, strategy: str,
                     lookforward: int = PRIMARY_LOOKFORWARD,
                     threshold: float = 0.0,
                     consecutive_days: int = 20) -> tuple[bool, int]:
        """检测策略信号是否持续衰减（连续 N 个交易日 IC 低于阈值）。

        只在一个期限的序列上数「连续」——此前是在混编序列上数【条目】，而每个
        日期贡献两条（5 日 + 20 日），等于把「连续 20 日」缩成了约 10 日。

        Returns:
            (是否衰减, 连续异常天数)
        """
        neg_run = 0
        for entry in reversed(self._series(strategy, lookforward)):
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

    def get_summary(self, lookforward: int = PRIMARY_LOOKFORWARD) -> dict:
        """生成信号质量摘要报告，只在单一前瞻期内统计（不跨期限混算）。

        Args:
            lookforward: 窗口所依据的前瞻期，默认 20 日（与评级/回测口径对齐）。

        Returns:
            {策略: {lookforward, avg_ic_20d, avg_ic_60d, ir_20d, ir_60d,
                    ic_positive_ratio, ic_significant_ratio, ir_status,
                    latest_spread, neg_consecutive, decayed, samples}}
        """
        summary = {}
        for s in self.STRATEGIES:
            series = self._series(s, lookforward)
            if not series:
                continue
            recent = series[-20:]
            ic_vals = [e["ic"] for e in recent]
            avg_ic = float(np.mean(ic_vals))
            ic_std = float(np.std(ic_vals)) if len(ic_vals) > 1 else 0.0

            has_60 = len(series) >= 60
            tail = series[-60:] if has_60 else recent
            avg_60d = float(np.mean([e["ic"] for e in tail])) if has_60 else avg_ic
            ic_std_60d = float(np.std([e["ic"] for e in tail])) if has_60 and len(tail) > 1 else ic_std
            ic_std_60d = ic_std_60d if ic_std_60d > 1e-10 else 0.0

            ir_20 = avg_ic / ic_std if ic_std > 1e-10 else 0.0
            ir_60 = avg_60d / ic_std_60d if ic_std_60d > 1e-10 else 0.0
            latest_spread = recent[-1].get("spread_return", 0) if recent else 0
            is_decayed, neg_run = self.detect_decay(s, lookforward)

            # IC 正比例
            ic_pos_ratio = sum(1 for e in recent if e.get("ic", 0) > 0) / len(recent)

            # IC 显著性：p < 0.05 的比例
            ic_sig_ratio = sum(1 for e in recent if e.get("p_value", 1.0) < 0.05) / len(recent)

            # IR 状态判定。口径为 per-observation（未年化），年化因子 √(252/前瞻期)；
            # 这里的 1.0/0.5 阈值只在展示层使用，不驱动任何权重决策。
            if ir_20 >= 1.0:
                ir_status = "优秀"
            elif ir_20 >= 0.5:
                ir_status = "合格"
            elif ir_20 >= 0:
                ir_status = "弱"
            else:
                ir_status = "差"

            summary[s] = {
                "lookforward": lookforward,
                "avg_ic_20d": round(avg_ic, 4),
                "avg_ic_60d": round(avg_60d, 4),
                "ir_20d": round(ir_20, 4),
                "ir_60d": round(ir_60, 4),
                "ic_positive_ratio": round(ic_pos_ratio, 4),
                "ic_significant_ratio": round(ic_sig_ratio, 4),
                "ir_status": ir_status,
                "latest_spread": round(latest_spread, 4),
                "neg_consecutive": neg_run,
                "decayed": is_decayed,
                "samples": len(series),
            }
        return summary

    def print_summary(self):
        """打印信号质量报告到控制台（按前瞻期分块，5 日与 20 日不混算）。"""
        horizons = sorted({int(e.get("lookforward", 0))
                           for entries in self.history.values() for e in entries} - {0})
        printed = False
        for lf in horizons:
            summary = self.get_summary(lf)
            if not summary:
                continue
            printed = True
            print(f"\n{'='*110}")
            print(f"  信号质量报告 (IC追踪) │ 前瞻期={lf}日 │ IC正比=IC>0比例 │ "
                  f"IR状态: 优秀≥1.0 | 合格≥0.5 | 弱≥0 | 差<0")
            print(f"{'='*110}")
            print(f"{'策略':<18} {'IC-20d':<9} {'IC-60d':<9} {'IR-20d':<9} {'IR-60d':<9}"
                  f" {'IC正比':<8} {'p<.05':<8} {'IR状态':<6} {'价差':<7} {'衰减':<6}")
            print(f"{'-'*110}")
            for s in self.STRATEGIES:
                info = summary.get(s)
                if not info:
                    continue
                decay_mark = "[衰减]" if info["decayed"] else "[正常]"
                print(f"{s:<18} {info['avg_ic_20d']:<+9.4f} {info['avg_ic_60d']:<+9.4f} "
                      f"{info['ir_20d']:<+9.4f} {info['ir_60d']:<+9.4f} "
                      f"{info['ic_positive_ratio']:<8.2%} {info['ic_significant_ratio']:<8.2%} "
                      f"{info['ir_status']:<6} {info['latest_spread']:<+7.4f} {decay_mark:<6}")
            print(f"{'='*110}")
        if not printed:
            print("无 IC 数据")
