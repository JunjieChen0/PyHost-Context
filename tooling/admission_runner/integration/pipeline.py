"""PyHost-Context 准入阶段：集成流水线的 observer / parser / summary 粘合层。

链路（producer→observer→JSONL→parser→summary）：

1. **producer**：``jsonl_producer`` 作为 pytest 插件挂到真实子进程，逐行如实写 native JSONL；
2. **observer**：``run_pytest_suite`` 以子进程启动真实 pytest（合成 fixture 目录），
   通过 ``PYHOST_JSONL_PATH`` 观察并落盘产出的 JSONL 工件；
3. **JSONL**：落在磁盘上的原始逐行记录文件（producer 独占创建，不覆盖）；
4. **parser**：复用 ``pytest_jsonl.py`` 的 ``parse_jsonl``/``collection_info``/
   ``phase_states``/``build_per_test``；
5. **summary**：``summarize`` 汇总 collection、逐测试记录、计数、标签索引，并保留
   ``RuntimeSnapshot``/``SessionFinished`` 与 data/collection 错误。

不下载、不安装、不运行任何候选项目；只对临时目录中的合成套件执行真实 pytest。
"""
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ADMISSION_DIR = _HERE.parent
for _path in (str(_ADMISSION_DIR), str(_HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from jsonl_producer import JSONL_ENV_VAR  # noqa: E402
from pytest_jsonl import build_per_test, collection_info, parse_jsonl  # noqa: E402

PRODUCER_PLUGIN = "jsonl_producer"


def run_pytest_suite(suite_dir, jsonl_path, target=".", extra_args=(), cwd=None,
                     python=None, timeout=120):
    """observer：在真实子进程中运行合成套件，native JSONL 写到 ``jsonl_path``（须不存在）。

    ``PYTHONPATH`` 暴露 producer 插件目录与 parser 目录；``-o addopts=``、``-p
    no:cacheprovider``、``PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`` 保证干净可复现。
    """
    suite_dir = Path(suite_dir)
    jsonl_path = Path(jsonl_path)
    env = dict(os.environ)
    env[JSONL_ENV_VAR] = str(jsonl_path)
    # 关闭 entry-point 插件自动加载：既是冻结基线条件，也隔离无关的第三方自动加载插件
    # （某些环境会因此引入 zarr/dask 等重型依赖）。producer 仍由显式 -p 加载。
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    parts = [str(_HERE), str(_ADMISSION_DIR)]
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    argv = [
        python or sys.executable, "-m", "pytest",
        "-p", PRODUCER_PLUGIN, "-p", "no:cacheprovider",
        "-o", "addopts=", "--no-header", "-q",
        *[str(arg) for arg in extra_args], str(target),
    ]
    return subprocess.run(argv, cwd=str(cwd or suite_dir), env=env,
                          capture_output=True, text=True, timeout=timeout)


def read_jsonl(jsonl_path):
    """observer：读取落盘的 native JSONL 文本。"""
    return Path(jsonl_path).read_text(encoding="utf-8")


def summarize(text, baseline_nodeids=None, run_dir_prefixes=()):
    """parser + summary：解析 native JSONL 并汇总为可断言结构。

    ``collection``（collection_info）、``per_test``（build_per_test）、``counts``、
    ``label_index``、``malformed``、``runtime_snapshot``、``session_finished``、
    ``collection_finished``、``data_errors``、``incomplete_tests``、``collection_errors``。
    """
    records, malformed = parse_jsonl(text)
    collection = collection_info(records, baseline_nodeids=baseline_nodeids)
    nodeids = collection["collected"]
    per_test = build_per_test(records, nodeids, baseline_nodeids=baseline_nodeids,
                              run_dir_prefixes=run_dir_prefixes)
    counts, label_index = {}, {}
    for node, record in per_test.items():
        key = record["raw_outcome"] or "no_outcome"
        counts[key] = counts.get(key, 0) + 1
        for label in record["derived_labels"]:
            label_index.setdefault(label, []).append(node)
    runtime_snapshot = _first(records, "RuntimeSnapshot")
    session_finished = _first(records, "SessionFinished")
    return {
        "collection": collection,
        "per_test": per_test,
        "counts": counts,
        "label_index": {key: sorted(value) for key, value in label_index.items()},
        "malformed": malformed,
        "data_errors": list(malformed),
        "collection_errors": list(collection["errors"]),
        "incomplete_tests": sorted(n for n, r in per_test.items() if not r["raw_complete"]),
        "runtime_snapshot": runtime_snapshot,
        "session_finished": session_finished,
        "collection_finished": collection["collection_finished"],
    }


def _first(records, report_type):
    for record in records:
        if record.get("$report_type") == report_type:
            return record
    return None


def produce_and_summarize(suite_dir, jsonl_path, target=".", extra_args=(),
                          baseline_nodeids=None, run_dir_prefixes=(), **kwargs):
    """完整链路一步到位：跑真实 pytest → 读 JSONL → 解析 → 汇总，并附进程元信息。"""
    proc = run_pytest_suite(suite_dir, jsonl_path, target=target,
                            extra_args=extra_args, **kwargs)
    summary = summarize(read_jsonl(jsonl_path), baseline_nodeids=baseline_nodeids,
                        run_dir_prefixes=run_dir_prefixes)
    summary["process"] = {
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-1500:],
        "stderr_tail": proc.stderr[-1500:],
    }
    return summary


def run_round(suite_dir, jsonl_path, target=".", extra_args=(), **kwargs):
    """为 admission_gate 产出一轮：{jsonl, returncode, timed_out, summary}。"""
    proc = run_pytest_suite(suite_dir, jsonl_path, target=target,
                            extra_args=extra_args, **kwargs)
    return {
        "jsonl": read_jsonl(jsonl_path),
        "returncode": proc.returncode,
        "timed_out": bool(getattr(proc, "timed_out", False)),
        "summary": summarize(read_jsonl(jsonl_path)),
    }
