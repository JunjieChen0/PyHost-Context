"""ClawsGO Science Agent：S1b 薄适配器（准备件；执行须另行签署）。

职责边界：
- 只消费 ``S1B_ROSTER`` / ``S1B_TOOLCHAINS`` / ``S1B_SCHEDULE`` / 事前承诺；
- 授权门只接受 Reviewer 001 签署、绑定本承诺哈希的 S1b 放行件；
- 逐项目环境按**具体 wheel 文件路径**离线安装，保留各自原工具链（D-R1 / D-R0 / S1a）；
- 主矩阵与独立 B0 对照按冻结调度交错执行，两个账本 run_id 不重叠；
- pytest 9.1.1 的 ``PYTEST_VERSION`` 只按 Reviewer 001 签署的派生规则比较，不改环境。

本文件本身不执行任何候选；``--status`` 可离线核验输入与承诺。
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tarfile

HERE = pathlib.Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
WS = PROJECT.parent
INPUTS = HERE / "inputs"

ROSTER_PATH = HERE / "S1B_ROSTER_20261019.json"
TOOLCHAINS_PATH = HERE / "S1B_TOOLCHAINS_20261019.json"
SCHEDULE_PATH = HERE / "S1B_SCHEDULE_20261019.json"
COMMITMENT_PATH = HERE / "S1B_PRE_RUN_COMMITMENT_20261019.json"
TRANSITION_PATH = INPUTS / "pytest_runner_transition.py"
AMENDMENT_PATH = INPUTS / "DECLARED_PYTEST_METADATA_AMENDMENT.json"

BATCH = "next-S1b-20261019"
OBSERVER = ("pyhost-observer", "0.2.0")
DECISION = "APPROVED_FOR_S1B_EXECUTION"
STATE_ROOT_REL = "pyhost-context/extension/next_phase/state/s1b_20261019"


class Refused(RuntimeError):
    pass


class TechnicalFailure(RuntimeError):
    pass


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8")
    temp.replace(path)


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def aggregate(files):
    return hashlib.sha256("".join(f"{path}:{files[path]}\n"
                                  for path in sorted(files)).encode()).hexdigest()


def canon(name):
    import re
    return re.sub(r"[-_.]+", "-", str(name)).lower()


def _ensure_paths():
    for path in (PROJECT / "matrix/tooling", PROJECT / "tooling",
                 PROJECT / "tooling/admission_runner",
                 PROJECT / "matrix/tooling/matrix_runner",
                 PROJECT / "extension/tooling", INPUTS):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def load_inputs():
    return (read(ROSTER_PATH), read(TOOLCHAINS_PATH), read(SCHEDULE_PATH),
            read(COMMITMENT_PATH))


# ---------------------------------------------------------------- 输入校验

def validate_inputs(roster, toolchains, schedule):
    """纯结构校验；不访问候选、不安装。"""
    problems = []
    projects = roster.get("projects") or []
    if len(projects) != 5 or roster.get("expected_actual_N") != 5:
        problems.append("roster_size")
    if [p["project"] for p in projects] != ["structlog", "rich-toolkit", "twine",
                                            "typer", "pytest-xdist"]:
        problems.append("roster_order")
    if [p["position"] for p in projects] != [2, 4, 7, 18, 25]:
        problems.append("roster_positions")
    profiles = {p["project"]: p["profile"] for p in projects}
    if profiles != {"structlog": "D-R1", "rich-toolkit": "D-R1", "twine": "D-R0",
                    "typer": "S1a", "pytest-xdist": "S1a"}:
        problems.append("roster_profiles")
    if roster.get("main_matrix", {}).get("runs") != 225 or \
            roster.get("independent_b0", {}).get("runs") != 225:
        problems.append("roster_budget")
    tools = toolchains.get("projects") or {}
    if sorted(tools) != sorted(profiles):
        problems.append("toolchain_projects")
    for project, tool in tools.items():
        row = next(p for p in projects if p["project"] == project)
        if tool.get("version") != row["version"] or tool.get("profile") != row["profile"]:
            problems.append(f"toolchain_identity:{project}")
        if tool.get("pytest_version") != row["pytest_version"]:
            problems.append(f"toolchain_pytest:{project}")
        files = tool.get("install_files") or []
        if not files:
            problems.append(f"install_files_empty:{project}")
        seen = set()
        for item in files:
            key = item["filename"]
            if key in seen:
                problems.append(f"install_file_duplicate:{project}:{key}")
            seen.add(key)
        expected = tool.get("expected_distributions") or {}
        if canon("pyhost-observer") not in expected:
            problems.append(f"observer_not_expected:{project}")
        loads = tool.get("plugin_loads") or []
        if len(loads) != len(set(loads)):
            problems.append(f"plugin_loads_duplicate:{project}")
        if tool["profile"].startswith("D-") and not loads:
            problems.append(f"D_plugins_missing:{project}")
        if tool["profile"] == "S1a" and loads:
            problems.append(f"S1a_plugins_changed:{project}")
        if tool["profile"] != "D-R1" and (tool.get("extra_pytest_args") or []):
            problems.append(f"extra_args_outside_D_R1:{project}")
    entries = schedule.get("entries") or []
    if len(entries) != 450:
        problems.append(f"schedule_size:{len(entries)}")
    main = [e for e in entries if e["arm"] == "main"]
    control = [e for e in entries if e["arm"] == "control"]
    if len(main) != 225 or len(control) != 225:
        problems.append(f"schedule_arms:{len(main)}:{len(control)}")
    for row in projects:
        project = row["project"]
        if sum(1 for e in main if e["project"] == project) != 45:
            problems.append(f"main_per_project:{project}")
        if sum(1 for e in control if e["project"] == project) != 45:
            problems.append(f"control_per_project:{project}")
        for condition in ["B0"] + [f"K0{i}" for i in range(1, 9)]:
            if sum(1 for e in main if e["project"] == project
                   and e["condition"].startswith(condition)) != 5:
                problems.append(f"main_condition_repeats:{project}:{condition}")
    ids = [e["run_id"] for e in entries]
    if len(ids) != len(set(ids)):
        problems.append("run_id_duplicate")
    if any(e["run_id"].startswith("s1b-main") != (e["arm"] == "main") for e in entries):
        problems.append("run_id_arm_prefix")
    if sorted(e["order"] for e in entries) != list(range(1, 451)):
        problems.append("schedule_order")
    recomputed = hashlib.sha256(
        json.dumps({k: v for k, v in schedule.items() if k != "schedule_sha256"},
                   sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if recomputed != schedule.get("schedule_sha256"):
        problems.append("schedule_sha256")
    for entry in entries:
        want = hashlib.sha256(f"{schedule['seed']}|{entry['label']}".encode()).hexdigest()
        if entry.get("label_sha256") != want:
            problems.append(f"label_sha256:{entry['run_id']}")
    if [e["label_sha256"] for e in entries] != sorted(e["label_sha256"] for e in entries):
        problems.append("schedule_not_sorted")
    problems.extend(validate_canonical_schedule(roster, schedule))
    return problems


def verify_commitment(commitment):
    files = commitment.get("files") or {}
    problems = []
    for path, digest in files.items():
        full = WS / path
        if not full.is_file():
            problems.append(f"missing:{path}")
        elif sha(full) != digest:
            problems.append(f"drift:{path}")
    if aggregate(files) != commitment.get("pre_run_commitment_hash"):
        problems.append("commitment_aggregate")
    return problems


# ---------------------------------------------------------------- 授权门

def validate_clearance(document, commitment, actual_bindings):
    """结构/语义校验；签名真实性由 gate 另行要求。"""
    problems = []
    if document.get("decision") != DECISION:
        problems.append("decision")
    if document.get("schema_version") != "pyhost-context/reviewer001-S1b-clearance/1.0":
        problems.append("schema_version")
    if document.get("signed_by") != "Reviewer 001":
        problems.append("signed_by")
    if not document.get("signed_at_utc"):
        problems.append("signed_at_utc")
    auth = document.get("authorization") or {}
    if auth.get("S1b_execution") is not True:
        problems.append("S1b_not_authorized")
    for key in ("S2", "S3", "repro", "new_perturbation_runs"):
        if auth.get(key) is not False:
            problems.append(f"scope_not_bounded:{key}")
    scope = document.get("scope") or {}
    if scope.get("batch_id") != BATCH:
        problems.append("scope_batch")
    if scope.get("total_runs") != 450 or scope.get("main_runs") != 225 \
            or scope.get("control_runs") != 225:
        problems.append("scope_runs")
    if scope.get("schedule_sha256") != commitment.get("schedule_sha256"):
        problems.append("scope_schedule")
    if scope.get("state_root") != STATE_ROOT_REL:
        problems.append("scope_state_root")
    if document.get("pre_run_commitment_hash") != commitment.get("pre_run_commitment_hash"):
        problems.append("commitment_hash")
    if document.get("bindings") != actual_bindings:
        problems.append("binding_drift")
    return problems


def gate(clearance_path, commitment, actual_bindings):
    path = pathlib.Path(clearance_path)
    if not path.is_file():
        raise Refused("S1b_clearance_missing")
    document = read(path)
    problems = validate_clearance(document, commitment, actual_bindings)
    if problems:
        raise Refused("S1b_clearance_invalid:" + ",".join(problems))
    return {"ok": True, "clearance_sha256": sha(path),
            "commitment": commitment["pre_run_commitment_hash"],
            "state_root": str((WS / document["scope"]["state_root"]).resolve())}


# ---------------------------------------------------------------- 环境准备

def install_plan(toolchain):
    """把工具链清单解析为具体文件路径，并逐件核验哈希。"""
    files = []
    for item in toolchain["install_files"]:
        path = pathlib.Path(item["path"])
        if not path.is_absolute():
            path = WS / path
        if not path.is_file():
            raise Refused(f"install_file_missing:{toolchain['project']}:{item['filename']}")
        if sha(path) != item["sha256"] or path.stat().st_size != item["bytes"]:
            raise Refused(f"install_file_drift:{toolchain['project']}:{item['filename']}")
        files.append(str(path))
    return files


def install_argv(venvpy, paths):
    return ["uv", "pip", "install", "--python", str(venvpy), "--offline", "--no-index",
            "--no-deps", "--link-mode", "copy", *paths]


def python_version_of(venvpy, runner=subprocess.run):
    proc = runner([str(venvpy), "-c", "import platform;print(platform.python_version())"],
                  capture_output=True, text=True, timeout=120)
    version = (proc.stdout or "").strip()
    if proc.returncode != 0 or version != "3.11.9":
        raise TechnicalFailure(f"python_version_mismatch:{version}")
    return version


def inventory(venvpy, runner=subprocess.run):
    proc = runner([str(venvpy), "-c",
                   "import importlib.metadata as m;"
                   "print('\\n'.join(sorted(f'{d.metadata[\"Name\"]}=={d.version}' "
                   "for d in m.distributions())))"],
                  capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise TechnicalFailure("inventory_probe_failed")
    result = {}
    for line in proc.stdout.splitlines():
        if "==" in line:
            name, value = line.split("==", 1)
            result[canon(name)] = value
    return result


def ensure_venv(project, toolchain, venv_root, *, runner=subprocess.run,
                base_python=None, observer_install=None):
    """离线、文件级安装；成功返回 venv python 与安装清单。"""
    venv_root = pathlib.Path(venv_root)
    venv_dir = venv_root / project / "venv"
    venvpy = venv_dir / "bin" / "python"
    profile_path = venv_root / project / "profile.json"
    if venvpy.is_file() and profile_path.is_file():
        profile = read(profile_path)
        if profile.get("profile") != toolchain["runner_profile"] or \
                profile.get("toolchain_sha256") != toolchain_sha(toolchain):
            shutil.rmtree(venv_root / project, ignore_errors=True)
    if not venvpy.is_file():
        if base_python is None:
            _ensure_paths()
            import run_matrix as rm
            base_python = rm.BASE_PY
        created = runner([str(base_python), "-m", "venv", "--without-pip", str(venv_dir)],
                         capture_output=True, text=True, timeout=600)
        if created.returncode != 0:
            raise TechnicalFailure(f"venv_create_failed:{created.returncode}")
    python_version_of(venvpy, runner)
    paths = install_plan(toolchain)
    installed = runner(install_argv(venvpy, paths), capture_output=True, text=True, timeout=1800)
    if installed.returncode != 0:
        raise TechnicalFailure(f"exact_install_failed:{installed.returncode}:"
                               f"{installed.stderr[-400:]}")
    check = runner(["uv", "pip", "check", "--python", str(venvpy)],
                   capture_output=True, text=True, timeout=300)
    if check.returncode != 0:
        raise TechnicalFailure(f"dependency_check_failed:{check.returncode}")
    _ensure_paths()
    import run_matrix as rm
    observer = (observer_install or rm.install_matrix_observer)(str(venvpy))
    if toolchain["profile"]=="D-R1":
        config=venvpy.parent.parent/"lib/python3.11/site-packages/_pytest/config/__init__.py"
        declared=read(AMENDMENT_PATH)["pytest_config_source_sha256"]
        if not config.is_file() or sha(config)!=declared:
            raise TechnicalFailure("installed_pytest_metadata_source_drift")
    final = inventory(venvpy, runner)
    expected = {canon(k): str(v) for k, v in toolchain["expected_distributions"].items()}
    if final != expected:
        raise TechnicalFailure(f"installed_set_mismatch:{project}")
    write(profile_path, {"profile": toolchain["runner_profile"], "project": project,
                         "toolchain_sha256": toolchain_sha(toolchain),
                         "install_files": len(paths), "observer": observer,
                         "installed": final})
    return {"venv_python": str(venvpy), "installed": final, "observer": observer}


def toolchain_sha(toolchain):
    return hashlib.sha256(json.dumps(toolchain, sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()


def installed_plugin_names(venvpy, runner=subprocess.run):
    proc = runner([str(venvpy), "-c",
                   "import json,importlib.metadata as m;"
                   "print(json.dumps(sorted({ep.name for d in m.distributions() "
                   "for ep in d.entry_points if ep.group=='pytest11'})))"],
                  capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise TechnicalFailure("plugin_probe_failed")
    return set(json.loads(proc.stdout))


def check_plugin_declarations(toolchain, installed_names):
    """声明的 pytest11 名称必须都真实存在于 venv；S1a 参照档案保持零显式加载。"""
    missing = sorted(set(toolchain["plugin_loads"]) - set(installed_names))
    if missing:
        raise TechnicalFailure(f"declared_plugin_missing:{toolchain['project']}:"
                               f"{','.join(missing)}")
    return {"declared": list(toolchain["plugin_loads"]),
            "installed_pytest11": sorted(installed_names)}


# ---------------------------------------------------------------- 源码树

def sdist_payload(path):
    files = {}
    with tarfile.open(path) as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            parts = pathlib.PurePosixPath(member.name).parts
            rel = "/".join(parts[1:]) if len(parts) > 1 else parts[0]
            if rel in files:
                raise Refused("sdist_duplicate_member")
            files[rel] = archive.extractfile(member).read()
    return pathlib.Path(path), files


@contextlib.contextmanager
def explicit_sources(toolchains):
    """让矩阵源码树只从本批绑定的五份 sdist 物化/恢复。"""
    from matrix_runner import source_tree
    saved = {"sdist_bytes": source_tree.sdist_bytes,
             "artifact_identity": source_tree.artifact_identity}

    def provider(name):
        tool = toolchains[name]
        path = WS / tool["source"]["path"]
        if sha(path) != tool["source"]["sha256"]:
            raise Refused(f"sdist_drift:{name}")
        return sdist_payload(path)

    source_tree.sdist_bytes = provider
    source_tree.artifact_identity = lambda name: {
        "name": name, "version": toolchains[name]["version"],
        "artifact_path": toolchains[name]["source"]["path"],
        "sha256": toolchains[name]["source"]["sha256"], "identity_verified": True}
    try:
        yield
    finally:
        source_tree.sdist_bytes = saved["sdist_bytes"]
        source_tree.artifact_identity = saved["artifact_identity"]


def prepare_environment(roster, toolchains, state_root, *, runner=subprocess.run):
    """准备五个隔离 venv 与源码树；不运行任何测试。"""
    _ensure_paths()
    import run_matrix as rm
    records = {}
    with explicit_sources(toolchains["projects"]):
        for row in roster["projects"]:
            project = row["project"]
            tool = toolchains["projects"][project]
            record = ensure_venv(project, tool, pathlib.Path(state_root) / "venvs",
                                 runner=runner)
            plugins = check_plugin_declarations(
                tool, installed_plugin_names(record["venv_python"], runner))
            record["plugins"] = plugins
            context = rm.run_context(pathlib.Path(state_root) / "rounds" / project,
                                     project, project)
            if not (pathlib.Path(context["source_path"]).is_dir()):
                rm.prepare_context(context, record["venv_python"])
            records[project] = {"venv": record, "context": {
                key: str(value) for key, value in context.items() if key != "slots"}}
    return records


# ---------------------------------------------------------------- 判定

def condition_for(protocol, condition_id):
    if condition_id == "B0":
        return None
    for condition in protocol["core_conditions"]:
        if condition["id"] == condition_id:
            return condition
    raise Refused(f"unknown_condition:{condition_id}")


def evaluate_transition(toolchain, condition_id, spec, launch_env, post_init_env,
                        snapshot_env):
    """按项目档案评估三视图；D-R1 使用签署的派生规则，其余保持原规则。"""
    _ensure_paths()
    import launch
    if toolchain["profile"] != "D-R1":
        return launch.assess_startup_transition(spec, launch_env, post_init_env,
                                                snapshot_env, condition_id)
    sys.path.insert(0, str(INPUTS))
    import pytest_runner_transition as policy
    return policy.assess(launch.assess_startup_transition, spec, launch_env,
                         post_init_env, snapshot_env, condition_id,
                         declared_pytest_version=toolchain["pytest_version"],
                         pytest_config_source_sha256=
                         toolchains_amendment()["pytest_config_source_sha256"])


def toolchains_amendment():
    return read(AMENDMENT_PATH)


# ---------------------------------------------------------------- 执行

def validate_canonical_schedule(roster, schedule):
    """重演冻结标签及全部实际消费字段；不更改任何实验设计。"""
    seed = "pyhost-context-S1b-v1-20261019"
    conditions = ["B0", "K01_TZ", "K02_LC_ALL", "K03_LANG", "K04_HASH",
                  "K05_NO_COLOR", "K06_TERM", "K07_COLUMNS", "K08_LINES"]
    expected = []
    for p in roster["projects"]:
        for arm in ("main", "control"):
            for condition in conditions if arm == "main" else ["B0"]:
                for repeat in range(1, 6 if arm == "main" else 46):
                    pos=p["position"]
                    label=f"{arm}|pos={pos:02d}|{p['project']}|{condition}|r={repeat}"
                    run_id=(f"s1b-main-p{pos:02d}-{condition}-r{repeat}" if arm=="main"
                            else f"s1b-ctl-p{pos:02d}-B0-r{repeat:02d}")
                    expected.append(dict(arm=arm, project=p["project"], position=pos,
                        condition=condition, repeat=repeat, label=label, run_id=run_id,
                        label_sha256=hashlib.sha256(f"{seed}|{label}".encode()).hexdigest()))
    expected.sort(key=lambda e:(e["label_sha256"],e["run_id"]))
    for i,e in enumerate(expected,1): e["order"]=i
    problems=[]
    if schedule.get("batch_id") != BATCH or schedule.get("seed") != seed:
        problems.append("canonical_batch_or_seed")
    if schedule.get("entries") != expected:
        problems.append("canonical_schedule_consumed_fields")
    return problems


def run_assessment(toolchain, entry, context, b0, condition, result):
    """run_one 已清空固定槽位；只读取该轮返回的、哈希一致的归档。"""
    _ensure_paths()
    from matrix_runner import launch, slots as slots_mod
    logs=pathlib.Path(context["logs"])
    boot=logs/f"{entry['run_id']}_boot.stdout.txt"
    post_init=None
    boot_data={}
    if boot.is_file():
        try:
            boot_data=json.loads(boot.read_text().strip().splitlines()[-1])
            post_init=boot_data.get("post_init_env")
        except (ValueError, IndexError): pass
    snapshot=None
    archived=(result.get("archived_slots") or {}).get("jsonl")
    if archived:
        path=pathlib.Path(archived["path"])
        expected_path=logs/f"{entry['run_id']}.jsonl"
        if path.resolve()!=expected_path.resolve() or not path.is_file():
            raise TechnicalFailure("archived_jsonl_identity")
        digest=sha(path)
        if digest!=archived.get("sha256") or digest!=result.get("jsonl_sha256"):
            raise TechnicalFailure("archived_jsonl_hash")
        if path.stat().st_size!=archived.get("bytes"):
            raise TechnicalFailure("archived_jsonl_size")
        snapshots=[]
        for line in path.read_text().splitlines():
            try: row=json.loads(line)
            except ValueError: continue  # malformed count remains in the raw run result
            if row.get("$report_type")=="RuntimeSnapshot": snapshots.append(row.get("environment"))
        if len(snapshots)==1: snapshot=snapshots[0]
    spec=launch.build_round_spec(b0,condition,
        {"<frozen-run-root>":str(context["run_root"]),
         "<project-cwd>":str(context["source_path"])},
        slots_mod.slots_for(pathlib.Path(context["run_root"])))
    env=launch.materialize(spec)
    transition=evaluate_transition(toolchain,entry["condition"],spec,env,post_init,snapshot)
    from matrix_runner.validity import run_validity
    launch_problems=list((result.get("launch_env_three_view") or {}).get("launch_problems") or [])
    if "launch_spec" in result and result["launch_spec"]!=spec:
        launch_problems.append({"kind":"launch_spec_mismatch"})
    combined=run_validity(launch_problems,transition,
                         launch.interpreter_flags_violation(boot_data.get("flags")))
    raw_failures=set((result.get("perturbation") or {}).get("failures") or [])
    raw_transition=(result.get("launch_env_three_view") or {}).get("transition") or {}
    # Only the registered transition may change; flags/launch/unknown validity failures survive.
    residual=raw_failures-set(raw_transition.get("failures") or [])
    combined["failures"]=sorted(set(combined["failures"])|residual)
    combined["perturbation_valid"]=not combined["failures"]
    return {**transition,"startup_transition_valid":transition["valid"],
            "valid":combined["perturbation_valid"],"combined_run_validity":combined,
            "original_perturbation":result.get("perturbation")}


def check_ledger(ledger, schedule, commitment, state_root, *, verify_results=True):
    """完成=已保存观测；不等于测试通过或会话完整。"""
    problems=[]
    expected={e["run_id"]:e for e in schedule["entries"]}
    for key,value in (("batch_id",BATCH),("commitment",commitment["pre_run_commitment_hash"]),
                      ("schedule_sha256",schedule["schedule_sha256"])):
        if ledger.get(key)!=value: problems.append("resume_"+key)
    if ledger.get("plan_run_ids") != list(expected): problems.append("resume_plan")
    if [r.get("run_id") for r in ledger.get("records",[])] != list(expected)[:len(ledger.get("records",[]))]:
        problems.append("resume_nonprefix_records")
    seen=set()
    for row in ledger.get("records",[]):
        run_id=row.get("run_id")
        if run_id in seen: problems.append("resume_duplicate:"+str(run_id))
        seen.add(run_id)
        entry=expected.get(run_id)
        if entry is None:
            problems.append("resume_unknown:"+str(run_id));continue
        for key in ("order","project","position","arm","condition","repeat"):
            if row.get(key)!=entry[key]: problems.append(f"resume_identity:{run_id}:{key}")
        if row.get("batch_id")!=BATCH or row.get("commitment")!=commitment["pre_run_commitment_hash"]:
            problems.append("resume_record_batch:"+run_id)
        if verify_results:
            for key in ("result","assessment"):
                item=row.get(key) or {}
                path=pathlib.Path(item.get("path",""))
                base=pathlib.Path(state_root).resolve()
                if not path.is_absolute():path=base/path
                if not path.resolve().is_relative_to(base) or not path.is_file():
                    problems.append(f"resume_{key}_missing:{run_id}")
                elif sha(path)!=item.get("sha256") or path.stat().st_size!=item.get("bytes"):
                    problems.append(f"resume_{key}_drift:{run_id}")
                elif key=="result":
                    payload=read(path)
                    for field in ("run_id","project","condition","repeat","position"):
                        if payload.get(field)!=entry[field]:
                            problems.append(f"resume_payload_identity:{run_id}:{field}")
    return problems


def ledger_snapshot(ledger,schedule,status):
    ids={r["run_id"] for r in ledger["records"]}
    ledger["status"]=status
    ledger["planned"]=len(schedule["entries"])
    ledger["recorded"]=len(ledger["records"])
    ledger["not_run_ids"]=[e["run_id"] for e in schedule["entries"] if e["run_id"] not in ids]
    ledger["not_run_count"]=len(ledger["not_run_ids"])
    return ledger


def run_schedule(roster,toolchains,schedule,commitment,*,state_root,clearance,
                 runner=None,prepare=True,max_runs=None):
    if max_runs is not None and max_runs<0: raise Refused("negative_max_runs")
    problems=validate_inputs(roster,toolchains,schedule)+verify_commitment(commitment)
    if problems:raise Refused("input_invalid:"+",".join(problems))
    approved=gate(clearance,commitment,commitment["files"])
    if pathlib.Path(state_root).resolve()!=pathlib.Path(approved["state_root"]).resolve():
        raise Refused("state_root_outside_signed_batch")
    _ensure_paths()
    import run_matrix as rm
    import run_extension as rx
    protocol,b0=rx.source_protocol([f"{p['project']}-{p['version']}" for p in roster["projects"]])
    state_root=pathlib.Path(state_root).resolve()
    state_root.mkdir(parents=True,exist_ok=True)
    with (state_root/"execution.lock").open("a") as mutex:
        try:fcntl.flock(mutex,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise Refused("S1b_already_running")
        ledger_path=state_root/"ledger.json"
        ledger=read(ledger_path) if ledger_path.is_file() else {
            "batch_id":BATCH,"commitment":commitment["pre_run_commitment_hash"],
            "schedule_sha256":schedule["schedule_sha256"],
            "plan_run_ids":[e["run_id"] for e in schedule["entries"]],"records":[],"attempts":[]}
        problems=check_ledger(ledger,schedule,commitment,state_root)
        if problems:raise Refused("ledger_invalid:"+",".join(problems))
        if any(a.get("status") in ("started","runner_returned","interrupted","instrument_error")
               for a in ledger.get("attempts",[])):
            raise Refused("unresolved_attempt_preserved_no_automatic_replay")
        done={r["run_id"]:r for r in ledger["records"]}
        if len(done)==450:
            return {"ok":True,"recorded":450,"executed":0,"status":"completed","ledger":str(ledger_path)}
        ledger["ledger_consistency"]={"ok":True,"problems":[]}
        write(ledger_path,ledger_snapshot(ledger,schedule,"running"))
        executed=0
        current=None
        try:
            environments=prepare_environment(roster,toolchains,state_root) if prepare else {}
            with explicit_sources(toolchains["projects"]):
                for entry in schedule["entries"]:
                    if entry["run_id"] in done:continue
                    if max_runs is not None and executed>=max_runs:break
                    project=entry["project"];tool=toolchains["projects"][project]
                    current=dict(entry)
                    current["attempt_index"]=1+sum(a["run_id"]==entry["run_id"] for a in ledger["attempts"])
                    current["status"]="started"
                    ledger["attempts"].append(current)
                    write(ledger_path,ledger_snapshot(ledger,schedule,"running"))
                    context=rm.run_context(state_root/"rounds"/project,project,project)
                    condition=condition_for(protocol,entry["condition"])
                    call=runner or rm.run_one
                    result=call(protocol=protocol,b0=b0,context=context,condition=condition,
                        run_id=entry["run_id"],phase="s1b",repeat=entry["repeat"],position=entry["position"],
                        commitment=commitment,venvpy=environments[project]["venv"]["venv_python"] if environments else None,
                        plugin_loads=tuple(tool["plugin_loads"]),entry_argv=("-m","pytest",*tool["extra_pytest_args"]),
                        extra_meta={"s1b_arm":entry["arm"],"s1b_order":entry["order"],"profile":tool["profile"],
                            "s1b_commitment":commitment["pre_run_commitment_hash"],
                            "clearance_sha256":approved["clearance_sha256"]})
                    target=state_root/"results"/entry["run_id"]/f"attempt-{current['attempt_index']:03d}.json"
                    if target.exists():raise TechnicalFailure("result_would_overwrite")
                    write(target,result)  # complete raw run_one return, including stages/cleanup/load
                    current["raw_result"]={"path":str(target.relative_to(state_root)),"sha256":sha(target),"bytes":target.stat().st_size}
                    current["status"]="runner_returned"
                    write(ledger_path,ledger)
                    for key,want in (("run_id",entry["run_id"]),("project",project),
                        ("condition",entry["condition"]),("repeat",entry["repeat"]),("position",entry["position"])):
                        if result.get(key)!=want:raise TechnicalFailure("returned_identity:"+key)
                    assessment=run_assessment(tool,entry,context,b0,condition,result)
                    assessment_path=target.with_suffix(".assessment.json")
                    write(assessment_path,assessment)
                    row={k:entry[k] for k in ("run_id","order","project","position","arm","condition","repeat")}
                    row.update(batch_id=BATCH,commitment=commitment["pre_run_commitment_hash"],
                        result=current["raw_result"],assessment={"path":str(assessment_path.relative_to(state_root)),
                            "sha256":sha(assessment_path),"bytes":assessment_path.stat().st_size},
                        returncode=result.get("returncode"),timed_out=result.get("timed_out"),
                        collected=result.get("collected"),duration_seconds=result.get("duration_seconds"),
                        jsonl_sha256=result.get("jsonl_sha256"),
                        raw_perturbation_valid=(result.get("perturbation") or {}).get("perturbation_valid"),
                        perturbation_valid=assessment.get("valid"),
                        runner_metadata_transition_accepted=assessment.get("runner_metadata_transition_accepted",False))
                    ledger["records"].append(row);done[entry["run_id"]]=row
                    current["status"]="recorded";current=None;executed+=1
                    write(ledger_path,ledger_snapshot(ledger,schedule,"running"))
        except BaseException as exc:
            if current is not None:
                current["status"]="interrupted" if isinstance(exc,KeyboardInterrupt) else "instrument_error"
                current["error_type"]=type(exc).__name__;current["error"]=str(exc)
            ledger["abort"]={"type":type(exc).__name__,"message":str(exc),"root_cause":"unresolved"}
            write(ledger_path,ledger_snapshot(ledger,schedule,"aborted"))
            raise
        problems=check_ledger(ledger,schedule,commitment,state_root)
        ledger["ledger_consistency"]={"ok":not problems,"problems":problems}
        status="completed" if not problems and len(done)==450 else "aborted" if problems else "partial"
        write(ledger_path,ledger_snapshot(ledger,schedule,status))
        if problems:raise TechnicalFailure("ledger_self_check:"+",".join(problems))
        return {"ok":True,"recorded":len(done),"executed":executed,"status":status,"ledger":str(ledger_path)}


# ---------------------------------------------------------------- CLI

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true", help="离线核验输入与承诺")
    parser.add_argument("--prepare", action="store_true", help="准备五个隔离环境（需签署放行）")
    parser.add_argument("--run", action="store_true", help="执行 450 轮（需签署放行）")
    parser.add_argument("--state-root", type=pathlib.Path)
    parser.add_argument("--clearance", type=pathlib.Path)
    parser.add_argument("--max-runs", type=int)
    args = parser.parse_args(argv)
    roster, toolchains, schedule, commitment = load_inputs()
    if args.status:
        actual = verify_commitment(commitment)
        problems = validate_inputs(roster, toolchains, schedule) + actual
        print(json.dumps({"ok": not problems, "problems": problems,
                          "commitment": commitment["pre_run_commitment_hash"],
                          "schedule": schedule["schedule_sha256"],
                          "runs": len(schedule["entries"])},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if not problems else 1
    if not (args.prepare or args.run):
        parser.error("需要 --status、--prepare 或 --run")
    if args.state_root is None or args.clearance is None:
        parser.error("--state-root 与 --clearance 必填")
    if args.prepare and not args.run:
        approved = gate(args.clearance, commitment, commitment["files"])
        if args.state_root.resolve()!=pathlib.Path(approved["state_root"]).resolve():
            raise Refused("state_root_outside_signed_batch")
        problems=validate_inputs(roster,toolchains,schedule)+verify_commitment(commitment)
        if problems:raise Refused("input_invalid:"+",".join(problems))
        prepared=prepare_environment(roster,toolchains,args.state_root)
        print(json.dumps({"authorization":approved,"prepared":prepared},ensure_ascii=False,sort_keys=True))
        return 0
    result = run_schedule(roster, toolchains, schedule, commitment,
                          state_root=args.state_root, clearance=args.clearance,
                          max_runs=args.max_runs)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.path[:0] = [str(PROJECT / "matrix/tooling"), str(PROJECT / "tooling"),
                    str(PROJECT / "tooling/admission_runner"),
                    str(PROJECT / "matrix/tooling/matrix_runner")]
    try:
        code=main()
    except Refused as exc:
        print(json.dumps({"status":"refused","reason":str(exc)},ensure_ascii=False));code=2
    except (TechnicalFailure,Exception) as exc:
        print(json.dumps({"status":"instrument_error","reason":str(exc)},ensure_ascii=False));code=3
    raise SystemExit(code)
