"""Sequoia-X V2 主程序入口。

运行模式：
  python main.py                          # 日常模式
  python main.py --backfill               # 回填历史K线
  python main.py --rating                 # 评级模式：全市场7大策略评分
  python main.py --install-schedule       # 安装每日21:00定时任务
  python main.py --remove-schedule        # 卸载定时任务
  python main.py --backtest               # 回测模式：策略历史绩效评估
  python main.py --regime                 # 市场状态检测
  python main.py --factors TOP20          # 多因子选股（取排名前20只）
  python main.py --parallel-backfill      # 并行回填（8进程，约3分钟）卸载定时任务

日常模式流程：
  A. 8进程增量补数据 + MaVolume/TurtleTrade 策略选股 + 各自推送
  B. 计算两策略交集，与前日对比，推送新增/持续信息
  C. 从交集评分选出最佳5只，逐只技术分析（支撑/压力/买卖策略），推送详细分析

评级模式流程：
  A. 同步今日数据
  B. 全市场7大策略评分（MaVolume/TurtleTrade/均值回归/量价关系/ATR通道/MACD+RSI/MA交叉）
  C. 5档评级分类（S级/A级/B级/C级/回避）
  D. 飞书推送评级报告
"""
import argparse
import sys
from datetime import datetime, date, timedelta
import os
import subprocess

from dotenv import load_dotenv
load_dotenv()

import socket
socket.setdefaulttimeout(30.0)

from sequoia_x.core.config import get_settings
from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine
from sequoia_x.notify.feishu import FeishuNotifier
from sequoia_x.strategy.base import BaseStrategy
from sequoia_x.strategy.ma_volume import MaVolumeStrategy
from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy
from sequoia_x.strategy.high_tight_flag import HighTightFlagStrategy
from sequoia_x.strategy.limit_up_shakeout import LimitUpShakeoutStrategy
from sequoia_x.strategy.uptrend_limit_down import UptrendLimitDownStrategy
from sequoia_x.strategy.rps_breakout import RpsBreakoutStrategy
from sequoia_x.regime import RegimeDetector, REGIME_LABELS
from sequoia_x.utils.name_resolver import NameResolver
from sequoia_x import cloud

def _check_data_freshness(engine, logger) -> dict:
    """检查数据库中股票数据的新鲜度，返回报告。"""
    from sequoia_x.data.engine import DataValidator
    report = DataValidator.compute_data_health_report(engine.db_path)
    stale = report.get("stale_symbols", 0)
    total = report.get("total_symbols", 0)
    fresh = total - stale
    logger.info(f"数据新鲜度: {fresh}/{total} 只近5个交易日有更新, {stale} 只数据过期")
    if total > 0 and stale > total * 0.3:
        logger.warning(f"超过30%股票数据过期，选股结果可能不准确！建议执行 --backfill 或检查网络")
    return report

def _upload_priority_watchlist(uploader, engine, regime_result=None):
    """上传优先监控列表（Serenity 产业链瓶颈标的）到 COS。"""
    import json, os
    watchlist_path = "data/priority_watchlist.json"
    if not os.path.exists(watchlist_path):
        return False
    try:
        with open(watchlist_path, encoding="utf-8") as f:
            watchlist = json.load(f)
    except Exception:
        return False

    from sequoia_x.utils.name_resolver import get_name_resolver
    resolver = get_name_resolver()

    items = []
    for item in watchlist:
        symbol = item["symbol"]
        df = engine.get_ohlcv(symbol)
        current_price = float(df["close"].iloc[-1]) if not df.empty else 0
        pct_chg = 0
        if len(df) >= 2:
            pct_chg = round((df["close"].iloc[-1] - df["close"].iloc[-2]) / df["close"].iloc[-2] * 100, 1)
        items.append({
            "symbol": symbol,
            "name": resolver.get_name(symbol),
            "reason": item.get("reason", ""),
            "category": item.get("category", ""),
            "current_price": round(current_price, 2),
            "pct_chg": pct_chg,
        })

    data = {
        "date": __import__("time").strftime("%Y-%m-%d"),
        "generated_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "market_regime": regime_result.label if regime_result else "",
        "items": items,
    }
    return uploader._put_json("priority/today.json", data)


def _track_consecutive(top_picks: list[dict]):
    """追踪推荐股票的持续入选天数，更新 top_picks 内每个项的 consecutive_days。

    数据存储在 output/consecutive_tracker.json：
      { "stocks": {"000429": {"days": 5, "last_date": "2026-07-04"}} }

    断档判定：距上次上榜超过 _MAX_STREAK_GAP_DAYS 个自然日（覆盖周末与常见小长假，
    如周五→周一为 3 天）视为中断，days 重置为 1，避免"累计出现天数"虚高
    （如消失一个月再出现仍显示"持续N天"）。
    """
    import json, os
    from datetime import date, timedelta

    # 超过该天数未上榜即视为断档；周末（周五→周一=3天）+ 小长假容差
    _MAX_STREAK_GAP_DAYS = 4

    tracker_path = "output/consecutive_tracker.json"
    tracker: dict = {"stocks": {}}
    if os.path.exists(tracker_path):
        try:
            with open(tracker_path, encoding="utf-8") as f:
                tracker = json.load(f)
        except Exception:
            pass

    today = date.today().isoformat()
    floor = (date.today() - timedelta(days=_MAX_STREAK_GAP_DAYS)).isoformat()
    stocks = tracker.setdefault("stocks", {})

    # 为当前 top_picks 中的每只股票更新持续天数
    symbols_today = set()
    for pick in top_picks:
        sym = pick["symbol"]
        symbols_today.add(sym)
        entry = stocks.get(sym, {"days": 0})
        last = entry.get("last_date")
        if last == today:
            # 同一天多次运行，不重复计数
            pass
        elif last is None or last < floor:
            # 首次上榜或断档超过容忍天数 → 重置为连续第 1 天
            entry["days"] = 1
        else:
            entry["days"] = entry.get("days", 0) + 1
        entry["last_date"] = today
        stocks[sym] = entry
        pick["consecutive_days"] = entry["days"]

    # 按持续天数 + 评分重新排序：持续≥3天的优先，其次按评分
    if top_picks:
        top_picks.sort(key=lambda x: (
            -(x.get("consecutive_days", 1) >= 3),   # 持续≥3天的排前面
            -x.get("consecutive_days", 1),           # 持续天数多的排前面
            -x.get("total_score", 0),                # 同分按评分
        ))

    # 不在今天列表的股票，days 不变（只记录最后出现日期）
    for sym, entry in stocks.items():
        if sym not in symbols_today:
            entry["last_date"] = entry.get("last_date", today)

    os.makedirs("output", exist_ok=True)
    with open(tracker_path, "w", encoding="utf-8") as f:
        json.dump(tracker, f, ensure_ascii=False, indent=2)


