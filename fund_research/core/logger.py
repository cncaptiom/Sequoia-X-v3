"""日志模块。复用 Sequoia-X 的日志配置，保持格式统一。"""

import logging
import sys
from datetime import datetime


def get_logger(name: str = __name__) -> logging.Logger:
    """获取配置好的 logger 实例。"""
    logger = logging.getLogger(name)

    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "[%(asctime)s] %(levelname)-8s %(name)-40s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)

    return logger
