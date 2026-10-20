"""ClawsGO Science Agent：D 档实际执行入口（A5→A6→A7→A8→对账）。

设计约束（Reviewer 001 D 准备裁定 R1）：
- 只消费一份精确 D 锁；安装清单按**具体 wheel 路径与哈希**构造，不使用
  `--find-links + name==version`；
- 复用冻结的 `run_extension.admit_one` 与 `run_matrix.run_one` 仪器路径，
  仅替换 A5 安装、最终对账与 M1 行为；
- D 环境使用独立目录与独立档案，不读 S1a 的 CANDIDATES 安装闭包；
- 未授权、锁/工件漂移或已有未完成尝试时**不派发**；每项只允许一次尝试。

本模块本身不运行安装；合成测试用假子进程与假安装器验证接线。
"""
import contextlib
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import types

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
PYHOST = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PYHOST / "extension" / "tooling"))
sys.path.insert(0, str(PYHOST / "matrix" / "tooling"))

import d_adapter  # noqa: E402

try:
    import run_extension as rx  # noqa: E402
    import run_matrix as rm  # noqa: E402
except ImportError:  # 包内自检：仅有 stub 时使用占位模块
    sys.path.append(str(HERE / "stubs"))
    import run_extension as rx  # noqa: E402
    import run_matrix as rm  # noqa: E402

D_PROFILE = "d-runner/2026-10-18-v1"
DEFAULT_STATE = NEXT / "state" / "d_toolchain" / "runs"


class DRefused(RuntimeError):
    pass


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def load_document(locks_path):
    return json.loads(pathlib.Path(locks_path).read_text(encoding="utf-8"))


def project_config(roots_path, project):
    """从冻结根清单取项目配置中的显式插件禁用与追加参数。"""
    document = json.loads(pathlib.Path(roots_path).read_text(encoding="utf-8"))
    row = next(item for item in document["projects"] if item["project"] == project)
    config = row.get("pytest_config_as_data") or {}
    addopts = config.get("addopts") or []
    if isinstance(addopts, str):
        addopts = addopts.split()
    disabled, local_plugins = set(), []
    index = 0
    while index < len(addopts):
        item = str(addopts[index])
        value = None
        if item == "-p" and index + 1 < len(addopts):
            value = str(addopts[index + 1])
            index += 1
        elif item.startswith("-p") and len(item) > 2:
            value = item[2:]
        if value:
            if value.startswith("no:"):
                disabled.add(value[3:])
            else:
                local_plugins.append(value)
        index += 1
    return {"disabled_plugins": sorted(disabled),
            "project_local_plugins": local_plugins,
            "extra_pytest_args": []}


def stage_wheels(lock, wheel_root, stage_dir):
    """把锁内**唯一登记**的 wheel 按文件名铺到干净目录；不复制任何未登记文件。"""
    stage_dir = pathlib.Path(stage_dir)
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)
    targets = list(lock["lock"]["rows"])
    candidate = lock.get("candidate_wheel")
    if candidate:
        targets.append(candidate)
    seen = {}
    for row in targets:
        key = (row["name"].lower().replace("_", "-"), row["version"], row["filename"])
        if key in seen:
            raise DRefused(f"duplicate_wheel_row:{key}")
        seen[key] = row["sha256"]
        source = pathlib.Path(wheel_root) / row["sha256"] / row["filename"]
        if not source.is_file() or sha(source) != row["sha256"]:
            raise DRefused(f"wheel_missing_or_changed:{row['filename']}")
        destination = stage_dir / row["filename"]
        try:
            destination.hardlink_to(source)
        except OSError:
            shutil.copyfile(source, destination)
        if sha(destination) != row["sha256"]:
            raise DRefused(f"staged_wheel_sha:{row['filename']}")
    stray = sorted(path.name for path in stage_dir.iterdir()
                   if path.is_file() and path.name not in {r["filename"] for r in targets})
    if stray:
        raise DRefused(f"stray_wheels:{stray}")
    return stage_dir


