"""ClawsGO Science Agent：把 observer 作为**已声明工件**装进候选 venv 的 site-packages。

背景：原实现靠 ``PYTHONPATH`` 把 ``tooling/admission_runner`` 与其 ``integration`` 目录
塞进子进程，才能 ``-p jsonl_producer``。这使冻结的 B0 启动环境里 ``PYTHONPATH`` 声明为
``unset`` 而实际为 ``set(两个目录)``，违反三态规格（独立审计 F01）。

改为把 observer 作为普通模块安装进候选 venv 的 site-packages：

- ``PYTHONPATH`` 保持 **unset**，启动环境与声明完全一致；
- ``-p jsonl_producer`` 靠普通 import 解析，不再依赖环境变量引导；
- 安装内容按文件逐个记录 SHA-256，成为可核验的冻结工件而非隐式路径注入。

同时安装 ``sitecustomize.py``（netwatch）：只**记录**非环回连接尝试，不阻断任何调用，
用于给出"测试期间未发起外部网络连接"的可核验证据。该文件对未设置 ``PYHOST_NETLOG``
的进程完全惰性。
"""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
INTEGRATION = HERE / "integration"

# 装入候选 venv site-packages 的工件（源路径 → 目标文件名）
OBSERVER_FILES = {
    INTEGRATION / "jsonl_producer.py": "jsonl_producer.py",
    HERE / "pytest_jsonl.py": "pytest_jsonl.py",
    HERE / "netwatch_sitecustomize.py": "sitecustomize.py",
}

DIST_INFO = "pyhost_observer-0.1.0.dist-info"


def sha256_of(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def site_packages(venvpy):
    out = subprocess.run(
        [str(venvpy), "-c", "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
        capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"无法定位 site-packages：{out.stderr[-400:]}")
    return Path(out.stdout.strip())


METADATA = """Metadata-Version: 2.1
Name: pyhost-observer
Version: 0.1.0
Summary: PyHost-Context 准入阶段的只读 observer（pytest 插件 + 网络尝试记录）
"""

WHEEL = """Wheel-Version: 1.0
Generator: pyhost-context-observer-installer
Root-Is-Purelib: true
Tag: py3-none-any
"""


def install_observer(venvpy, record=None):
    """把 observer 装进 venv；返回逐文件 SHA-256 记录（不含目标目录前缀的绝对路径）。"""
    venvpy = Path(venvpy)
    purelib = site_packages(venvpy)
    installed = {}
    for source, name in OBSERVER_FILES.items():
        target = purelib / name
        shutil.copyfile(source, target)
        installed[name] = {"sha256": sha256_of(target), "bytes": target.stat().st_size}

    dist = purelib / DIST_INFO
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "METADATA").write_text(METADATA, encoding="utf-8")
    (dist / "WHEEL").write_text(WHEEL, encoding="utf-8")
    (dist / "INSTALLER").write_text("pyhost-context-observer-installer\n", encoding="utf-8")
    lines = []
    for name in sorted(installed) + ["METADATA", "WHEEL", "INSTALLER"]:
        rel = name if name in installed else f"{DIST_INFO}/{name}"
        path = purelib / rel
        lines.append(f"{rel},,{sha256_of(path)}")
    (dist / "RECORD").write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = {
        "site_packages": str(purelib),
        "dist_info": DIST_INFO,
        "files": installed,
        "purpose": "使 PYTHONPATH 在 B0 中保持 unset；observer 为已声明冻结工件",
    }
    if record is not None:
        record.update(result)
    return result


if __name__ == "__main__":  # pragma: no cover - 手工使用
    import sys
    print(json.dumps(install_observer(sys.argv[1]), ensure_ascii=False, indent=2))