def _upload_to_cos(settings, logger, engine, rating_results=None, signals=None, regime_result=None):
    if not settings.cos_enabled:
        logger.info("COS not enabled, skip")
        return False
    bucket = settings.cos_bucket
    region = settings.cos_region
    secret_id = settings.cos_secret_id
    secret_key = settings.cos_secret_key
    if not all([bucket, secret_id, secret_key]):
        logger.warning("COS config incomplete, skip")
        return False
    ok = True
    from sequoia_x.cloud import CosUploader
    uploader = CosUploader(bucket, region, secret_id, secret_key)

    from sequoia_x.rating.engine import RatingConfig
    config = RatingConfig()
    if regime_result:
        config.apply_regime(regime_result.label)

    # 上传评级策略配置
    if not uploader.upload_rating_config(config.to_dict()):
        ok = False

    # 上传市场状态
    if regime_result:
        rd = {
            "label": regime_result.label,
            "regime": regime_result.regime.value if hasattr(regime_result.regime, 'value') else str(regime_result.regime),
            "suggested_exposure": regime_result.suggested_exposure,
            "trend_strength": regime_result.trend_strength,
            "volatility_regime": regime_result.volatility_regime,
            "strategy_bias": regime_result.strategy_bias,
        }
        if not uploader.upload_market_regime(rd):
            ok = False

    # 上传全量评级结果（不限量）
    if rating_results:
        ratings_dict = [r.to_dict() for r in rating_results]
        # 用信号数据的买入区间覆盖评级引擎的（SignalGenerator算法更精确）
        if signals:
            signal_map = {s.symbol: s for s in signals}
            for rd in ratings_dict:
                sig = signal_map.get(rd["symbol"])
                if sig and sig.entry_zone_low > 0 and sig.entry_zone_high > 0:
                    rd["buy_range"] = f"{sig.entry_zone_low:.2f} - {sig.entry_zone_high:.2f}"
                    rd["stop_loss_price"] = sig.stop_loss if sig.stop_loss > 0 else rd.get("stop_loss_price")
                    rd["take_profit_price"] = sig.take_profit_1 if sig.take_profit_1 > 0 else rd.get("take_profit_price")
        regime_data = {"label": regime_result.label} if regime_result else {}
        if not uploader.upload_full_ratings(ratings_dict, config.to_dict(), regime_data):
            ok = False

        # 上传组合摘要（用信号数据而非原始评级结果，确保与操作建议一致）
        if signals:
            # 从评级结果中取真实评分和评级，确保手机端等级（S/A/B/C）和标签一致
            rating_score_map = {r.symbol: r.total_score for r in (rating_results or [])}
            rating_map = {r.symbol: r.rating for r in (rating_results or [])}
            rating_label_map = {r.symbol: r.rating_label for r in (rating_results or [])}
            rating_stars_map = {r.symbol: r.rating_stars for r in (rating_results or [])}
            top_picks = [{
                "symbol": s.symbol, "name": s.name,
                "rating": rating_map.get(s.symbol, "strong_buy" if s.confidence >= 0.75 else "buy" if s.confidence >= 0.60 else "watch"),
                "rating_label": rating_label_map.get(s.symbol, "S级" if s.confidence >= 0.75 else "A级" if s.confidence >= 0.60 else "关注"),
                "rating_stars": rating_stars_map.get(s.symbol, "★★★★★" if s.confidence >= 0.75 else "★★★★" if s.confidence >= 0.60 else "★★★"),
                "total_score": rating_score_map.get(s.symbol, s.confidence * 10),
                "confidence": round(s.confidence, 3),
                "current_price": s.current_price,
            } for s in sorted(signals, key=lambda x: x.confidence, reverse=True)[:10]]
        elif rating_results:
            top_picks = [{
                "symbol": r.symbol, "name": r.name,
                "rating": r.rating, "rating_label": r.rating_label,
                "rating_stars": r.rating_stars,
                "total_score": r.total_score, "confidence": 0.0,
                "current_price": r.current_price,
            } for r in rating_results[:10]]

        # ── 持续天数追踪 ──
        _track_consecutive(top_picks)

        rs = {}
        for r in rating_results or []:
            rs[r.rating] = rs.get(r.rating, 0) + 1
        if not uploader.upload_portfolio_summary(
            top_picks, rs,
            regime_result.label if regime_result else "",
            regime_result.suggested_exposure if regime_result else 0.0,
        ):
            ok = False

        # 上传优先监控列表（Serenity 产业链瓶颈标的）
        _upload_priority_watchlist(uploader, engine, regime_result)

    # 上传完整信号数据（手机端操作建议主数据源）
    if signals:
        rl = regime_result.label if regime_result else ""
        exp = regime_result.suggested_exposure if regime_result else 0.0
        ratings_for_signal = [r.to_dict() for r in rating_results] if rating_results else None
        if not uploader.upload_signals(signals, ratings_for_signal, rl, exp):
            ok = False

    # 上传策略收益率回测数据（如有）
    import os as _os
    bt_path = "output/strategy_backtest.json"
    if _os.path.exists(bt_path):
        import json as _json
        try:
            with open(bt_path, encoding="utf-8") as _f:
                bt_data = _json.load(_f)
            if not uploader._put_json("backtest/strategy_performance.json", bt_data):
                ok = False
        except Exception as _exc:
            logger.warning(f"策略回测数据上传失败: {_exc}")

    logger.info(f"COS sync {'OK' if ok else 'partial fail'}")
    return ok




