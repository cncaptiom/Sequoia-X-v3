"""Sequoia-X-X V2：面向 A 股市场的量化选股系统。"""

# baostock 连接加固（接收超时 + 断线自动重连）。在此统一安装，覆盖全项目所有
# baostock 调用点；详见 sequoia_x/utils/baostock_guard.py。
from sequoia_x.utils.baostock_guard import install as _install_baostock_guard

_install_baostock_guard()
