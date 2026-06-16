"""多参数回测：MaVolume 策略的 6 组参数组合测试。

每组跑 500 只股票 × 3 个月 (2026-03-01 ~ 2026-06-01)。
"""

import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sequoia_x.data.engine import DataEngine
from sequoia_x.backtest.engine import BacktestEngine, BacktestConfig
from sequoia_x.backtest.metrics import PerformanceMetrics
from sequoia_x.strategy.ma_volume import MaVolumeStrategy

# 6 组参数
PARAMS = [
    {"short": 5, "long": 20, "vol": 1.5, "sl": -0.08, "tp": 0.20, "name": "A-基准 MA5/20"},
    {"short": 10, "long": 30, "vol": 1.5, "sl": -0.08, "tp": 0.20, "name": "B-MA10/30"},
    {"short": 5, "long": 20, "vol": 2.0, "sl": -0.08, "tp": 0.20, "name": "C-放量2x"},
    {"short": 5, "long": 20, "vol": 1.5, "sl": -0.05, "tp": 0.15, "name": "D-紧止损"},
    {"short": 10, "long": 30, "vol": 2.0, "sl": -0.08, "tp": 0.20, "name": "E-MA10/30放量2x"},
    {"short": 5, "long": 20, "vol": 1.5, "sl": -0.10, "tp": 0.25, "name": "F-宽止损"},
]

import sqlite3
conn = sqlite3.connect("data/sequoia_v2.db")
symbols = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM stock_daily LIMIT 500").fetchall()]
conn.close()

engine = DataEngine.__new__(DataEngine)
engine.db_path = "data/sequoia_v2.db"

def make_settings(short, long, vol):
    """创建带自定义参数的 Settings 实例。"""
    from sequoia_x.core.config import get_settings
    s = get_settings()
    object.__setattr__(s, "ma_volume_short", short)
    object.__setattr__(s, "ma_volume_long", long)
    object.__setattr__(s, "ma_volume_threshold", vol)
    return s

def run(p):
    t0 = time.time()
    settings = make_settings(p["short"], p["long"], p["vol"])
    cfg = BacktestConfig(
        initial_capital=1_000_000,
        stop_loss_pct=p["sl"],
        take_profit_pct=p["tp"],
    )
    bt = BacktestEngine(engine, cfg)
    result = bt.run(MaVolumeStrategy, symbols, "2026-03-01", "2026-06-01",
                    strategy_kwargs={"settings": settings})
    metrics = PerformanceMetrics.from_trades(result.trades, result.equity_curve)
    elapsed = time.time() - t0
    report = {
        "name": p["name"],
        "trades": metrics.total_trades,
        "win_rate": round(metrics.win_rate, 4),
        "sharpe": round(metrics.sharpe_ratio, 2),
        "max_drawdown": round(metrics.max_drawdown, 4),
        "total_return": round(metrics.total_return, 4),
        "annual_return": round(metrics.annual_return, 4),
        "avg_win": round(metrics.avg_win, 2),
        "avg_loss": round(metrics.avg_loss, 2),
        "elapsed_sec": round(elapsed, 0),
    }
    print(f"[{p['name']}] {metrics.total_trades}笔 | 胜率{metrics.win_rate:.1%} | "
          f"夏普{metrics.sharpe_ratio:.2f} | 收益{metrics.total_return:.1%} | {elapsed:.0f}s")
    return report

if __name__ == "__main__":
    print(f"回测: {len(symbols)}只 × 2026-03-01 ~ 2026-06-01 × {len(PARAMS)}组参数\n")
    results = [run(p) for p in PARAMS]

    print("\n" + "=" * 85)
    print(f"  MaVolume 多参数回测结果汇总")
    print("=" * 85)
    print(f"{'参数':<20} {'交易':<6} {'胜率':<8} {'夏普':<8} {'收益':<10} {'年化':<10} {'回撤':<8} {'均盈':<8} {'均亏':<8}")
    print("-" * 85)
    for r in results:
        print(f"{r['name']:<20} {r['trades']:<6} {r['win_rate']*100:<7.1f}% "
              f"{r['sharpe']:<8} {r['total_return']*100:<7.1f}% "
              f"{r['annual_return']*100:<7.1f}% {r['max_drawdown']*100:<7.1f}% "
              f"{r['avg_win']:<8} {r['avg_loss']:<8}")

    out = {"symbols": len(symbols), "start": "2026-03-01", "end": "2026-06-01",
           "results": results}
    with open("output/multi_backtest.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: output/multi_backtest.json")