def _install_schedule() -> None:
    """安装 Windows 每日 21:00 定时任务（SYSTEM 账户，息屏/未登录也可运行）。

    跑的是 `--rating`（生产实际使用的路径），**不是**默认的 `_run_daily`——
    后者是备用回退路径，两条流水线的输出并不相同。
    """
    python_exe = sys.executable
    script_path = os.path.abspath(__file__)
    work_dir = os.path.dirname(script_path)

    cmd = [
        "schtasks", "/create", "/tn", "Sequoia-X选股",
        "/tr", f'cmd /c cd /d "{work_dir}" && "{python_exe}" "{script_path}" --rating',
        "/sc", "daily", "/st", "21:00",
        "/ru", "SYSTEM",
        "/f",
    ]
    try:
        subprocess.run(
            cmd, cwd=work_dir, check=True, capture_output=True, text=True
        )
        print("[OK] 定时任务已安装：每日 21:00 自动运行 python main.py --rating")
        print("[提示] 以 SYSTEM 权限运行，息屏/未登录状态下仍可执行")
    except subprocess.CalledProcessError as exc:
        print(f"[ERROR] 安装定时任务失败: {exc.stderr or exc.stdout}")
        sys.exit(1)


def _remove_schedule() -> None:
    """卸载 Windows 定时任务。"""
    try:
        subprocess.run(
            ["schtasks", "/delete", "/tn", "Sequoia-X选股", "/f"],
            check=True, capture_output=True, text=True,
        )
        print("[OK] 定时任务已卸载")
    except subprocess.CalledProcessError as exc:
        print(f"[ERROR] 卸载定时任务失败: {exc.stderr or exc.stdout}")
        sys.exit(1)


def _run_daily(settings, logger, engine, notifier) -> None:
    """日常模式（**备用回退路径**）。

    入口是「无参数运行 `python main.py`」（main.py 末尾的兜底分支）。
    生产实际跑的是 `--rating`（`_run_rating`），本函数目前未被使用——
    注意它是**唯一**写追踪库（recommendations 表）的路径，所以 `--review`
    依赖本函数是否被执行。详见 docs/code-review-2026-10-13.md 的 M13。
    """
    # ── 数据新鲜度报告 ──
    _check_data_freshness(engine, logger)

    # ── A 阶段：同步数据 + 执行策略 ──
    logger.info("开始拉取最新快照...")
    if not _is_trading_day():
        logger.info("今日非交易日，跳过数据同步")
    else:
        count = engine.sync_today_bulk()
        logger.info(f"快照同步完成，写入 {count} 条数据")

    # -- market regime detection --
    detector = RegimeDetector(engine)
    regime = detector.detect("000905")
    logger.info(f"Regime: {regime.label}, exposure: {regime.suggested_exposure:.0%}, trend: {regime.trend_strength:+.2f}")

    regime_warning = ""
    if regime.suggested_exposure <= 0.30:
        regime_warning = f"[Bear {regime.label} exposure {regime.suggested_exposure:.0%}] "

    # ── 策略分类（与 regime 模块的策略偏向定义一致） ──
    logger.info(f"市场状态: {regime.label} | 策略偏向: {regime.strategy_bias}")

    all_strategies: list[BaseStrategy] = [
        MaVolumeStrategy(engine=engine, settings=settings),
        TurtleTradeStrategy(engine=engine, settings=settings),
        HighTightFlagStrategy(engine=engine, settings=settings),
        LimitUpShakeoutStrategy(engine=engine, settings=settings),
        UptrendLimitDownStrategy(engine=engine, settings=settings),
        RpsBreakoutStrategy(engine=engine, settings=settings),
    ]

    strategy_results: dict[str, list[str]] = {}

    for strategy in all_strategies:
        strategy_name = type(strategy).__name__
        logger.info(f"执行策略：{strategy_name}")
        try:
            selected: list[str] = strategy.run()
            logger.info(f"{strategy_name} 选出 {len(selected)} 只股票")
        except Exception as e:
            logger.error(f"{strategy_name} 执行失败: {e}")
            selected = []
        strategy_results[strategy_name] = selected

    # ── B 阶段：多策略融合评分（按市场状态加权） ──
    from sequoia_x.ensemble import ConsensusEngine
    from sequoia_x.utils.name_resolver import get_name_resolver

    name_resolver = get_name_resolver()

    # 构建 strategy_map: 每只股票被哪些策略选中
    strategy_map: dict[str, list[str]] = {}
    for strat_name, stock_list in strategy_results.items():
        for sym in stock_list:
            if sym not in strategy_map:
                strategy_map[sym] = []
            strategy_map[sym].append(strat_name)

    logger.info(f"全策略选股共 {len(strategy_map)} 只独特股票")

    if not strategy_map:
        logger.warning("所有策略均无选股结果，跳过后续分析")
        return

    # 多策略融合评分
    engine_consensus = ConsensusEngine()
    consensus_results = engine_consensus.score(strategy_map)
    top_consensus = ConsensusEngine.get_top_n(consensus_results, n=50, min_strategies=1)

    logger.info(f"多策略融合评分完成: Top5={[r.symbol for r in top_consensus[:5]]}")

    # 记录各策略选股数量
    strategy_counts = {name: len(stocks) for name, stocks in strategy_results.items()}
    logger.info(f"策略选股数量: {strategy_counts}")

    # 统计多策略确认股票数
    multi_strategy = [s for s in consensus_results if s.strategy_count >= 2]
    logger.info(f"多策略确认(>=2个策略): {len(multi_strategy)} 只")

    # 保存今日融合结果
    import pandas as pd
    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)
    today_str = datetime.now().strftime("%Y%m%d")
    records = []
    for cs in top_consensus:
        name = name_resolver.get_name(cs.symbol)
        records.append({
            "symbol": cs.symbol,
            "name": name,
            "strategies": ",".join(cs.strategies),
            "strategy_count": cs.strategy_count,
            "final_score": cs.final_score,
            "categories": ",".join(cs.categories_hit),
        })
    pd.DataFrame(records).to_csv(f"{output_dir}/Consensus_{today_str}.csv", index=False, encoding="utf-8-sig")
    logger.info(f"融合结果已保存: {len(records)} 只")

    if multi_strategy:
        logger.info(f"多策略确认: {len(multi_strategy)} 只（不单独推送，见推荐报告）")

    # ── C 阶段：精选评分与详细分析（直接用 SignalGenerator 做统一排序） ──
    top5_symbols = [s.symbol for s in top_consensus[:5]]
    logger.info("=" * 50)
    logger.info(f"精选 Top 5 详细分析: {top5_symbols}")
    logger.info("=" * 50)

    if top5_symbols:
        from sequoia_x.factors import FactorCalculator, CrossSectionalRanker

        # 多因子基本面评分
        factor_calc = FactorCalculator(engine)
        factor_results = factor_calc.compute_all(top5_symbols, progress=False)
        ranked_factors = CrossSectionalRanker.rank_and_combine(factor_results)
        factor_map = {r.symbol: r for r in ranked_factors}
        logger.info(f"因子评分完成: {len(ranked_factors)} 只")

        # 逐只技术分析
        for sym in top5_symbols:
            df = engine.get_ohlcv(sym)
            from sequoia_x.analysis.technical import analyze_stock
            tech = analyze_stock(df)
            fr = factor_map.get(sym)
            # 防御：财务因子可能为 None（akshare 间歇性拉取失败），避免格式化崩溃
            factor_str = ""
            if fr is not None:
                try:
                    pe, pb, roe = (float(v) if v is not None else None
                                   for v in (fr.pe, fr.pb, fr.roe))
                    if pe is not None and pb is not None and roe is not None:
                        factor_str = (f" [因子:PE={pe:.1f} PB={pb:.1f} "
                                      f"ROE={roe:.1f}%]")
                except (TypeError, ValueError):
                    factor_str = ""
            logger.info(f"  {sym} | 趋势:{tech['trend']} | 买入:{tech['strategy']['buy_zone']} | 止损:{tech['strategy']['stop_loss']}{factor_str}")

        # 获取名称
        from sequoia_x.utils.name_resolver import get_name_resolver
        name_resolver = get_name_resolver()
        stock_names = name_resolver.get_names(top5_symbols)
        logger.info(f"精选分析完成，详情见推荐报告")

    else:
        logger.info("无候选股票，跳过精选分析")


    # ═══════════════════════════════════════════════
    # D 阶段：生成结构化交易信号 + 推荐报告
    # ═══════════════════════════════════════════════
    logger.info("=" * 50)
    logger.info("生成交易信号与推荐报告...")
    logger.info("=" * 50)

    try:
        from sequoia_x.signal import SignalGenerator
        from sequoia_x.recommender import DISPLAY_TOP_N, ReportGenerator
        from datetime import datetime as _dt

        # strategy_map 已在 B 阶段构建完成，直接使用
        # 所有候选股票：用融合评分结果排序
        # 优先使用已融合评分的 top_consensus
        consensus_symbols = [cs.symbol for cs in top_consensus] if top_consensus else []
        # 同时补充未被融合评分覆盖的策略选股
        all_strategy_symbols = list(strategy_map.keys())
        # 按融合评分排序，未评分的放后面
        all_candidates = list(dict.fromkeys(consensus_symbols + all_strategy_symbols))
        

        # 限制候选数量避免过多
        all_candidates = all_candidates[:50]

        if all_candidates:
            # 生成结构化交易信号
            sig_gen = SignalGenerator(engine, total_capital=1_000_000.0)
            signals = sig_gen.batch_generate(
                symbols=all_candidates,
                strategy_map=strategy_map,
                market_regime=regime.label,
            )

            # 构建推荐报告
            from sequoia_x.signal.models import SignalResult
            report_result = SignalResult(
                signals=signals,
                total_scanned=len(all_candidates),
                market_regime=regime.label,
                suggested_total_exposure=regime.suggested_exposure,
                generated_at=_dt.now().strftime("%Y-%m-%d %H:%M:%S"),
            )

            # 记录推荐到数据库：只记实际推送给用户的那批（与 push_feishu 的
            # DISPLAY_TOP_N 一致），否则命中率统计的分母会和用户看到的名单不符
            from sequoia_x.tracker import RecommendationTracker
            tracker = RecommendationTracker(engine.db_path)
            tracker.record_signals(report_result.top_n(DISPLAY_TOP_N))

            # 终端打印推荐报告
            report_gen = ReportGenerator(notifier)
            report_gen.print_console(report_result)

            # 飞书推送 Top 10 推荐
            report_gen.push_feishu(report_result, webhook_key="ma_volume")

            # 同步到腾讯云COS（供手机端实时监控）
            _upload_to_cos(settings, logger, engine, signals=signals, regime_result=regime)
        else:
            logger.info("无候选股票，跳过信号生成")

    except ImportError as ie:
        logger.warning(f"信号/推荐模块导入失败: {ie}，跳过信号生成")
    except Exception as e:
        logger.warning(f"信号生成异常: {e}")

    _check_data_freshness(engine, logger)
    logger.info("日常模式运行完成")
