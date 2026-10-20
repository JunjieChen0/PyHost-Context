"""ClawsGO Science Agent：候选 venv 内的网络**拒绝并记录**观察器（装入为 ``sitecustomize.py``）。

规则选择（回应 v3 独立审计第 2 条）：本机不允许创建网络命名空间（``unshare -n`` 返回
``Operation not permitted``），因此**无法做内核级隔离**。在"实现并验证隔离"与"把表述限定为
所覆盖调用的观察"之间，本协议选择**二者结合的有限强制**：

- 对被覆盖的 Python 层调用**拒绝**（fail-closed）：非环回连接与非常用名的域名解析在发起前
  抛出 ``PermissionError``，使"测试期间未发生外部网络访问"由拒绝保证，而非由沉默保证；
- 同时把**覆盖范围、每次拒绝、观察器自身的初始化与结束、以及观察器内部错误**逐条写入
  ``$PYHOST_NETLOG``，使"日志缺失"与"确实没有连接尝试"可区分。

**覆盖范围（必须随结论一并声明）**：

- 覆盖：本 venv 内 Python 进程的 ``socket.socket.connect`` / ``connect_ex`` /
  ``socket.create_connection`` / ``socket.getaddrinfo`` / ``socket.gethostbyname``；
- 不覆盖：非 Python 子进程（curl、git 等）、进程外的原生库直接系统调用、已建立连接上的
  读写、以及内核层面的其他网络路径。

**地址判定（按**套接字地址族**，不用文本形状冒充地址族，也不用文本前缀冒充 IP 校验）**：

- **连接类**（``connect`` / ``connect_ex``）：以 ``sock.family == AF_UNIX`` 判定本地进程间通信，
  一律放行——**包括相对路径名**，因为路径是否以 ``/`` 开头与地址族无关；
  ``create_connection`` 没有套接字对象，按 Python 的约定以地址是否为 ``str`` 区分
  AF_UNIX 与网络地址；其余按网络地址规则判定。
- **解析类**（``getaddrinfo`` / ``gethostbyname``）：**只**用网络地址规则，
  不把以 ``/`` 或 NUL 开头的字符串当作地址族证据。
- **网络地址规则**：IP 字面量用 ``ipaddress`` 解析，仅环回与未指定地址放行；
  域名只允许一份**事前明确的本地名清单**（``localhost`` 等），其余一律拒绝——
  以 ``127.`` 开头的**域名**不是 127.0.0.0/8 中的地址，不得因文本前缀放行。

**观察者效应**：本文件替换上述 socket 方法，属**固定运行时包装**，不是只读查询；该包装对
A 与 B 两个条件完全相同。

未设置 ``PYHOST_NETLOG`` 时本文件完全惰性。任何内部异常都被吞掉并记录，绝不影响被测项目
自身的执行路径（被拒绝的调用除外）。
"""
import ipaddress
import os
import sys

_LOG_PATH_ENV = "PYHOST_NETLOG"

# 事前明确的本地名清单：域名只有落在这里才放行，不做任何前缀或后缀猜测。
_LOCAL_NAMES = frozenset({
    "", "localhost", "localhost.localdomain", "localhost4", "localhost6",
    "ip6-localhost", "ip6-loopback", "0.0.0.0", "::",
})

COVERED_CALLS = ("socket.socket.connect", "socket.socket.connect_ex",
                 "socket.create_connection", "socket.getaddrinfo", "socket.gethostbyname")


def _log_path():
    return os.environ.get(_LOG_PATH_ENV)


def _write(record):
    path = _log_path()
    if not path:
        return
    import json
    import time
    record = dict(record)
    record.setdefault("t", time.time())
    record.setdefault("pid", os.getpid())
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:  # 记录失败不得影响被测进程
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write('{"kind": "observer_error", "phase": "write", "error": "%s"}\n'
                         % type(exc).__name__)
        except Exception:
            pass


def _as_text(target):
    if isinstance(target, bytes):
        try:
            return target.decode("utf-8", "replace")
        except Exception:
            return None
    return target if isinstance(target, str) else None


def _network_host_class(host):
    """**仅用于解析类调用**：把主机名分成 ``empty`` / ``ip`` / ``name`` / ``other``。

    这里刻意不做任何"看起来像路径"的判断——路径不是可解析的主机名，
    字符串形状不能作为地址族的证据。
    """
    if host is None:
        return "empty", None
    text = _as_text(host)
    if text is None:
        return "other", host
    if text == "":
        return "empty", text
    try:
        return "ip", ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return "name", text


def _network_host_allowed(host):
    kind, value = _network_host_class(host)
    if kind == "empty":
        return True
    if kind == "ip":
        return value.is_loopback or value.is_unspecified
    if kind == "name":
        return value.lower() in _LOCAL_NAMES
    return False


def _socket_family(sock):
    try:
        return sock.family
    except Exception:
        return None


