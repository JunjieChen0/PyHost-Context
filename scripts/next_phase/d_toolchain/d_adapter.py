"""ClawsGO Science Agent：D 档精确锁的最小执行适配器。

职责：
1. 载入并核验一份 D 锁（文件哈希、名称/版本、pytest 版本、插件映射）；
2. 生成 A5 安装清单与离线 uv 安装命令（全部 `name==version`，`--no-deps`）；
3. 从锁中实际 wheel 的 `entry_points.txt` 生成显式 `-p` 插件列表；
4. 在复用既有 `run_extension` 仪器的前提下，把插件加载与 pytest 版本断言注入
   A6 收集和 A7 单轮。

本模块不被当前准备阶段执行安装；合成测试只使用临时假 wheel 与假进程。
"""
import contextlib
import hashlib
import json
import pathlib
import zipfile

from packaging.utils import canonicalize_name

STATUSES = ("locked",)

WHEEL_PLATFORM_TAGS = {"any", "linux_x86_64", "manylinux1_x86_64",
                       "manylinux2010_x86_64", "manylinux2014_x86_64"}


def wheel_compatible(filename):
    """目标 CPython 3.11 / manylinux2014（glibc 2.17）/ x86_64 文件名标签。"""
    import re
    if not str(filename).endswith(".whl"):
        return False
    parts = str(filename)[:-4].split("-")
    if len(parts) < 5:
        return False
    python_tag, abi_tag, platform_tag = parts[-3].lower(), parts[-2].lower(), parts[-1].lower()

    def platform_ok(tag):
        if tag in WHEEL_PLATFORM_TAGS:
            return True
        match = re.fullmatch(r"manylinux_2_(\d+)_x86_64", tag)
        return bool(match and int(match.group(1)) <= 17)

    if not any(platform_ok(tag) for tag in platform_tag.split(".")):
        return False
    if python_tag in ("py3", "py2.py3"):
        return abi_tag in ("none", "abi3")
    if python_tag.startswith("cp") and python_tag[2:].isdigit():
        major_minor = int(python_tag[2:])
        if major_minor == 311:
            return abi_tag in ("cp311", "abi3", "none")
        if abi_tag == "abi3" and major_minor <= 311:
            return True
    return False


class LockError(RuntimeError):
    pass


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def load_lock(document, project):
    for row in document.get("projects", []):
        if row.get("project") == project:
            if row.get("status") != "locked":
                raise LockError(f"project_not_locked:{row.get('status')}")
            return row
    raise LockError(f"project_missing:{project}")


def wheel_entries(path):
    with zipfile.ZipFile(path) as archive:
        entry = next((n for n in archive.namelist()
                      if n.endswith(".dist-info/entry_points.txt")
                      and n.count("/") == 1), None)
        if not entry:
            return {}
        import configparser
        parser = configparser.ConfigParser()
        parser.read_string(archive.read(entry).decode("utf-8", "replace"))
        return dict(parser.items("pytest11")) if parser.has_section("pytest11") else {}