def _is_trading_day() -> bool:
    """判断今天是否是 A 股交易日。

    1. 先检查是否周末（周六日一定休市）
    2. 再用 baostock 的交易日历 API 验证（含春节/国庆等节假日判断）
    3. 如果 baostock 调用失败，fallback 到周末判断（保守模式）
    """
    from datetime import datetime
    today = datetime.now()
    # 周末一定休市
    if today.weekday() >= 5:
        return False
    try:
        import baostock as bs
        bs.login()
        try:
            rs = bs.query_trade_dates(start_date=today.strftime("%Y-%m-%d"),
                                       end_date=today.strftime("%Y-%m-%d"))
            is_trading = False
            has_data = False
            while rs.next():
                has_data = True
                row = rs.get_row_data()
                if row[1] == "1":  # 1=交易日, 0=非交易日
                    is_trading = True
            # baostock 返回空结果（网络异常或无日历数据），回退到 weekday 判断
            if not has_data:
                return True
            return is_trading
        finally:
            bs.logout()
    except Exception:
        # fallback: 周一至周五认为是交易日（节假日时会有误判，但保守可用）
        return True


def _run_rating(settings, logger, engine, notifier) -> None:
    """评级模式：全市场扫描 + 评级推送。"""
    logger.info("=" * 50)
    logger.info("启动全市场评级模式")
    logger.info("=" * 50)

    from sequoia_x.rating.engine import RatingEngine
    import json, requests

    # 数据新鲜度报告
    _check_data_freshness(engine, logger)

    # 获取本地所有股票
    symbols = engine.get_local_symbols()
    logger.info(f"本地共 {len(symbols)} 只股票")

    if not symbols:
        logger.warning("无本地数据，请先执行 --backfill")
        return

    # 非交易日跳过数据同步
    if not _is_trading_day():
        logger.info("今日非交易日，跳过数据同步")
    else:
        count = engine.sync_today_bulk()
        logger.info(f"数据同步完成，写入 {count} 条")

    # 批量查股票名称（使用 NameResolver 缓存替代重复 baostock 查询）
    from sequoia_x.utils.name_resolver import get_name_resolver
    name_resolver = get_name_resolver()
    stock_names = name_resolver.get_names(symbols)

    # 过滤 ST 股票（退市风险警示，±5%涨跌幅+退市风险，不应参与常规评级）
    st_removed = [s for s in symbols if "ST" in stock_names.get(s, s).upper()]
    if st_removed:
        symbols = [s for s in symbols if s not in st_removed]
        logger.info(f"过滤 ST 股票 {len(st_removed)} 只，剩余 {len(symbols)} 只参与评级")

    # 市场状态检测
    detector = RegimeDetector(engine)
    regime = detector.detect("000905")
    logger.info(f"Regime: {regime.label}, exposure: {regime.suggested_exposure:.0%}")
    notifier.send_regime_alert(regime, webhook_key="rating")

    # 创建评级引擎（根据市场状态动态调整策略权重）
    from sequoia_x.rating.engine import RatingConfig
    rating_config = RatingConfig()
    rating_config.apply_regime(regime.label)
    logger.info(f"策略权重: maVol={rating_config.weight_ma_volume:.2f} turtle={rating_config.weight_turtle_trade:.2f} "
                f"meanRev={rating_config.weight_mean_reversion:.2f} volPrice={rating_config.weight_volume_price:.2f} "
                f"atr={rating_config.weight_atr_channel:.2f} macdRsi={rating_config.weight_macd_rsi:.2f} "
                f"maCross={rating_config.weight_ma_cross:.2f} kdj={rating_config.weight_kdj:.2f} "
                f"fund={rating_config.weight_fundamental:.2f}")
    rating_engine = RatingEngine(config=rating_config, data_engine=engine)

    # IC 追踪（信号质量监控）
    from sequoia_x.signal.quality import ICTracker
    ic_tracker = ICTracker()

    # 全市场扫描（数据走复权安全加载：跳过最近含未复权跳变的股票）
    all_results = rating_engine.scan_market(
        symbols=symbols,
        data_loader=lambda s: engine.get_ohlcv_rating_safe(s),
        top_n=99999,
        min_bars=60,
        ic_tracker=ic_tracker,
    )
    logger.info(f"评级完成，上榜 {len(all_results)} 只")

    # 计算 IC（处理 pending 快照）
    ic_tracker.eval_forward_returns(engine, lookforward_days=5)
    ic_tracker.eval_forward_returns(engine, lookforward_days=20)

    # 打印 IC 质量报告
    ic_tracker.print_summary()

    # 填充股票名称
    for r in all_results:
        if not r.name:
            r.name = stock_names.get(r.symbol, r.symbol)

    # 统计
    strong_buy = sum(1 for r in all_results if r.rating == "strong_buy")
    buy = sum(1 for r in all_results if r.rating == "buy")
    logger.info(f"强烈买入: {strong_buy}, 买入: {buy}")

    # 评级统计已合并到操作建议卡片中，不再单独推送评级报告
    # 导出 JSON 到 output/（供 gpfxAPP 导入）
    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)
    today_str = datetime.now().strftime("%Y%m%d")
    export = []
    for r in all_results:
        export.append({
            "code": r.symbol,
            "name": getattr(r, "name", "") or stock_names.get(r.symbol, r.symbol),
            "rating": r.rating,
            "rating_label": r.rating_label,
            "total_score": r.total_score,
            "strategy_scores": r.strategy_scores,
            "per_strategy_scores": r.per_strategy_scores,
            "category_scores": r.category_scores,
            "current_price": r.current_price,
            "stop_loss": r.stop_loss_price,
            "take_profit": r.take_profit_price,
            "risk_reward_ratio": r.risk_reward_ratio,
            "buy_range": r.buy_range,
        })
    import json
    json_path = f"{output_dir}/rating_result_{today_str}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False, indent=2)
    logger.info(f"评级结果已导出: {json_path} ({len(export)} 只)")

    # ── 综合推荐报告：按 (评级类别, 总分) 取前 15 名，全部推送操作建议 ──
    # 不再只筛选 strong_buy/buy，所有有评分的股票按总分排名展示
    # 这样用户可以看到从"强烈买入"到"关注"的完整梯队
    #
    # 第 15 名常有大量同分股票，直接 all_results[:15] 会按输入顺序任意截断
    # （名单由股票列表顺序决定，输入集合一变就可能变）。
    # 因此先放宽候选池到「第 15 名及以上 + 所有同评级同分者」，
    # 等 batch_generate 算出置信度后，再用置信度作为并列时的二级排序键截断。
    # 展示/推送条数：与 push_feishu 的 DISPLAY_TOP_N 共用同一常量，避免两处 15 各自漂移
    from sequoia_x.recommender.reporter import DISPLAY_TOP_N

    POOL_CAP = 200  # 防御：极端并列时不让候选池无限膨胀
    RATING_ORDER = {"strong_buy": 0, "buy": 1, "watch": 2, "attention": 3, "avoid": 4}

    def _production_key(r) -> tuple:
        return (RATING_ORDER.get(r.rating, 5), -r.total_score)

    if len(all_results) >= DISPLAY_TOP_N:
        cut_key = _production_key(all_results[DISPLAY_TOP_N - 1])
        candidates = [r for r in all_results if _production_key(r) <= cut_key][:POOL_CAP]
    else:
        candidates = list(all_results)
    logger.info(f"推荐候选池: {len(candidates)} 只（含并列同分者）")

    if candidates:
        # 构建 strategy_map: {symbol: [positive_score_strategies]}
        strategy_map = {}
        strategy_key_map = {
            "maVolume": "MaVolumeStrategy", "turtleTrade": "TurtleTradeStrategy",
            "meanReversion": "MeanReversionStrategy", "volumePrice": "VolumePriceStrategy",
            "atrChannel": "AtrChannelStrategy", "macdRsi": "MacdRsiStrategy",
            "maCross": "MaCrossStrategy",
        }
        for r in candidates:
            trigger_strats = []
            for sk, sv in r.strategy_scores.items():
                if sv > 0:
                    trigger_strats.append(strategy_key_map.get(sk, sk))
            if not trigger_strats:
                trigger_strats = ["RatingEngine"]
            strategy_map[r.symbol] = trigger_strats

        # 使用 SignalGenerator 生成完整TradeSignal（优先方案）
        from sequoia_x.signal.generator import SignalGenerator
        from sequoia_x.recommender.reporter import ReportGenerator
        from sequoia_x.signal.models import SignalResult, TradeSignal

        gen = SignalGenerator(engine)
        signals = gen.batch_generate(
            symbols=[r.symbol for r in candidates],
            strategy_map=strategy_map,
            market_regime=regime.label,
        )

        # 如果SignalGenerator返回空（数据过期/不足），从AnalysisResult直接构建
        if not signals:
            logger.info("SignalGenerator返回空，直接从AnalysisResult构建TradeSignal")
            signals = []
            for r in candidates:
                entry_low = entry_high = 0.0
                if r.buy_range:
                    try:
                        parts = r.buy_range.replace(" ", "").split("-")
                        if len(parts) == 2:
                            entry_low = float(parts[0])
                            entry_high = float(parts[1])
                    except (ValueError, IndexError):
                        pass
                if entry_low <= 0 or entry_high <= 0:
                    entry_low = r.current_price * 0.97
                    entry_high = r.current_price * 1.01
                stop_loss = r.stop_loss_price or round(entry_low * 0.95, 2)
                tp1 = r.take_profit_price or round(entry_high * 1.08, 2)
                tp2 = round(entry_high * 1.15, 2)
                pos_pct = 0.08 if r.rating == "strong_buy" else 0.05
                strat_cnt = sum(1 for v in r.strategy_scores.values() if v > 0)
                conf_score = 0.0
                # 综合评分.docx 规则：
                # 1. 多策略确认（满分0.25）
                if strat_cnt >= 3: conf_score += 0.25
                elif strat_cnt == 2: conf_score += 0.15
                else: conf_score += 0.10
                # 2. 趋势走好（满分0.20）
                if r.trend_passed: conf_score += 0.20
                # 3. 成交量活跃（满分0.15）
                active_strat_ratio = strat_cnt / max(len(r.strategy_scores), 1)
                if active_strat_ratio >= 0.5: conf_score += 0.15
                elif active_strat_ratio >= 0.3: conf_score += 0.08
                # 4. 接近支撑位（满分0.15）
                if r.stop_loss_price and r.current_price > 0:
                    proximity = 1.0 - abs(r.current_price - r.stop_loss_price) / (r.current_price * 0.15)
                    if proximity > 0.7: conf_score += 0.15
                    elif proximity > 0.4: conf_score += 0.08
                # 5. 低波动环境（满分0.10）
                if r.total_score > 0: conf_score += 0.05
                # 6. 量价共振加分（满分0.10）
                if r.trend_passed and active_strat_ratio >= 0.5:
                    conf_score += 0.10
                # 截断到1.0
                confidence = round(max(min(conf_score, 1.0), 0.05), 3)
                sname = r.name or stock_names.get(r.symbol, r.symbol)
                sig = TradeSignal(
                    symbol=r.symbol, name=sname,
                    strategies=list(r.strategy_scores.keys()) if r.strategy_scores else ["rating"],
                    current_price=r.current_price,
                    entry_zone_low=round(entry_low, 2),
                    entry_zone_high=round(entry_high, 2),
                    entry_condition=f"现价{r.current_price:.2f}，买入区间{entry_low:.2f}~{entry_high:.2f}",
                    stop_loss=round(stop_loss, 2),
                    take_profit_1=round(tp1, 2),
                    take_profit_2=round(tp2, 2),
                    suggested_position_pct=pos_pct,
                    confidence=confidence,
                    score_details=r.strategy_scores or {},
                    market_regime=regime.label,
                    rationale=f"评级{r.rating_label}，总分{r.total_score:.1f}，{sname}进入买入区间",
                )
                signals.append(sig)

        # 最终名单：先按 (评级类别, 总分)，并列时再按置信度降序，取前 DISPLAY_TOP_N。
        # batch_generate 只按置信度排过序，这里把评级维度补回来，
        # 否则同分股票的顺序会由置信度而不是评级决定。
        pool_key = {r.symbol: _production_key(r) for r in candidates}
        signals.sort(key=lambda s: (pool_key.get(s.symbol, (9, 0.0)), -s.confidence))
        signals = signals[:DISPLAY_TOP_N]
        logger.info(f"推送名单 {len(signals)} 只（排序：评级 → 总分 → 置信度）")

        # 记录推荐到数据库：与飞书推送的名单保持一致，
        # 否则 --review 的命中率统计没有数据源（--rating 是生产实际跑的路径）
        from sequoia_x.tracker import RecommendationTracker
        RecommendationTracker(engine.db_path).record_signals(signals)

        # 评级分布统计
        rating_dist = {"strong_buy": 0, "buy": 0, "watch": 0, "attention": 0, "avoid": 0}
        for r in all_results:
            rating_dist[r.rating] = rating_dist.get(r.rating, 0) + 1

        # 推送推荐报告（合并评级统计信息）
        report_result = SignalResult(
            signals=signals,
            total_scanned=len(symbols),
            market_regime=regime.label,
            suggested_total_exposure=regime.suggested_exposure,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            rating_summary=rating_dist,
        )
        report_gen = ReportGenerator(notifier)
        report_gen.push_feishu(report_result, webhook_key="rating")
        logger.info(f"综合推荐报告已推送到飞书: {len(signals)} 只")

        # 同步到腾讯云COS（同时上传评级结果和推荐信号）
        try:
            _upload_to_cos(settings, logger, engine, rating_results=all_results, signals=signals if signals else None, regime_result=regime)
        except Exception as exc:
            logger.warning(f"COS upload failed: {exc}")