def preflight_d(lock, stage_dir, config):
    try:
        candidate = d_adapter.candidate_artifact(lock, stage_dir)
        validation = d_adapter.validate_lock(lock, stage_dir, candidate)
    except d_adapter.LockError as error:
        raise DRefused(str(error)) from error
    if not validation["ok"]:
        raise DRefused("lock_invalid:" + ";".join(validation["problems"]))
    # 目标标签：锁内每一件（含候选）都必须匹配 manylinux2014 / glibc 2.17。
    for row in list(lock["lock"]["rows"]) + [candidate]:
        if not d_adapter.wheel_compatible(row["filename"]):
            raise DRefused(f"target_tag_mismatch:{row['filename']}")
    return {"validation": validation, "candidate": candidate,
            "loads": validation["plugins"]["loads"],
            "disabled": list(config.get("disabled_plugins") or [])}


def _probe_installed(venvpy):
    code = ("import importlib.metadata as m;"
            "print('\\n'.join(sorted(f'{d.metadata[\"Name\"]}=={d.version}' "
            "for d in m.distributions())))")
    proc = subprocess.run([str(venvpy), "-c", code], capture_output=True,
                          text=True, timeout=180)
    if proc.returncode != 0:
        raise DRefused(f"inventory_probe_failed:{proc.returncode}")
    return sorted(line.strip() for line in proc.stdout.splitlines() if line.strip())


