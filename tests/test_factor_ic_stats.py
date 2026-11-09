"""因子 IC 统计测试：重叠前瞻窗口必须做自相关修正。

对应 2026-11-09 代码审查 #12。
"""
import numpy as np

from run_factor_validation import _ic_stats


def test_overlapping_ic_series_gets_wider_standard_error():
    """高度自相关的 IC 序列：NW 修正后的 t 值应明显小于朴素 t 值。"""
    rng = np.random.default_rng(3)
    # 用滚动均值制造强自相关（模拟重叠 20 日收益下的 IC 序列）
    raw = rng.normal(0, 1, 400)
    smooth = np.convolve(raw, np.ones(15) / 15, mode="valid")
    ics = 0.05 + 0.02 * smooth

    stats = _ic_stats(ics, overlap=20)
    assert stats["t_nw"] is not None
    assert abs(stats["t_nw"]) < abs(stats["t_naive"])


def test_independent_series_keeps_similar_t():
    rng = np.random.default_rng(5)
    ics = rng.normal(0.02, 0.05, 500)

    stats = _ic_stats(ics, overlap=1)   # overlap=1 → 无滞后项，等价普通 t
    # 两者分母自由度口径略有差别（总体 vs 样本方差），差异应在舍入级别
    assert abs(stats["t_nw"] - stats["t_naive"]) < 0.05


def test_effective_sample_size_uses_overlap():
    ics = np.linspace(-0.05, 0.05, 100)
    stats = _ic_stats(ics, overlap=20)
    assert stats["n_days"] == 100
    assert stats["n_eff"] == 5


def test_too_few_samples_returns_none():
    stats = _ic_stats(np.array([0.1, 0.2]), overlap=5)
    assert stats["mean_ic"] is None
    assert stats["n_eff"] == 0