def _run_backtest(settings, logger, engine) -> None:
    """回测模式：对所有策略进行历史回测并输出绩效报告。"""
    from sequoia_x.backtest.engine import BacktestEngine, BacktestConfig
    from sequoia_x.backtest.metrics import PerformanceMetrics
    from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy
    from sequoia_x.regime import RegimeDetector, REGIME_LABELS
    from sequoia_x.strategy.ma_volume import MaVolumeStrategy
    from sequoia_x.strategy.high_tight_flag import HighTightFlagStrategy
    from sequoia_x.strategy.limit_up_shakeout import LimitUpShakeoutStrategy
    from sequoia_x.strategy.uptrend_limit_down import UptrendLimitDownStrategy
    from sequoia_x.strategy.rps_breakout import RpsBreakoutStrategy
    from datetime import date, timedelta
    import json, os

    symbols = engine.get_local_symbols()
    if not symbols:
        logger.error("无本地数据，请先执行 --backfill")
        return

    end_date = date.today().strftime("%Y-%m-%d")
    start_date = (date.today() - timedelta(days=365)).strftime("%Y-%m-%d")
    logger.info(f"回测区间: {start_date} ~ {end_date}")

    config = BacktestConfig(initial_capital=1_000_000)
    bt_engine = BacktestEngine(engine, config)

    # 所有策略配置
    strategies_to_test = [
        ("TurtleTrade 海龟突破", TurtleTradeStrategy, {"settings": settings}),
        ("MaVolume 均线放量", MaVolumeStrategy, {"settings": settings}),
        ("HighTightFlag 高窄旗形", HighTightFlagStrategy, {"settings": settings}),
        ("LimitUpShakeout 涨停洗盘", LimitUpShakeoutStrategy, {}),
        ("UptrendLimitDown 上升跌停", UptrendLimitDownStrategy, {}),
        ("RpsBreakout RPS突破", RpsBreakoutStrategy, {}),
    ]

    all_reports = {}
    for name, strategy_cls, kwargs in strategies_to_test:
        logger.info("=" * 40)
        logger.info(f"回测: {name}")
        logger.info("=" * 40)
        try:
            result = bt_engine.run(
                strategy_cls, symbols, start_date, end_date,
                strategy_kwargs=kwargs,
            )
            metrics = PerformanceMetrics.from_trades(result.trades, result.equity_curve)
            logger.info(metrics.summary())
            logger.info(f"夏普: {metrics.sharpe_ratio:.2f} | 索提诺: {metrics.sortino_ratio:.2f}")
            logger.info(f"年化收益: {metrics.annual_return:.1%} | 最大回撤: {metrics.max_drawdown:.1%}")
            logger.info(f"胜率: {metrics.win_rate:.1%} | 交易次数: {metrics.total_trades}")
            all_reports[name] = metrics.to_dict()
        except Exception as e:
            logger.error(f"回测 {name} 失败: {e}")
            all_reports[name] = {"error": str(e)}

    # 导出汇总报告
    os.makedirs("output", exist_ok=True)
    report = {
        "date": date.today().strftime("%Y-%m-%d"),
        "start": start_date,
        "end": end_date,
        "strategies": all_reports,
    }
    path = f"output/backtest_{date.today().strftime('%Y%m%d')}.json"
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)
    logger.info(f"回测汇总报告已导出: {path}")


