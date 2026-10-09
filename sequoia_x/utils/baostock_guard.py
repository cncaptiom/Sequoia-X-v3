"""baostock 连接加固：接收超时 + 断线自动重连。

baostock 0.9.2 的 ``socketutil.send_msg`` 有两个问题：

1. ``recv()`` 未设超时，服务端静默断连时可能永久阻塞（比报错更糟）；
2. 断连异常被 ``except`` 吞掉，只打印 ``[WinError 10054] ...`` /
   ``接收数据异常，请稍后再试。`` 然后返回 ``None``；调用方无法感知失败，
   会静默读到空数据（paging 循环直接判定为“没有更多数据”）。

这里在 ``send_msg`` 这一唯一出口上加固：设置接收超时，失败时 ``logout`` +
``login`` 重连并重发同一请求（baostock 全部请求都是幂等读操作，重发安全），
重试耗尽后仍返回 ``None``、仍打印原有提示，保持与原库一致的外部契约，
因此所有既有调用点无需改动。
"""

import socket
import zlib

_RECV_TIMEOUT = 30.0  # 单次 recv 超时（秒），服务端正常响应远快于此
_MAX_ATTEMPTS = 3  # 含首次在内最多尝试次数
_RETRY_BACKOFF = 1.0  # 重连后等待基数（秒），第 n 次重试等待 n * 基数

_installed = False
_orig_send_msg = None
_in_relogin = False


class BaostockDisconnected(OSError):
    """baostock 连接层失败（服务端重置 / 超时 / 连接关闭）。"""


def _recv_message(sock: socket.socket) -> bytes:
    """读取一条完整报文，直到结尾分隔符。超时或对端关闭时抛异常，不静默返回空。"""
    sock.settimeout(_RECV_TIMEOUT)
    buf = b""
    while True:
        chunk = sock.recv(8192)
        if not chunk:
            raise BaostockDisconnected("连接被服务端关闭")
        buf += chunk
        if buf[-13:] == b"<![CDATA[]]>\n":
            return buf


def _decode_message(raw: bytes) -> str:
    """按原库口径解码：压缩报文返回头+解压后的体，否则整段解码。"""
    import baostock.common.contants as cons

    head = raw[: cons.MESSAGE_HEADER_LENGTH].decode()
    if head.split(cons.MESSAGE_SPLIT)[1] in cons.COMPRESSED_MESSAGE_TYPE_TUPLE:
        inner_length = int(head.split(cons.MESSAGE_SPLIT)[2])
        body = zlib.decompress(
            raw[cons.MESSAGE_HEADER_LENGTH : cons.MESSAGE_HEADER_LENGTH + inner_length]
        ).decode()
        return head + body
    return raw.decode()


def _relogin() -> bool:
    """断线后重新登录。重连期间临时还原原实现，避免递归进入重试逻辑。"""
    global _in_relogin
    if _in_relogin:
        return False
    _in_relogin = True

    import baostock as bs
    import baostock.util.socketutil as su

    try:
        su.send_msg = _orig_send_msg
        try:
            bs.logout()
        except Exception:
            pass
        login_result = bs.login()
        return str(getattr(login_result, "error_code", "")) == "0"
    except Exception:
        return False
    finally:
        su.send_msg = _send_msg
        _in_relogin = False


def _send_msg(msg: str):
    """替代 ``baostock.util.socketutil.send_msg``：超时保护 + 断线自动重连重发。"""
    import time

    import baostock.common.context as context

    last_exc = None
    for attempt in range(_MAX_ATTEMPTS):
        sock = getattr(context, "default_socket", None)
        if sock is None:
            return _orig_send_msg(msg)  # 未登录等边缘情况：交回原实现
        try:
            sock.sendall(bytes(msg + "\n", encoding="utf-8"))
            return _decode_message(_recv_message(sock))
        except (OSError, ValueError, zlib.error) as exc:
            last_exc = exc
            if attempt == _MAX_ATTEMPTS - 1 or not _relogin():
                break
            time.sleep(_RETRY_BACKOFF * (attempt + 1))

    print(last_exc)
    print("接收数据异常，请稍后再试。")
    return None


def install() -> None:
    """安装加固补丁（幂等）。baostock 不可用时静默跳过，不影响本项目其它功能。"""
    global _installed, _orig_send_msg
    if _installed:
        return
    try:
        import baostock.util.socketutil as su
    except ImportError:
        return
    _orig_send_msg = su.send_msg
    su.send_msg = _send_msg
    _installed = True
