"""回测框架：滑动窗口回测、绩效归因、策略对比。

用法:
    from sequoia_x.backtest.engine import BacktestEngine
    from sequoia_x.backtest.metrics import PerformanceMetrics

    engine = BacktestEngine(data_engine)
    results = engine.run(strategy, symbols, start="2024-01-01", end="2025-01-01")
    metrics = PerformanceMetrics.from_trades(results.trades)
    print(metrics.summary())
"""

from sequoia_x.backtest.engine import BacktestEngine, BacktestConfig, BacktestResult
from sequoia_x.backtest.metrics import PerformanceMetrics

__all__ = [
    "BacktestEngine",
    "BacktestConfig",
    "BacktestResult",
    "PerformanceMetrics",
]