def ensure_venv_d(project, record, *, lock, stage_dir, venv_root, lock_sha256):
    """D 专用 A5：干净目录 + 独立档案 + 文件级安装。"""
    venv_root = pathlib.Path(venv_root)
    venv_dir = venv_root / project / "venv"
    venvpy = venv_dir / "bin" / "python"
    profile_path = venv_root / project / "profile.json"
    stages = []
    if venvpy.is_file() and (not profile_path.is_file()
                             or json.loads(profile_path.read_text()).get("profile") != D_PROFILE
                             or json.loads(profile_path.read_text()).get("lock_sha256") != lock_sha256):
        shutil.rmtree(venv_root / project, ignore_errors=True)
        stages.append({"stage": "venv_rebuild", "reason": "d_profile_or_lock_changed"})
    if not venvpy.is_file():
        created = subprocess.run([str(rm.BASE_PY), "-m", "venv", "--without-pip",
                                  str(venv_dir)], capture_output=True, text=True,
                                 timeout=600)
        stages.append({"stage": "venv_create", "returncode": created.returncode,
                       "stderr_tail": created.stderr[-300:]})
        if created.returncode != 0:
            raise DRefused(f"venv_create_failed:{created.returncode}")
    plan = d_adapter.install_plan(lock, stage_dir, lock["candidate_wheel"])
    argv = d_adapter.install_argv(venvpy, stage_dir, plan)
    installed = subprocess.run(argv, capture_output=True, text=True, timeout=1800)
    stages.append({"stage": "exact_file_install", "returncode": installed.returncode,
                   "file_count": len(plan["files"]),
                   "stderr_tail": installed.stderr[-600:]})
    if installed.returncode != 0:
        raise DRefused(f"exact_install_failed:{installed.returncode}:{installed.stderr[-400:]}")
    check = subprocess.run(["uv", "pip", "check", "--python", str(venvpy)],
                           capture_output=True, text=True, timeout=300)
    stages.append({"stage": "dependency_check", "returncode": check.returncode,
                   "stdout_tail": check.stdout[-400:]})
    if check.returncode != 0:
        raise DRefused(f"dependency_check_failed:{check.returncode}")
    inventory = _probe_installed(venvpy)
    record["venv"] = {
        "python": str(venvpy), "version_line": [],
        "installed": inventory, "stages": stages,
        "requested_requirement": f"{lock['project']}=={lock['version']}",
        "requested_extras": [], "frozen_stack": [],
        "d_lock_sha256": lock_sha256,
        "install_files": [{"filename": item["filename"], "sha256": item["sha256"],
                           "path": item["path"]} for item in plan["files"]],
    }
    observer = rm.install_matrix_observer(venvpy)
    record["venv"]["observer"] = observer
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps({
        "profile": D_PROFILE, "lock_sha256": lock_sha256,
        "project": project, "install_files": len(plan["files"]),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"venv_python": str(venvpy), "installed": inventory}


def expected_from_lock(lock):
    expected = {}
    for row in lock["lock"]["rows"]:
        expected[d_adapter.canonicalize_name(row["name"])] = str(row["version"])
    candidate = lock["candidate_wheel"]
    expected[d_adapter.canonicalize_name(candidate["name"])] = str(candidate["version"])
    return expected


def explain_environment_d(record, venv, expected):
    """最终对账只认 D 锁 + 冻结 observer；任何多余包或版本不符都拒绝。"""
    final = record.get("installed_final") or {}
    allowed = dict(expected)
    allowed["pyhost-observer"] = None
    unexplained, mismatches = [], []
    for name, version in sorted(final.items()):
        key = d_adapter.canonicalize_name(name)
        if key not in allowed:
            unexplained.append({"distribution": name, "version": version})
        elif allowed[key] and version and allowed[key] != version:
            mismatches.append({"distribution": name, "installed": version,
                               "expected": allowed[key]})
    return {"installed_count": len(final), "project": record.get("project"),
            "final_inventory_source": "re-queried after D exact-file install",
            "unexplained_distributions": unexplained,
            "version_mismatches": mismatches,
            "accounted": not unexplained and not mismatches,
            "rule": "D 锁外分发与版本不符一律不得准入"}


def _is_pytest_argv(argv):
    text = [str(item) for item in argv]
    return "-m" in text and text[text.index("-m") + 1:text.index("-m") + 2] == ["pytest"]


class _RMProxy:
    """委托真实 run_matrix，只合并 D 的插件与追加参数。"""

    def __init__(self, module, loads, extra_args):
        self._module = module
        self._loads = tuple(loads)
        self._extra = tuple(extra_args)

    def __getattr__(self, name):
        return getattr(self._module, name)

    def run_one(self, **kwargs):
        existing = tuple(kwargs.get("plugin_loads") or ())
        kwargs["plugin_loads"] = tuple(dict.fromkeys(existing + self._loads))
        entry = tuple(kwargs.get("entry_argv") or ("-m", "pytest"))
        kwargs["entry_argv"] = entry + self._extra
        return self._module.run_one(**kwargs)


@contextlib.contextmanager
def d_runtime(lock, stage_dir, venvpy, loads, disabled, extra_args, expected,
              venv_root, lock_sha256):
    """把真实 admit_one 接到 D 的 A5、M1 拒绝与最终对账上。"""
    original = {
        "ensure_venv": rx.ensure_venv, "venv_python": rx.venv_python,
        "explain_environment": rx.explain_environment,
        "supplemental_install": rx.supplemental_install,
        "declaration_evidence": rx.declaration_evidence,
        "load_json": rx.load_json, "subprocess": rx.subprocess,
        "rm": rx.rm, "artifact_dir": rx.artifact_dir,
        "project_source_dir": rx.project_source_dir,
    }
    original_run = rx.subprocess.run
    original_candidates = rx.CANDIDATES

    def run_with_d_args(argv, *args, **kwargs):
        if isinstance(argv, list) and _is_pytest_argv(argv):
            d_adapter.inject_plugins(argv, loads, disabled)
            for value in extra_args:
                if value not in argv:
                    argv.append(value)
        return original_run(argv, *args, **kwargs)

    def load_json_d(path):
        if pathlib.Path(path) == pathlib.Path(original_candidates):
            return {"candidates": {lock["project"]: {
                "version": lock["version"],
                "sdist": {"filename": "d-lock-no-sdist", "sha256": "0" * 64},
                "dependencies": {}}}}
        return original["load_json"](path)

    rx.ensure_venv = lambda project, record: ensure_venv_d(
        project, record, lock=lock, stage_dir=stage_dir, venv_root=venv_root,
        lock_sha256=lock_sha256)
    rx.venv_python = lambda project: pathlib.Path(venvpy)
    rx.explain_environment = lambda record, venv: explain_environment_d(
        record, venv, expected)
    rx.supplemental_install = lambda *args, **kwargs: (
        False, {"refused": "D_lock_closed", "reason": "M1 must not resolve outside D lock"})
    rx.declaration_evidence = lambda *args, **kwargs: {
        "extras": {}, "files": {}, "lines": [], "sdist": "d-lock-no-sdist"}
    rx.load_json = load_json_d
    rx.subprocess = types.SimpleNamespace(run=run_with_d_args)
    rx.rm = _RMProxy(original["rm"], loads, extra_args)
    rx.artifact_dir = lambda project: pathlib.Path(stage_dir)
    rx.project_source_dir = lambda project: project
    try:
        yield
    finally:
        rx.ensure_venv = original["ensure_venv"]
        rx.venv_python = original["venv_python"]
        rx.explain_environment = original["explain_environment"]
        rx.supplemental_install = original["supplemental_install"]
        rx.declaration_evidence = original["declaration_evidence"]
        rx.load_json = original["load_json"]
        rx.subprocess = original["subprocess"]
        rx.rm = original["rm"]
        rx.artifact_dir = original["artifact_dir"]
        rx.project_source_dir = original["project_source_dir"]


def admit_one_d(project, protocol, b0, commitment, phase_root, *,
                locks_path, wheel_root, roots_path, state_dir=DEFAULT_STATE,
                authorization=None, extra_pytest_args=(),
                defaults_path=None, prepare_context=None):
    """实际 D 派发入口；未授权/漂移/既有未完成尝试时不调用任何子进程。"""
    authorization = authorization or {}
    if authorization.get("D_admission") is not True:
        return {"dispatched": False, "reason": "D_admission_not_authorized",
                "project": project}
    document = load_document(locks_path)
    lock = d_adapter.load_lock(document, project)
    lock_sha256 = hashlib.sha256(
        json.dumps(lock, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    state_dir = pathlib.Path(state_dir)
    attempt_path = state_dir / project / "attempt.json"
    if attempt_path.is_file():
        prior = json.loads(attempt_path.read_text(encoding="utf-8"))
        if prior.get("status") == "complete":
            return {"dispatched": False, "reason": "attempt_already_complete",
                    "project": project, "cached": prior}
        return {"dispatched": False, "reason": "prior_attempt_not_reusable",
                "project": project, "prior": prior}
    config = project_config(roots_path, project)
    defaults_path = pathlib.Path(defaults_path or
                                 NEXT / "D_PLUGIN_DEFAULTS_20261018.json")
    defaults = {}
    if defaults_path.is_file():
        defaults = (json.loads(defaults_path.read_text(encoding="utf-8"))
                    .get("projects") or {}).get(project, {})
    config["extra_pytest_args"] = list(dict.fromkeys(
        list(extra_pytest_args) + list(defaults.get("extra_pytest_args") or [])))
    stage_dir = state_dir / project / "install_wheels"
    preflight = preflight_d(lock, stage_wheels(lock, wheel_root, stage_dir), config)
    venv_root = state_dir / "venvs"
    expected = expected_from_lock(lock)
    attempt_path.parent.mkdir(parents=True, exist_ok=True)
    attempt_path.write_text(json.dumps({
        "status": "running", "project": project, "lock_sha256": lock_sha256,
        "lock_rows": len(lock["lock"]["rows"]), "dispatched": True,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    record = {}
    original_prepare = rm.prepare_context
    try:
        if prepare_context is not None:
            rm.prepare_context = prepare_context
        with d_runtime(lock, stage_dir, state_dir / "venvs" / project / "venv" / "bin" / "python",
                       preflight["loads"], preflight["disabled"],
                       config["extra_pytest_args"], expected, venv_root, lock_sha256):
            first = rx.admit_one(project, protocol, b0, commitment, phase_root, record)
    except Exception as error:  # noqa: BLE001
        attempt_path.write_text(json.dumps({
            "status": "error", "project": project, "lock_sha256": lock_sha256,
            "error": f"{type(error).__name__}:{error}",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    finally:
        rm.prepare_context = original_prepare
    admitted, refusals = rx.admission_decision(record)
    outcome = {
        "status": "complete", "project": project, "lock_sha256": lock_sha256,
        "admitted": admitted, "refusals": refusals,
        "A5_environment_ok": record.get("A5_environment_ok"),
        "A6_collect_ok": record.get("A6_collect_ok"),
        "A7_baseline": record.get("A7_baseline"),
        "A8_restore_ok": record.get("A8_restore_ok"),
        "environment_accounting": record.get("environment_accounting"),
        "loads": preflight["loads"], "disabled": preflight["disabled"],
        "extra_pytest_args": config["extra_pytest_args"],
        "installed_final": record.get("installed_final"),
        "venv": record.get("venv"),
        "M1": record.get("M1"),
    }
    attempt_path.write_text(json.dumps(outcome, ensure_ascii=False, indent=2,
                                       sort_keys=True, default=str), encoding="utf-8")
    return {"dispatched": True, "reason": "attempt_complete", "project": project,
            "outcome": outcome, "record": record, "first": first}