def _is_local_ipc(sock, address):
    """连接目标的地址族判定：AF_UNIX 套接字一律是本地 IPC（含相对路径名）。"""
    import socket as _socket
    if sock is not None and _socket_family(sock) == getattr(_socket, "AF_UNIX", None):
        return True
    if sock is None and isinstance(address, (str, bytes)):
        # create_connection 无套接字对象；Python 以 str 地址表示 AF_UNIX。
        return True
    return False


def _host_of(address):
    return address[0] if isinstance(address, (tuple, list)) and address else address


def _deny(kind, target, extra=None):
    record = {"kind": "connection_denied", "call": kind, "target": str(target)}
    if extra:
        record.update(extra)
    _write(record)
    raise PermissionError(
        f"PyHost-Context B0: 非环回网络访问被拒绝（{kind} → {target}）；"
        f"见 PYHOST_NETLOG 中的 connection_denied 记录")


def _allow_local_ipc(kind, target, family_name):
    _write({"kind": "local_ipc_allowed", "call": kind, "target": str(target),
            "family": family_name, "note": "本地进程间通信，不适用网络地址规则"})


def _install():
    if not _log_path():
        return
    import socket

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create_connection = socket.create_connection
    original_getaddrinfo = socket.getaddrinfo
    original_gethostbyname = socket.gethostbyname

    def _family_name(sock):
        try:
            return socket.AddressFamily(_socket_family(sock)).name
        except Exception:
            return str(_socket_family(sock))

    def connect(self, address, *args, **kwargs):
        if _is_local_ipc(self, address):
            _allow_local_ipc("socket.socket.connect", address, _family_name(self))
            return original_connect(self, address, *args, **kwargs)
        if not _network_host_allowed(_host_of(address)):
            _deny("socket.socket.connect", address,
                  {"target_class": _network_host_class(_host_of(address))[0],
                   "family": _family_name(self)})
        _write({"kind": "connection_allowed", "call": "socket.socket.connect",
                "target": str(address)})
        return original_connect(self, address, *args, **kwargs)

    def connect_ex(self, address, *args, **kwargs):
        if _is_local_ipc(self, address):
            _allow_local_ipc("socket.socket.connect_ex", address, _family_name(self))
            return original_connect_ex(self, address, *args, **kwargs)
        if not _network_host_allowed(_host_of(address)):
            _deny("socket.socket.connect_ex", address,
                  {"target_class": _network_host_class(_host_of(address))[0],
                   "family": _family_name(self)})
        _write({"kind": "connection_allowed", "call": "socket.socket.connect_ex",
                "target": str(address)})
        return original_connect_ex(self, address, *args, **kwargs)

    def create_connection(address, *args, **kwargs):
        if _is_local_ipc(None, address):
            _allow_local_ipc("socket.create_connection", address, "AF_UNIX")
            return original_create_connection(address, *args, **kwargs)
        if not _network_host_allowed(_host_of(address)):
            _deny("socket.create_connection", address,
                  {"target_class": _network_host_class(_host_of(address))[0]})
        _write({"kind": "connection_allowed", "call": "socket.create_connection",
                "target": str(address)})
        return original_create_connection(address, *args, **kwargs)

    def getaddrinfo(host, port, *args, **kwargs):
        if not _network_host_allowed(host):
            _deny("socket.getaddrinfo", (host, port),
                  {"target_class": _network_host_class(host)[0]})
        _write({"kind": "resolution_allowed", "call": "socket.getaddrinfo",
                "target": f"{host}:{port}"})
        return original_getaddrinfo(host, port, *args, **kwargs)

    def gethostbyname(host):
        if not _network_host_allowed(host):
            _deny("socket.gethostbyname", host,
                  {"target_class": _network_host_class(host)[0]})
        _write({"kind": "resolution_allowed", "call": "socket.gethostbyname", "target": host})
        return original_gethostbyname(host)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.create_connection = create_connection
    socket.getaddrinfo = getaddrinfo
    socket.gethostbyname = gethostbyname


def _record_end():
    _write({"kind": "observer_end", "phase": "exit", "python": sys.version.split()[0],
            "covered_calls": list(COVERED_CALLS)})


try:
    if _log_path():
        _write({"kind": "observer_init", "phase": "startup",
                "python": sys.version.split()[0],
                "executable": sys.executable,
                "policy": "deny_and_record_non_loopback",
                "covered_calls": list(COVERED_CALLS),
                "not_covered": ["non_python_subprocesses", "native_library_direct_syscalls",
                                "established_connection_io", "kernel_level_paths"]})
        _install()
        import atexit
        atexit.register(_record_end)
except Exception as exc:  # 观察器自身故障必须留痕，且不得影响被测进程
    try:
        _write({"kind": "observer_error", "phase": "install",
                "error": f"{type(exc).__name__}: {exc}"})
    except Exception:
        pass
