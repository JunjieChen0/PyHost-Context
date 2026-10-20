"""合成测试替身：仅提供 run_extension 的 subprocess 命名空间。

真实仪器存在于源码树的 extension/tooling；包内自检只验证注入逻辑，
不会调用真实子进程。
"""
import subprocess  # noqa: F401


def ensure_venv(project, record):  # pragma: no cover - 恒被 instrument_scope 替换
    raise RuntimeError("stub_ensure_venv_must_be_patched")

