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


# ── 错误码层重试 ──
#
# 与上面的 socket 层加固**互补**，不是重复：
#   · 上面那层管传输——recv 超时、对端关闭、断线重发；
#   · 这一层管 baostock **返回非零 error_code** 的情况（socket 没抛异常，但查询失败）。
#
# 这一层原先在 `rating/engine.py` 与 `factors/__init__.py` 各写了一份**完全相同的**
# 重试循环（只差一个节流），错误码集合也各写了一遍——收敛到这里。

# baostock 网络类错误码：连接被服务端断开 / 未登录等
BS_NETWORK_ERRORS = frozenset({
    "10001001", "10002001", "10002002", "10002003", "10002004",
    "10002005", "10002006", "10002007", "10002008",
})

_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF = 1.0  # 第 n 次重试等待 n * 基数（秒）

_last_query_ts = 0.0
"""上一次查询的时刻，供跨调用的节流使用（模块级，所以是跨调用生效的）。"""


def query_with_reconnect(query_fn, *args, throttle_interval: float = 0.0, **kwargs):
    """执行 baostock 查询；连接被服务端断开（WinError 10054 等）时 logout+login 重试。

    网络类错误码（见 `BS_NETWORK_ERRORS`）或抛异常才重连；「无数据」等正常错误码
    直接返回不重试（重试无意义）。

    `throttle_interval > 0` 时，每次尝试前保证距上一次查询至少间隔这么久——
    降低被服务端踢连接的概率。
    """
    global _last_query_ts

    import time

    import baostock as bs

    rs = None
    for attempt in range(_RETRY_ATTEMPTS):
        if throttle_interval > 0:
            wait = _last_query_ts + throttle_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _last_query_ts = time.monotonic()
        try:
            rs = query_fn(*args, **kwargs)
        except Exception:
            rs = None
        if rs is not None and str(getattr(rs, "error_code", "0")) == "0":
            return rs
        err = getattr(rs, "error_code", None) if rs is not None else None
        if not (rs is None or str(err) in BS_NETWORK_ERRORS):
            return rs
        if attempt < _RETRY_ATTEMPTS - 1:
            try:
                bs.logout()
                bs.login()
            except Exception:
                pass
            time.sleep(_RETRY_BACKOFF * (attempt + 1))
    return rs
