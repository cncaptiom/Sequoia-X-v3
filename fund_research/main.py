"""Sequoia Fund — ETF 轮动量化分析系统。

基于 Sequoia-X 选股系统的技术框架，拓展到 ETF 轮动策略。

用法:
  python -m fund_research.main --etf-fetch       # 拉取ETF历史数据
  python -m fund_research.main --etf-status       # 数据状态
  python -m fund_research.main --etf-rotate       # 最新一期轮动信号
  python -m fund_research.main --etf-backtest     # 回测轮动策略
"""

import argparse
import sys
from datetime import datetime, date, timedelta

from dotenv import load_dotenv
load_dotenv()

from fund_research.core.logger import get_logger

logger = get_logger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Sequoia Fund — ETF轮动量化系统")
    parser.add_argument("--etf-fetch", action="store_true", help="拉取ETF历史K线")
    parser.add_argument("--etf-status", action="store_true", help="ETF数据状态")
    parser.add_argument("--etf-list", action="store_true", help="列出ETF标的池")
    parser.add_argument("--etf-rotate", action="store_true", help="最新一期轮动信号")
    parser.add_argument("--etf-backtest", action="store_true", help="ETF轮动回测")
    parser.add_argument("--db-path", type=str, default="data/funds.db", help="数据库路径")
    args = parser.parse_args()

    if len(sys.argv) == 1:
        parser.print_help()
        return

    logger.info(f"Sequoia Fund 启动 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")

    # ── ETF 数据 ──
    if args.etf_fetch:
        from fund_research.data.etf_fetcher import EtfDataFetcher
        fetcher = EtfDataFetcher()
        count = fetcher.fetch_all()
        logger.info(f"拉取完成: {count}条")

    if args.etf_status:
        from fund_research.data.etf_fetcher import EtfDataFetcher
        status = EtfDataFetcher().status()
        print(f"ETF数量: {status['etf_count']}")
        print(f"数据行数: {status['rows']}")
        print(f"日期范围: {status['date_range']}")

    if args.etf_list:
        from fund_research.data.etf_list import ETF_POOL
        print(f"{'代码':<8} {'名称':<18} {'分类':<6} {'说明'}")
        print("-" * 56)
        for e in ETF_POOL:
            print(f"{e.code:<8} {e.name:<18} {e.category:<6} {e.description}")

    # ── ETF 轮动 ──
    if args.etf_rotate:
        _run_rotation()

    if args.etf_backtest:
        _run_backtest()


def _run_rotation():
    """输出最新一期轮动信号。"""
    from fund_research.data.etf_fetcher import EtfDataFetcher
    from fund_research.strategy.etf_rotation import EtfRotationStrategy, RotationConfig
    from fund_research.data.etf_list import get_etf_map

    fetcher = EtfDataFetcher()
    data = fetcher.get_all_ohlcv()
    if not data:
        logger.error("无ETF数据，请先运行 --etf-fetch")
        return

    latest = fetcher.get_latest_date()
    if not latest:
        logger.error("无法获取最新日期")
        return

    config = RotationConfig()
    strategy = EtfRotationStrategy(config)
    signal = strategy.run(data, latest)

    etf_map = get_etf_map()
    print(f"\n{'='*50}")
    print(f"ETF 轮动信号 ({signal.date})")
    print(f"{'='*50}")
    print(f"\n选中 Top {len(signal.selected)}:\n")
    for i, code in enumerate(signal.selected, 1):
        info = etf_map.get(code)
        name = info.name if info else code
        score = signal.scores.get(code, 0)
        print(f"  {i:2d}. {name:<16} ({code})  评分: {score:+.1f}")

    print(f"\n评分排名:\n")
    ranked = sorted(signal.scores.items(), key=lambda x: x[1], reverse=True)
    for code, score in ranked[:15]:
        info = etf_map.get(code)
        name = info.name if info else code
        print(f"  {name:<16} ({code})  {score:+.1f}")


def _run_backtest():
    """运行轮动策略回测。"""
    from fund_research.data.etf_fetcher import EtfDataFetcher
    from fund_research.strategy.etf_rotation import EtfRotationStrategy, RotationConfig
    from fund_research.backtest.etf_backtest import EtfRotationBacktest, BacktestConfig

    fetcher = EtfDataFetcher()
    data = fetcher.get_all_ohlcv()
    if not data:
        logger.error("无ETF数据，请先运行 --etf-fetch")
        return

    end = fetcher.get_latest_date() or str(date.today())
    start = str(date.fromisoformat(end) - timedelta(days=365 * 3))  # 3年

    bt_config = BacktestConfig(top_n=5)
    strategy_config = RotationConfig(top_n=5)
    bt = EtfRotationBacktest(bt_config)
    strategy = EtfRotationStrategy(strategy_config)

    result = bt.run(
        data=data,
        strategy=lambda d, dt: strategy.run(d, dt).selected,
        start_date=start,
        end_date=end,
    )
    print()
    print(result.summary())


if __name__ == "__main__":
    main()