def _run_regime_check(logger, engine) -> None:
    """市场状态检测模式。"""
    from sequoia_x.regime import RegimeDetector
    detector = RegimeDetector(engine)
    regime = detector.detect("000905")
    logger.info("=" * 40)
    logger.info(f"当前市场状态: {regime.label}")
    logger.info(f"建议总仓位: {regime.suggested_exposure:.0%}")
    logger.info(f"趋势强度: {regime.trend_strength:.2f}")
    logger.info(f"波动率状态: {regime.volatility_regime} (P{regime.volatility_pct:.0%})")
    logger.info(f"策略偏向: {regime.strategy_bias}")
    logger.info(f"详情: {regime.details}")


def _run_factors(settings, logger, engine, top_n_str: str) -> None:
    """多因子选股模式。"""
    from sequoia_x.factors import FactorCalculator, CrossSectionalRanker, FactorConfig
    import json, os

    # 解析 top_n
    try:
        top_n = int(top_n_str.replace("TOP", ""))
    except ValueError:
        top_n = 20

    symbols = engine.get_local_symbols()
    if not symbols:
        logger.error("无本地数据，请先执行 --backfill")
        return

    # 过滤 ST 股票（与评级流程一致）
    from sequoia_x.utils.name_resolver import get_name_resolver
    before = len(symbols)
    symbols = get_name_resolver().filter_st(symbols)
    if len(symbols) != before:
        logger.info(f"过滤 ST 股票 {before - len(symbols)} 只，剩余 {len(symbols)} 只参与多因子扫描")

    logger.info(f"开始多因子扫描 {len(symbols)} 只股票...")
    calc = FactorCalculator(engine)
    factor_results = calc.compute_all(symbols, progress=True)
    ranked = CrossSectionalRanker.rank_and_combine(factor_results)

    # 飞书推送多因子结果
    notifier = FeishuNotifier(settings)
    notifier.send_factor_results(ranked, total_scanned=len(symbols), top_n=top_n)

    top_stocks = ranked[:top_n]
    logger.info(f"多因子选股 Top {top_n}:")
    for i, r in enumerate(top_stocks, 1):
        logger.info(
            f"  {i:2d}. {r.symbol} | 总分:{r.composite_score:.3f} | "
            f"价值:{r.factor_percentiles.get('value',0):.2f} "
            f"动量:{r.factor_percentiles.get('momentum',0):.2f} "
            f"质量:{r.factor_percentiles.get('quality',0):.2f}"
        )

    # 导出
    os.makedirs("output", exist_ok=True)
    from datetime import date
    export = []
    for r in ranked[:max(top_n, 50)]:
        export.append({
            "symbol": r.symbol,
            "composite_score": r.composite_score,
            "factor_percentiles": r.factor_percentiles,
            "momentum_12m1": r.momentum_12m1,
            "pe": r.pe, "pb": r.pb, "roe": r.roe,
        })
    path = f"output/factors_{date.today().strftime('%Y%m%d')}.json"
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(export, fp, ensure_ascii=False, indent=2)
    logger.info(f"多因子结果已导出: {path}")



