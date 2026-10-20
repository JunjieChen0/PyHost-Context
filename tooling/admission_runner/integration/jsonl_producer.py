"""PyHost-Context 准入阶段：真实 pytest 原始 JSONL **producer**（pytest 插件）。

角色：作为 pytest 插件挂到真实子进程上，把 **native** pytest 报告如实翻译为
``pytest_jsonl.py`` 所解析的原始 JSONL schema，逐行写入 ``$PYHOST_JSONL_PATH``。
只消费合成 fixture 的运行结果，不涉及任何候选项目。

产出的记录类型：

- ``CollectReport``：**所有** native collection 报告（root/package/module…）原样保留；
- ``CollectionItem``：每个最终被选中的测试项一条（来自 ``session.items``）；**不**把 item
  伪造成 CollectReport；
- ``CollectionFinished``：collection 结束时的身份/顺序摘要（含 ``collected`` 顺序与指纹）；
- ``DeselectedReport``：被 ``-k``/``-m`` 等取消选择的测试项；
- ``TestReport``：逐测试 setup/call/teardown 的 native 报告，含 ``when``/``outcome``/
  ``longrepr`` 与 **native** ``wasxfail``（存在才写，绝不伪造）；
- ``RuntimeSnapshot``：``pytest_configure`` 时的进程内**只读**快照（flags/cwd/FD/env/
  locale/tz/hash/pytest 版本/实际插件清单/TTY 与 capture 明细/每轮绑定元数据；
  无 ``tzset``/``setlocale`` 修改）；
- ``SessionFinished``：``pytest_sessionfinish`` 时的会话结束标记（exitstatus 等）。

关键约定：

- 严格 XPASS 保持 native ``call outcome=failed`` 与原 ``longrepr``，**不改写 outcome、
  不删除 longrepr、不伪造 wasxfail**；派生标签由 parser 依 native ``[XPASS(strict)]`` 得出。
- JSONL 以**独占创建**（``open(..., "x")``）打开，绝不覆盖既有文件。
"""
import hashlib
import json
import os

import pytest

try:  # 与 parser 共用同一指纹函数；插件被独立加载时退回等价实现。
    from pytest_jsonl import identity_fingerprint as _identity_fingerprint
