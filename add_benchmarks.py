"""一次性脚本：回填基准指数 K 线数据到数据库。"""
import sys
sys.path.insert(0, '.')

from sequoia_x.data.engine import DataEngine
from sequoia_x.core.config import get_settings
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)

# 常用基准指数
BENCHMARKS = {
    "000300": "sh.000300",   # 沪深300
    "000905": "sh.000905",   # 中证500
    "000016": "sh.000016",   # 上证50
    "399006": "sz.399006",   # 创业板指
    "000852": "sh.000852",   # 中证1000
}

settings = get_settings()
engine = DataEngine(settings)

for code, bs_code in BENCHMARKS.items():
    logger.info(f"回填 {bs_code} ({code})...")
    try:
        # 先删除旧数据：000905 曾因 _to_baostock_code 前缀映射错误被写入 sz.000905 的股票数据
        import sqlite3
        with sqlite3.connect(engine.db_path) as conn:
            deleted = conn.execute(
                "DELETE FROM stock_daily WHERE symbol=?", (code,)
            ).rowcount
        if deleted:
            logger.info(f"  已清理 {code} 旧错误数据 {deleted} 行")
        engine.backfill([code], bs_code_map={code: bs_code})
        logger.info(f"  {code} 完成")
    except Exception as e:
        logger.error(f"  {code} 失败: {e}")

logger.info("全部基准指数回填完成")
