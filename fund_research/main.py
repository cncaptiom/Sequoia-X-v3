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
    parser.add_argument("--etf-push", action="store_true", help="推送本轮轮动信号到飞书")
    parser.add_argument("--fund-screen", action="store_true", help="全市场基金筛选（按近1年收益）")
    parser.add_argument("--fund-satellite", action="store_true", help="卫星配置筛选（有动量但不过热）")
    parser.add_argument("--fund-type", type=str, default="全部", help="基金类型：股票型/混合型/指数型")
    parser.add_argument("--top-n", type=int, default=20, help="返回前N只")
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

    if args.etf_push:
        _run_push()

    if args.fund_screen:
        _run_fund_screen(args)

    if args.fund_satellite:
        _run_fund_satellite(args)


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


def _run_push():
    """推送ETF轮动信号到飞书。"""
    import os
    webhook = os.getenv("FEISHU_ETF_WEBHOOK", "")
    if not webhook:
        logger.warning("未设置 FEISHU_ETF_WEBHOOK 环境变量，跳过推送")
        return

    from fund_research.data.etf_fetcher import EtfDataFetcher
    from fund_research.strategy.etf_rotation import EtfRotationStrategy, RotationConfig
    from fund_research.notify.feishu import FeishuNotifier
    from fund_research.data.etf_list import get_etf_map
    from fund_research.analysis.screener import FundScreener

    fetcher = EtfDataFetcher()
    data = fetcher.get_all_ohlcv()
    if not data:
        logger.error("无ETF数据")
        return

    latest = fetcher.get_latest_date()
    if not latest:
        return

    strategy = EtfRotationStrategy(RotationConfig())
    signal = strategy.run(data, latest)
    etf_map = get_etf_map()

    # 打印到控制台
    print(f"\nETF轮动信号 ({signal.date}):")
    for i, code in enumerate(signal.selected, 1):
        info = etf_map.get(code)
        name = info.name if info else code
        score = signal.scores.get(code, 0)
        print(f"  {i}. {name} ({code})  {score:+.1f}")

    # ── 获取卫星基金（有动量但不过热） ──
    satellite = []
    try:
        screener = FundScreener()
        satellite = screener.screen_satellite(top_n=3)
        if satellite:
            print(f"\n卫星配置推荐（有动量但不过热）:")
            for f in satellite:
                print(f"  {f['name']} ({f['code']})  "
                      f"近1年+{f['return_1y']:.1f}%  "
                      f"近3月+{f['return_3m']:.1f}%  "
                      f"评分{f['_score']}")
    except Exception as e:
        logger.warning(f"卫星基金获取失败: {e}")

    # 推送到飞书（含卫星配置）
    notifier = FeishuNotifier(webhook)
    notifier.send_rotation_signal(signal, etf_map, satellite_funds=satellite)
    logger.info("推送完成")


def _run_fund_screen(args):
    """全市场基金筛选。"""
    from fund_research.analysis.screener import FundScreener

    screener = FundScreener()
    funds = screener.screen(
        min_return_1y=0.0,
        min_return_3y=0.0,
        fund_type=args.fund_type,
        top_n=args.top_n,
    )

    if not funds:
        logger.warning("无筛选结果，可能数据源暂不可用")
        return

    screener.print_result(funds)


def _run_fund_satellite(args):
    """卫星配置筛选。"""
    from fund_research.analysis.screener import FundScreener

    screener = FundScreener()
    funds = screener.screen_satellite(top_n=args.top_n if args.top_n else 5)
    screener.print_satellite_result(funds)


if __name__ == "__main__":
    main()