except Exception:  # pragma: no cover - 仅在缺路径时触发
    def _identity_fingerprint(nodeids):
        payload = json.dumps(list(nodeids), ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

JSONL_ENV_VAR = "PYHOST_JSONL_PATH"
ROUND_META_ENV_VAR = "PYHOST_ROUND_META"

_handle = None


def pytest_configure(config):
    """以独占方式打开输出文件并写 RuntimeSnapshot；未设置环境变量时插件完全惰性。"""
    global _handle
    path = os.environ.get(JSONL_ENV_VAR)
    if not path:
        return
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    _handle = open(path, "x", encoding="utf-8")
    _emit(_runtime_snapshot(config))


def pytest_unconfigure(config):
    global _handle
    if _handle is not None:
        _handle.close()
        _handle = None


def _emit(record):
    if _handle is None:
        return
    _handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    _handle.flush()


def _safe_flags():
    """``sys.flags`` 只保留数据型字段。

    原实现直接 ``getattr(sys.flags, name)`` 配 ``default=str``，把 ``count``/``index``
    两个具名元组方法序列化成 ``<built-in method ... at 0x...>``——既非数据，又含内存地址，
    使 ``snapshot_hash`` 不可复现（独立审计 F02）。此处只保留 int/str/float/bool。
    """
    import sys
    out = {}
    for name in dir(sys.flags):
        if name.startswith("_"):
            continue
        try:
            value = getattr(sys.flags, name)
        except Exception:
            continue
        if isinstance(value, (bool, int, float, str)):
            out[name] = value
    return out


def _plugin_inventory(config):
    """实际加载的插件清单（名字、来源模块文件、文件 SHA-256）。"""
    inventory = []
    try:
        plugins = list(config.pluginmanager.list_name_plugin())
    except Exception:
        return inventory
    for name, plugin in plugins:
        module = getattr(plugin, "__name__", None)
        if module is None:
            module = getattr(getattr(plugin, "__class__", object), "__module__", None)
        entry = {"name": name, "module": module}
        path = getattr(plugin, "__file__", None)
        if path:
            entry["file"] = path
            try:
                with open(path, "rb") as fh:
                    entry["sha256"] = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                pass
        inventory.append(entry)
    return sorted(inventory, key=lambda item: (item["name"], item.get("module") or ""))


def _tty_detail():
    """标准流的 TTY 与重定向明细：既有协议要求的 TTY/capture 证据。"""
    import os
    import sys
    detail = {"isatty": {}, "fd_target": {}}
    for label, stream, fd in (("stdin", sys.stdin, 0), ("stdout", sys.stdout, 1),
                              ("stderr", sys.stderr, 2)):
        try:
            detail["isatty"][label] = bool(stream is not None and stream.isatty())
        except Exception:
            detail["isatty"][label] = None
        try:
            detail["fd_target"][str(fd)] = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            detail["fd_target"][str(fd)] = None
    return detail


def _round_meta():
    """读取驱动写入的每轮绑定元数据；缺失时记 None，不伪造。"""
    import os
    path = os.environ.get(ROUND_META_ENV_VAR)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:  # pragma: no cover
        return {"error": f"{type(exc).__name__}: {exc}", "path": path}


def _runtime_snapshot(config):
    """进程内只读快照；只读取 flags/env/locale/tz/FD/TTY/插件，不调用 tzset/setlocale。"""
    import locale
    import platform
    import sys
    import time

    env = dict(os.environ)
    try:
        fd_dir = "/proc/self/fd"
        open_fds = sorted(os.listdir(fd_dir)) if os.path.isdir(fd_dir) else []
    except OSError:  # pragma: no cover - 非 Linux
        open_fds = []
    snapshot = {
        "$report_type": "RuntimeSnapshot",
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_executable": sys.executable,
        "python_realpath": os.path.realpath(sys.executable),
        "pytest_version": pytest.__version__,
        "cwd": os.getcwd(),
        "sys_flags": _safe_flags(),
        "open_fds": open_fds,
        "environment": dict(sorted(env.items())),
        "locale": {
            "preferred_encoding": locale.getpreferredencoding(False),
            "LC_ALL": env.get("LC_ALL"),
            "LC_CTYPE": env.get("LC_CTYPE"),
            "LANG": env.get("LANG"),
        },
        "timezone": {"TZ": env.get("TZ"), "tzname": list(time.tzname)},
        "invocation_args": list(getattr(config.invocation_params, "args", ()) or ()),
        "capture_mode": getattr(getattr(config, "option", None), "capture", None),
        "tty": _tty_detail(),
        "loaded_plugins": _plugin_inventory(config),
        "round_meta": _round_meta(),
    }
    payload = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str)
    snapshot["snapshot_hash"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return snapshot


def pytest_collectreport(report):
    """保留所有 native collection 报告，原样翻译（含 root/package/module 与失败项）。"""
    record = {
        "$report_type": "CollectReport",
        "nodeid": report.nodeid,
        "outcome": report.outcome,
    }
    if report.longrepr is not None:
        record["longrepr"] = str(report.longrepr)
    _emit(record)


def pytest_collection_finish(session):
    """选中项单独登记为 CollectionItem，并写出 CollectionFinished 身份/顺序摘要。"""
    nodeids = [item.nodeid for item in session.items]
    for nodeid in nodeids:
        _emit({"$report_type": "CollectionItem", "nodeid": nodeid})
    _emit({
        "$report_type": "CollectionFinished",
        "collected": nodeids,
        "collected_count": len(nodeids),
        "identity_fingerprint": _identity_fingerprint(nodeids),
    })


def pytest_deselected(items):
    for item in items:
        _emit({"$report_type": "DeselectedReport", "nodeid": item.nodeid})


def pytest_runtest_logreport(report):
    """逐阶段 native 报告（setup/call/teardown 都会触发）。"""
    if not report.when:
        return
    record = {
        "$report_type": "TestReport",
        "nodeid": report.nodeid,
        "when": report.when,
        "outcome": report.outcome,
    }
    wasxfail = getattr(report, "wasxfail", None)
    if wasxfail is not None:
        record["wasxfail"] = wasxfail
    if report.longrepr is not None:
        record["longrepr"] = str(report.longrepr)
    _emit(record)


def pytest_sessionfinish(session, exitstatus):
    _emit({
        "$report_type": "SessionFinished",
        "exitstatus": int(exitstatus),
        "collected_count": len(session.items),
        "pytest_version": pytest.__version__,
        "cwd": os.getcwd(),
    })