def _run_verify_data(settings, logger, engine) -> None:
    """抽样比对库内最新收盘价与 baostock 前复权收盘价。"""
    from sequoia_x.data.engine import DataValidator

    symbols = engine.get_local_symbols()
    logger.info(f"数据一致性验证: {len(symbols)} 只股票")

    result = DataValidator.verify_prices(
        db_path=engine.db_path, symbols=symbols, sample_size=min(200, len(symbols))
    )

    sep = "=" * 60
    print(f"\n{sep}")
    print("  数据一致性验证结果（库内最新收盘 vs baostock 前复权）")
    print(sep)
    print(f"  抽样: {result['total']} 只")
    print(f"  比对: {result['compared']} 只")
    print(f"  通过: {result['passed']} 只")
    print(f"  异常: {result['failed']} 只")
    print(f"  跳过: {result['skipped']} 只（库内无数据或 baostock 取不到）")
    if result["passed_pct"] is None:
        print("  警告: 没有任何一条真正比对成功，结论是『未验证』而不是『一致』")
    else:
        print(f"  一致率: {result['passed_pct']}%（分母为已比对数 {result['compared']}）")
    if result['issues']:
        print("\n  异常详情（差异 >1%）:")
        for issue in result['issues'][:10]:
            print(f"    {issue['symbol']} {issue['date']}: 库内={issue['db_close']} "
                  f"baostock={issue['baostock_close']} 差异={issue['diff_pct']}%")
        if len(result['issues']) > 10:
            print(f"    ... 共 {len(result['issues'])} 只异常")
    print(sep)