def wheel_identity(path):
    """读取顶层 dist-info/METADATA 的 Name/Version。"""
    with zipfile.ZipFile(path) as archive:
        member = next((n for n in archive.namelist()
                       if n.endswith(".dist-info/METADATA") and n.count("/") == 1), None)
        if not member:
            return None, None
        header = archive.read(member).decode("utf-8", "replace").split("\n\n", 1)[0]
    name = version = None
    for line in header.splitlines():
        if line.startswith("Name:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("Version:"):
            version = line.split(":", 1)[1].strip()
    return name, version


def preflight(lock, wheel_dir):
    """逐件核验锁内 wheel 的存在、哈希与名称/版本；返回工件回执。"""
    wheel_dir = pathlib.Path(wheel_dir)
    problems = []
    receipts = []
    for row in lock["lock"]["rows"]:
        path = wheel_dir / row["filename"]
        if not path.is_file():
            problems.append(f"missing_wheel:{row['filename']}")
            continue
        digest = sha(path)
        if digest != row["sha256"]:
            problems.append(f"wheel_sha256:{row['filename']}")
        with zipfile.ZipFile(path) as archive:
            member = next((n for n in archive.namelist()
                           if n.endswith(".dist-info/METADATA")
                           and n.count("/") == 1), None)
            if not member:
                problems.append(f"metadata_missing:{row['filename']}")
                continue
        name, version = wheel_identity(path)
        if canonicalize_name(name or "") != canonicalize_name(row["name"]) \
                or str(version) != str(row["version"]):
            problems.append(f"identity:{row['filename']}:{name}=={version}")
        receipts.append({"name": row["name"], "version": row["version"],
                         "filename": row["filename"], "sha256": digest,
                         "entries": wheel_entries(path)})
    return {"ok": not problems, "problems": problems, "receipts": receipts}


def validate_lock(lock, wheel_dir, candidate):
    """锁级不变量：pytest 唯一、候选唯一、同名不重复、插件映射可用。"""
    problems = []
    names = {}
    for row in lock["lock"]["rows"]:
        names.setdefault(canonicalize_name(row["name"]), set()).add(row["version"])
    for name, versions in names.items():
        if len(versions) > 1:
            problems.append(f"duplicate_name:{name}:{sorted(versions)}")
    # 同名同版本只能出现一次；两份相同字节也是重复登记，不能静默接受。
    identities = {}
    for row in lock["lock"]["rows"]:
        key = (canonicalize_name(row["name"]), str(row["version"]))
        identities.setdefault(key, []).append(row.get("sha256"))
    for (name, version), digests in identities.items():
        if len(digests) > 1:
            unique = sorted(set(digests))
            suffix = "different_bytes" if len(unique) > 1 else "duplicate_row"
            problems.append(f"duplicate_same_version:{name}=={version}:{suffix}:{unique}")
    if "pytest" not in names:
        problems.append("pytest_missing")
    elif names["pytest"] != {str(lock.get("pytest_version"))}:
        problems.append(f"pytest_version_mismatch:{sorted(names['pytest'])}")
    if canonicalize_name(candidate["name"]) in names:
        problems.append("candidate_duplicates_lock_row")
    candidate_path = pathlib.Path(wheel_dir) / candidate["filename"]
    if not candidate_path.is_file():
        problems.append(f"missing_wheel:{candidate['filename']}")
    else:
        if sha(candidate_path) != candidate["sha256"]:
            problems.append(f"wheel_sha256:{candidate['filename']}")
        cname, cversion = wheel_identity(candidate_path)
        if canonicalize_name(cname or "") != canonicalize_name(candidate["name"]) \
                or str(cversion) != str(candidate["version"]):
            problems.append(f"candidate_identity:{cname}=={cversion}")
    pre = preflight(lock, wheel_dir)
    plugins = plugin_map(lock, wheel_dir, candidate)
    problems.extend(pre["problems"])
    problems.extend(plugins["problems"])
    problems.extend(f"plugin_conflict:{c['entry_name']}" for c in plugins["conflicts"])
    return {"ok": not problems, "problems": problems,
            "preflight": pre, "plugins": plugins}


def plugin_map(lock, wheel_dir, candidate=None):
    """读取锁中**全部** wheel（含候选自身）的 pytest11；返回加载列表与冲突。"""
    wheel_dir = pathlib.Path(wheel_dir)
    rows, owners, problems = [], {}, []
    targets = list(lock["lock"]["rows"])
    if candidate is not None:
        targets.append({**candidate, "candidate": True})
    for row in targets:
        path = wheel_dir / row["filename"]
        if not path.is_file():
            problems.append(f"missing_wheel:{row['filename']}")
            continue
        for entry_name, module in wheel_entries(path).items():
            rows.append({"distribution": row["name"], "version": row["version"],
                         "filename": row["filename"], "sha256": row["sha256"],
                         "entry_name": entry_name, "module": module,
                         "candidate": bool(row.get("candidate"))})
            owners.setdefault(entry_name, set()).add(row["name"])
    conflicts = [{"entry_name": name, "distributions": sorted(dist)}
                 for name, dist in owners.items() if len(dist) > 1]
    loads = sorted(owners)
    return {"ok": not problems and not conflicts, "problems": problems,
            "conflicts": conflicts, "loads": loads, "entries": rows}


def install_plan(lock, wheel_dir, candidate):
    """A5 安装清单：精确 pin + 文件回执；不解析依赖（--no-deps）。"""
    wheel_dir = pathlib.Path(wheel_dir)
    pins = []
    files = []
    for row in lock["lock"]["rows"]:
        pins.append(f"{row['name']}=={row['version']}")
        files.append({"name": row["name"], "version": row["version"],
                      "filename": row["filename"], "sha256": row["sha256"],
                      "path": str(wheel_dir / row["filename"])})
    pins.append(f"{candidate['name']}=={candidate['version']}")
    files.append({"name": candidate["name"], "version": candidate["version"],
                  "filename": candidate["filename"], "sha256": candidate["sha256"],
                  "path": str(wheel_dir / candidate["filename"])})
    return {"pins": sorted(set(pins)), "files": files,
            "wheel_count": len(files), "no_deps": True}


def install_argv(venvpy, wheel_dir, plan):
    """按**具体文件路径**安装；不使用 --find-links + 名字版本，避免同版本替代文件。"""
    paths = [str(pathlib.Path(item["path"])) for item in plan["files"]]
    return ["uv", "pip", "install", "--python", str(venvpy), "--offline", "--no-index",
            "--no-deps", "--link-mode", "copy", *paths]


def verify_environment(venvpy, lock, candidate):
    """在目标 venv 内只读列出已安装分发包，与锁逐项比对。"""
    import subprocess
    probe = ("import importlib.metadata as m;"
             "print('\\n'.join(sorted(f'{d.metadata[\"Name\"].lower().replace(\"_\",\"-\")}=={d.version}' "
             "for d in m.distributions())))")
    proc = subprocess.run([str(venvpy), "-c", probe], capture_output=True, text=True,
                          timeout=120)
    if proc.returncode != 0:
        return {"ok": False, "problems": [f"probe_failed:{proc.returncode}"], "installed": []}
    installed = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    expected = {f"{canonicalize_name(r['name'])}=={r['version']}"
                for r in lock["lock"]["rows"]}
    expected.add(f"{canonicalize_name(candidate['name'])}=={candidate['version']}")
    actual = set(installed)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    return {"ok": not missing and not extra, "missing": missing, "extra": extra,
            "installed": installed}


def pytest_version(venvpy):
    import subprocess
    proc = subprocess.run([str(venvpy), "-c", "import pytest;print(pytest.__version__)"],
                          capture_output=True, text=True, timeout=120)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _is_pytest_argv(argv):
    text = [str(item) for item in argv]
    return "-m" in text and text[text.index("-m") + 1:text.index("-m") + 2] == ["pytest"]


def _pytest_plugin_state(argv):
    """解析 argv 中 `-p x`、`-px`、`-p no:x`、`-pno:x` 的加载与禁用集合。"""
    text = [str(item) for item in argv]
    loaded, disabled = set(), set()
    index = 0
    while index < len(text):
        item = text[index]
        value = None
        if item == "-p" and index + 1 < len(text):
            value = text[index + 1]
            index += 1
        elif item.startswith("-p") and len(item) > 2:
            value = item[2:]
        if value is not None:
            value = value.strip()
            if value.startswith("no:"):
                disabled.add(value[3:])
            elif value:
                loaded.add(value)
        index += 1
    return loaded, disabled


def inject_plugins(argv, loads, disabled=()):
    """把 `-p <entry>` 追加到 pytest argv；显式 `-p no:<entry>` 不得被重新开启。"""
    if not _is_pytest_argv(argv):
        return argv
    existing, explicitly_disabled = _pytest_plugin_state(argv)
    explicitly_disabled |= set(disabled)
    for entry in loads:
        if entry in existing or entry in explicitly_disabled:
            continue
        argv.append("-p")
        argv.append(entry)
        existing.add(entry)
    return argv


@contextlib.contextmanager
def instrument_scope(lock, wheel_dir, venvpy, loads, require_pytest_version=True,
                     disabled=()):
    """在复用既有仪器时注入插件加载与 pytest 版本断言。"""
    import run_extension as rx
    import run_matrix as rm

    observed = pytest_version(venvpy) if require_pytest_version else None
    if require_pytest_version and observed != str(lock["pytest_version"]):
        raise LockError(f"pytest_version_mismatch:{observed}!={lock['pytest_version']}")
    original_run = rx.subprocess.run
    original_run_one = rm.run_one
    original_ensure = rx.ensure_venv
    if lock.get("candidate_wheel") and wheel_dir is not None:
        rx.ensure_venv = lambda project, record: _ensure_venv_from_lock(
            lock, wheel_dir, venvpy, record)

    def run_with_plugins(argv, *args, **kwargs):
        if isinstance(argv, list):
            inject_plugins(argv, loads, disabled)
        return original_run(argv, *args, **kwargs)

    def run_one_with_plugins(*args, **kwargs):
        existing = tuple(kwargs.get("plugin_loads") or ())
        kwargs["plugin_loads"] = tuple(dict.fromkeys(existing + tuple(loads)))
        return original_run_one(*args, **kwargs)

    rx.subprocess.run = run_with_plugins
    rm.run_one = run_one_with_plugins
    try:
        yield {"pytest_version": observed, "loads": list(loads)}
    finally:
        rx.subprocess.run = original_run
        rm.run_one = original_run_one
        rx.ensure_venv = original_ensure


def candidate_artifact(lock, wheel_dir):
    """从锁中取候选自身的 wheel 条目。"""
    candidate = lock.get("candidate_wheel")
    if not candidate:
        raise LockError("candidate_wheel_missing")
    path = pathlib.Path(wheel_dir) / candidate["filename"]
    if not path.is_file() or sha(path) != candidate["sha256"]:
        raise LockError("candidate_wheel_unverified")
    name, version = wheel_identity(path)
    if canonicalize_name(name or "") != canonicalize_name(candidate["name"]) \
            or str(version) != str(candidate["version"]):
        raise LockError(f"candidate_identity:{name}=={version}")
    return candidate


def _ensure_venv_from_lock(lock, wheel_dir, venvpy, record):
    """在仪器作用域内把 A5 绑到同一份 D 锁（文件级安装）。"""
    import run_extension as rx
    import run_matrix as rm
    venv_path = pathlib.Path(venvpy)
    venv_dir = venv_path.parent.parent
    if venv_path.parent.name == "bin" and not venv_path.is_file():
        created = rx.subprocess.run([str(rm.BASE_PY), "-m", "venv", "--without-pip",
                                     str(venv_dir)], capture_output=True, text=True,
                                    timeout=600)
        if getattr(created, "returncode", 1) != 0:
            raise LockError(f"venv_create_failed:{getattr(created, 'returncode', None)}")
    candidate = candidate_artifact(lock, wheel_dir)
    plan = install_plan(lock, wheel_dir, candidate)
    installed = rx.subprocess.run(install_argv(venvpy, wheel_dir, plan),
                                  capture_output=True, text=True, timeout=1800)
    if getattr(installed, "returncode", 1) != 0:
        raise LockError(f"exact_install_failed:{getattr(installed, 'returncode', None)}")
    check = rx.subprocess.run(["uv", "pip", "check", "--python", str(venvpy)],
                              capture_output=True, text=True, timeout=300)
    if getattr(check, "returncode", 1) != 0:
        raise LockError(f"dependency_check_failed:{getattr(check, 'returncode', None)}")
    probe = rx.subprocess.run(
        [str(venvpy), "-c",
         "import importlib.metadata as m;"
         "print('\\n'.join(sorted(f'{d.metadata[\"Name\"]}=={d.version}' "
         "for d in m.distributions())))"], capture_output=True, text=True, timeout=180)
    record["venv"] = {"python": str(venvpy), "installed": [
        line.strip() for line in (probe.stdout or "").splitlines() if line.strip()],
        "install_files": plan["files"]}
    record["venv"]["observer"] = rm.install_matrix_observer(venvpy)
    return {"venv_python": str(venvpy), "installed": record["venv"]["installed"]}


def prepare(lock_document, project, wheel_dir):
    """A5/A6/A7 共用入口：同一锁 → 安装清单、插件列表与仪器断言。

    任何哈希、身份或插件冲突都会在安装前抛出 LockError，不降级执行。
    """
    lock = load_lock(lock_document, project)
    candidate = candidate_artifact(lock, wheel_dir)
    validation = validate_lock(lock, wheel_dir, candidate)
    if not validation["ok"]:
        raise LockError("lock_invalid:" + ";".join(validation["problems"]))
    plan = install_plan(lock, wheel_dir, candidate)
    return {"lock": lock, "candidate": candidate, "plan": plan,
            "loads": validation["plugins"]["loads"],
            "plugins": validation["plugins"],
            "pytest_version": lock["pytest_version"]}
