"""Sequoia Fund — 基金研究与量化分析系统

基于 Sequoia-X 选股系统的技术框架，拓展到公募基金/ETF 领域。

支持：
  - 基金净值分析（收益/风险/回撤/夏普）
  - 持仓穿透分析（行业暴露、风格暴露）
  - 指数增强策略回测
  - 行业/风格 ETF 轮动策略
  - 基金筛选与评级
"""

import argparse
import sys
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from fund_research.core.logger import get_logger
from fund_research.core.config import Settings

logger = get_logger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Sequoia Fund — 基金量化分析系统")
    parser.add_argument("--list-funds", action="store_true", help="列出可用的基金列表")
    parser.add_argument("--analyze", type=str, help="分析指定基金代码")
    parser.add_argument("--screen", action="store_true", help="全市场基金筛选")
    parser.add_argument("--backtest", type=str, help="回测策略 (index_enhance/sector_rotation/etf_momentum)")
    parser.add_argument("--fetch-data", action="store_true", help="拉取基金数据")
    args = parser.parse_args()

    if len(sys.argv) == 1:
        parser.print_help()
        return

    logger.info(f"Sequoia Fund 启动 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")

    if args.fetch_data:
        from fund_research.data.fund_fetcher import FundFetcher
        fetcher = FundFetcher()
        fetcher.fetch_all()
        logger.info("数据拉取完成")

    if args.list_funds:
        from fund_research.data.fund_fetcher import FundFetcher
        fetcher = FundFetcher()
        funds = fetcher.get_fund_list()
        for f in funds[:20]:
            logger.info(f"  {f['code']} {f['name']}  {f['type']}")
        logger.info(f"共 {len(funds)} 只基金")

    if args.analyze:
        from fund_research.analysis.nav_analyzer import NavAnalyzer
        analyzer = NavAnalyzer()
        result = analyzer.analyze(args.analyze)
        print(result.summary())

    if args.screen:
        from fund_research.analysis.screener import FundScreener
        screener = FundScreener()
        results = screener.screen()
        for r in results[:20]:
            print(f"{r['rank']:2d}. {r['name']:<20} 评分:{r['score']:.1f}  夏普:{r['sharpe']:.2f}")

    if args.backtest:
        from fund_research.backtest.engine import BacktestEngine
        engine = BacktestEngine()
        result = engine.run(args.backtest)
        print(result.summary())


if __name__ == "__main__":
    main()