def _run_review(settings, logger, engine) -> None:
    """回溯模式：检查历史推荐命中率并推送到飞书。"""
    from sequoia_x.tracker import RecommendationTracker
    from datetime import date
    import requests, json

    tracker = RecommendationTracker(engine.db_path)
    stats = tracker.get_stats()

    logger.info("=" * 50)
    logger.info("推荐命中率回溯")
    logger.info("=" * 50)

    if stats:
        logger.info(f"总推荐: {stats['total']} 条")
        logger.info(f"胜率: {stats['wins']}/{stats['total']} ({stats['win_rate']}%)")
        logger.info(f"平均收益: {stats['avg_return']}%")

        emoji = "🟢" if stats["win_rate"] >= 40 else "🔴"
        card = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": f"{emoji} Sequoia-X 推荐回溯报告"},
                    "template": "blue",
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": (
                        f"**回溯统计**\n"
                        f"总推荐: {stats['total']} 条\n"
                        f"胜率: {stats['wins']}/{stats['total']} ({stats['win_rate']}%)\n"
                        f"平均收益: {stats['avg_return']}%\n"
                        f"触及止损: {stats['hit_stop_loss']} 次\n"
                        f"触及目标: {stats['hit_target_1']} 次"
                    )}},
                ],
            },
        }
        try:
            url = settings.get_webhook_url("ma_volume")
            requests.post(url, data=json.dumps(card), headers={"Content-Type": "application/json"}, timeout=10)
            logger.info("回溯报告已推送到飞书")
        except Exception as exc:
            logger.error(f"推送失败: {exc}")
    else:
        logger.info("暂无已回溯的推荐记录")

    results = tracker.review_pending(engine)
    if results:
        logger.info(f"本轮回溯 {len(results)} 条")
        wins = sum(1 for r in results if r["actual_return"] > 0)
        logger.info(f"  胜率: {wins}/{len(results)} ({wins/len(results)*100:.0f}%)")
    logger.info("回溯完成")

def main() -> None:
    parser = argparse.ArgumentParser(description="Sequoia-X V2 选股系统")
    parser.add_argument(
        "--backfill", action="store_true", help="回填模式：全市场历史K线"
    )
    parser.add_argument(
        "--install-schedule", action="store_true",
        help="安装每日 21:00 Windows 定时任务",
    )
    parser.add_argument(
        "--remove-schedule", action="store_true",
        help="卸载定时任务",
    )
    parser.add_argument(
        "--rating", action="store_true",
        help="评级模式：全市场 7 大策略评分 + 评级推送",
    )
    parser.add_argument(
        "--backtest", action="store_true",
        help="回测模式：策略历史绩效评估",
    )
    parser.add_argument(
        "--regime", action="store_true",
        help="市场状态检测：判断牛熊震荡",
    )
    parser.add_argument(
        "--factors", type=str, nargs="?", const="TOP20",
        help="多因子选股（取排名前N只）",
    )
    parser.add_argument(
        "--parallel-backfill", action="store_true",
        help="并行回填历史数据（8进程）",
    )
    parser.add_argument(
        "--review", action="store_true",
        help="回溯模式：检查历史推荐命中率并推送报告",
    )
    parser.add_argument(
        "--verify-data", action="store_true",
        help="验证数据一致性：抽样对比库内收盘价与 baostock 前复权收盘价",
    )
    parser.add_argument(
        "--refresh-history", action="store_true",
        help="定向重刷历史：仅重刷最近存在>21%未复权跳变的股票（修复前复权过期）",
    )
    parser.add_argument(
        "--refresh-history-all", action="store_true",
        help="全量重刷历史：重刷全部股票（耗时数小时，建议非交易时段执行）",
    )
    args = parser.parse_args()

    # 调度相关命令单独处理（无需初始化整个系统）
    if args.install_schedule:
        _install_schedule()
        return
    if args.remove_schedule:
        _remove_schedule()
        return

    try:
        settings = get_settings()
        logger = get_logger(__name__)
        logger.info("Sequoia-X V2 启动")

        engine = DataEngine(settings)

        if args.backfill:
            logger.info("进入回填模式...")
            all_symbols = engine.get_all_symbols()
            engine.backfill(all_symbols)
            logger.info("Sequoia-X V2 回填模式运行完成")
            return

        if args.rating:
            _run_rating(settings, logger, engine, FeishuNotifier(settings))
            return

        if args.backtest:
            _run_backtest(settings, logger, engine)
            return

        if args.regime:
            _run_regime_check(logger, engine)
            return

        if args.factors is not None:
            _run_factors(settings, logger, engine, args.factors)
            return

        if args.review:
            _run_review(settings, logger, engine)
            return

        if args.verify_data:
            _run_verify_data(settings, logger, engine)
            return

        if args.parallel_backfill:
            logger.info("进入并行回填模式...")
            all_symbols = engine.get_all_symbols()
            engine.backfill_parallel(all_symbols)
            logger.info("Sequoia-X V3 并行回填完成")
            return

        if args.refresh_history or args.refresh_history_all:
            local_set = set(engine.get_local_symbols())
            if args.refresh_history_all:
                symbols = sorted(local_set)
                logger.info(f"全量重刷 {len(symbols)} 只股票（可能耗时数小时）...")
            else:
                gap_symbols = engine.find_recent_gap_symbols()
                symbols = [s for s in gap_symbols if s in local_set]
                logger.info(
                    f"检测到 {len(gap_symbols)} 只含未复权跳变的股票，定向重刷其中 {len(symbols)} 只 A 股..."
                )
            engine.refresh_history(symbols)
            logger.info("重刷完成")
            return

        _run_daily(settings, logger, engine, FeishuNotifier(settings))

    except Exception:
        try:
            _logger = get_logger(__name__)
            _logger.exception("主流程发生未捕获异常，程序终止")
        except Exception:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    logger.info("Sequoia-X V2 运行完成")


if __name__ == "__main__":
    main()

