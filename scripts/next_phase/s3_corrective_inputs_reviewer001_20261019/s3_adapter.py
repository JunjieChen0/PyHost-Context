"""ClawsGO Science Agent：S3 240 行适配器（修订版）。

按 Reviewer 001 的 R1—R5 修正：

* 复用冻结 B0 启动环境（三态显式、TERM/LC_ALL/插件自动加载按原规格）；
* boot / RuntimeSnapshot / 启动环境三视图核对，网络观察器与 round meta 实际接线；
* 进程组清理、会话与返回码一致性、JSONL 完整性与收集状态进入可用性判定；
* 见证身份、产品根、退出码与必需字段全部核验；
* 放行门复算实际行内容、承诺与整包指纹；准备与执行分别按授权范围门控；
* 账本先登记完整计划；已登记尝试一律保留，不自动重跑、不覆盖原始证据。
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import time
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import s3_common as common  # noqa: E402

WS = HERE.parents[3] if len(HERE.parents) > 3 else HERE
ROWS_PATH = HERE / "S3_ROWS_20261019.json"
COMMITMENT_PATH = HERE / "S3_PRE_RUN_COMMITMENT_20261019.json"
INPUTS = HERE / "inputs"
TIMEOUT_SECONDS = 240
BATCH_ID = "next-S3-20261019"
FROZEN_INTERPRETER = (WS / "pycompat-env-select/runtime/interpreters/"
                      "cpython-3.11.9-linux-x86_64-gnu/bin/python3.11")

# 冻结 B0 启动环境（reference/b0_environment.json）的逐键三态。
B0_SPEC = {
    "TZ": ("set", "UTC"),
    "LC_ALL": ("unset", None),
    "LC_CTYPE": ("unset", None),
    "LANG": ("set", "C.UTF-8"),
    "LANGUAGE": ("unset", None),
    "PYTHONHASHSEED": ("set", "0"),
    "PYTHONCOERCECLOCALE": ("unset", None),
    "PYTHONUTF8": ("unset", None),
    "NO_COLOR": ("unset", None),
    "TERM": ("set", "xterm-256color"),
    "COLUMNS": ("unset", None),
    "LINES": ("unset", None),
}
CONTROL_KEYS = {
    "HOME": "{run}/home", "TMPDIR": "{run}/tmp", "PATH": "{run}/bin:/usr/bin:/bin",
    "PWD": "{cwd}", "USER": "user", "SHELL": "/bin/sh",
    "PYTHONPATH": None, "PYTHONWARNINGS": None, "PYTHONIOENCODING": None,
    "PYTEST_ADDOPTS": None, "PYTEST_PLUGINS": None,
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "CI": None,
    "FORCE_COLOR": None, "CLICOLOR": None, "CLICOLOR_FORCE": None, "PY_COLORS": None,
}
HARNESS_KEYS = ("PYHOST_JSONL_PATH", "PYHOST_NETLOG", "PYHOST_ROUND_META")
BOOT_CODE = (
    "import json,os,platform,sys;"
    "print(json.dumps({'python':platform.python_version(),"
    "'implementation':platform.python_implementation(),"
    "'flags':{'utf8_mode':sys.flags.utf8_mode,"
    "'ignore_environment':sys.flags.ignore_environment,"
    "'isolated':sys.flags.isolated},"
    "'env':dict(os.environ)},sort_keys=True))"
)
IGNORED_TREE_DIRS = {"__pycache__", ".pytest_cache"}


def sha(path) -> str:
    return common.sha256_file(path)


def read_json(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def write_json(path, payload):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def canonical_sha(payload) -> str:
    return common.sha256_bytes(json.dumps(payload, sort_keys=True,
                                          ensure_ascii=False).encode("utf-8"))


def rows_hash(document) -> str:
    return canonical_sha(document["suite_rows"] + document["witness_rows"])


def load_rows():
    document = read_json(ROWS_PATH)
    rows = document["suite_rows"] + document["witness_rows"]
    rows.sort(key=lambda row: (row["call_kind"] != "suite", row["order"]))
    return document, rows


def run_id_of(row) -> str:
    return f"s3-{row['call_kind'][:1]}-{row['order']:03d}-{row['label_sha256'][:8]}"


# ------------------------------------------------------------------ environment

def build_environment(row, run_dir) -> dict:
    run_dir = pathlib.Path(run_dir)
    env = {}
    for key, (state, value) in B0_SPEC.items():
        if state == "set":
            env[key] = value
        elif state == "empty":
            env[key] = ""
    cwd = str(run_dir / "src")
    for key, template in CONTROL_KEYS.items():
        if template is None:
            continue
        env[key] = template.replace("{run}", str(run_dir)).replace("{cwd}", cwd)
    context = row["context_environment"]
    key = row["context_key"]
    if context["state"] == "unset":
        env.pop(key, None)
    elif context["state"] == "empty":
        env[key] = ""
    else:
        env[key] = str(context["value"])
    for extra_key, value in (row.get("extra_environment") or {}).items():
        env[extra_key] = str(value)
    env["PYHOST_NETLOG"] = str(run_dir / "netlog.jsonl")
    env["PYHOST_ROUND_META"] = str(run_dir / "round_meta.json")
    env["PYHOST_JSONL_PATH"] = str(run_dir / "report.jsonl")
    return env


def environment_spec(env: dict) -> dict:
    return {key: ({"state": "empty", "value": ""} if env[key] == ""
                  else {"state": "set", "value": env[key]}) for key in sorted(env)}


def declared_snapshot_environment(row: dict, launch: dict) -> dict:
    """仅落实冻结 B/R1 在 conftest 导入期进入 Z 的已有声明；不豁免其它漂移。"""
    expected = dict(launch)
    if row["case"] == "B" and row.get("suite_variant") == "R1_global_fix":
        expected["COLUMNS"] = "80"
    return expected


# ------------------------------------------------------------------ process

def _popen(argv, cwd, env, timeout):
    started = time.time()
    process = subprocess.Popen(argv, cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()
    cleaned = True
    try:
        os.killpg(process.pid, 0)
        cleaned = False
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    except ProcessLookupError:
        cleaned = True
    except PermissionError:
        cleaned = False
    return {"returncode": process.returncode, "stdout": stdout, "stderr": stderr,
            "timed_out": timed_out, "duration_seconds": round(time.time() - started, 3),
            "child_pid": process.pid, "process_group_cleanup": cleaned}


def boot_probe(interpreter, cwd, env, run) -> dict:
    outcome = run([str(interpreter), "-c", BOOT_CODE], cwd, env, 60)
    data = None
    for line in reversed((outcome.get("stdout") or "").strip().splitlines()):
        try:
            data = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if not isinstance(data, dict):
        return {"ok": False, "error": "boot_probe_unparsed",
                "returncode": outcome.get("returncode")}
    declared = {key: value for key, value in env.items()}
    observed = data.get("env") or {}
    return {
        "ok": data.get("python") == "3.11.9" and observed == declared,
        "python": data.get("python"),
        "implementation": data.get("implementation"),
        "flags": data.get("flags"),
        "env_matches_launch": observed == declared,
        "unexpected_keys": sorted(set(observed) - set(declared)),
        "missing_keys": sorted(set(declared) - set(observed)),
        "returncode": outcome.get("returncode"),
    }


# ------------------------------------------------------------------ report

def parse_report(text: str) -> dict:
    nodes: dict[str, dict] = {}
    collected: list[str] = []
    session = None
    snapshot = None
    collection_finished = False
    collection_reports = []
    malformed = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        kind = record.get("$report_type")
        if kind == "CollectionItem":
            collected.append(record["nodeid"])
        elif kind == "CollectionFinished":
            collected = record.get("collected", collected)
            collection_finished = True
        elif kind == "CollectReport":
            collection_reports.append({"nodeid": record.get("nodeid"),
                                       "outcome": record.get("outcome")})
        elif kind == "TestReport":
            entry = nodes.setdefault(record["nodeid"], {"phase_details": {},
                                                        "longrepr": None})
            entry["phase_details"][record.get("when")] = {
                "outcome": record.get("outcome"),
                "wasxfail": record.get("wasxfail"),
                "longrepr": record.get("longrepr"),
            }
            if record.get("longrepr") is not None:
                entry["longrepr"] = record["longrepr"]
        elif kind == "SessionFinished":
            session = record
        elif kind == "RuntimeSnapshot":
            snapshot = record
    return {"node_outcomes": nodes, "collected": collected, "session": session,
            "snapshot": snapshot, "collection_finished": collection_finished,
            "collection_reports": collection_reports,
            "malformed_jsonl_lines": malformed}


def tree_delta(before_root, before_hashes, after_root) -> dict:
    after = {}
    for path in sorted(p for p in pathlib.Path(after_root).rglob("*") if p.is_file()):
        relative = path.relative_to(after_root)
        if any(part in IGNORED_TREE_DIRS for part in relative.parts):
            continue
        after[relative.as_posix()] = common.sha256_file(path)
    added = sorted(set(after) - set(before_hashes))
    removed = sorted(set(before_hashes) - set(after))
    changed = sorted(key for key in set(after) & set(before_hashes)
                     if after[key] != before_hashes[key])
    return {"added": added, "removed": removed, "changed": changed,
            "ok": not (removed or changed)}


def source_hashes(root) -> dict:
    root = pathlib.Path(root)
    result = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root)
        if any(part in IGNORED_TREE_DIRS for part in relative.parts):
            continue
        result[relative.as_posix()] = common.sha256_file(path)
    return result


# ------------------------------------------------------------------ run kinds

def execute_suite(row, run_dir: pathlib.Path, interpreter: pathlib.Path,
                  runner=None) -> dict:
    run = runner or _popen
    blob = (HERE / row["tree"]["path"]).read_bytes()
    if common.sha256_bytes(blob) != row["tree"]["tar_sha256"]:
        raise RuntimeError(f"tarball_hash_mismatch:{row['row_key']}")
    source = run_dir / "src"
    if source.exists():
        shutil.rmtree(source)
    common.unpack_tree(blob, source)
    before_hashes = source_hashes(source)
    before = common.tree_hash(source)
    if before != row["tree"]["tree_sha256"]:
        raise RuntimeError(f"tree_hash_mismatch_before:{row['row_key']}")
    env = build_environment(row, run_dir)
    write_json(run_dir / "round_meta.json", {
        "batch_id": BATCH_ID, "run_id": run_id_of(row), "row_key": row["row_key"],
        "call_kind": "suite", "commitment_sha256": sha(COMMITMENT_PATH)
        if COMMITMENT_PATH.exists() else None,
    })
    boot = boot_probe(interpreter, source, env, run)
    argv = [str(interpreter), "-m", "pytest", "-p", "jsonl_producer",
            "-p", "no:cacheprovider", "tests"]
    outcome = run(argv, source, env, TIMEOUT_SECONDS)
    delta = tree_delta(source, before_hashes, source)
    report_path = run_dir / "report.jsonl"
    text = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    parsed = parse_report(text)
    (run_dir / "stdout.txt").write_text(outcome.get("stdout") or "", encoding="utf-8")
    (run_dir / "stderr.txt").write_text(outcome.get("stderr") or "", encoding="utf-8")
    (run_dir / "boot.json").write_text(
        json.dumps(boot, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    returncode = outcome.get("returncode")
    snapshot = parsed["snapshot"] or {}
    snapshot_env = snapshot.get("environment") or {}
    snapshot_match = snapshot_env == env
    expected_snapshot = declared_snapshot_environment(row, env)
    snapshot_declared_match = snapshot_env == expected_snapshot
    round_meta = (snapshot.get("round_meta") or {})
    round_meta_match = round_meta.get("row_key") == row["row_key"]
    netlog_path = run_dir / "netlog.jsonl"
    netlog_lines = netlog_path.read_text(encoding="utf-8").splitlines() if netlog_path.exists() else []
    init_pids, end_pids = [], []
    for line in netlog_lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = record.get("kind") or record.get("event")
        if kind == "observer_init":
            init_pids.append(record.get("pid"))
        elif kind == "observer_end":
            end_pids.append(record.get("pid"))
    pytest_pid = outcome.get("child_pid")
    network = {
        "netlog_present": netlog_path.exists(),
        "observer_init_records": len(init_pids),
        "observer_end_records": len(end_pids),
        "pytest_pid": pytest_pid,
        "pytest_pid_seen_init": pytest_pid in init_pids if pytest_pid else None,
        "pytest_pid_seen_end": pytest_pid in end_pids if pytest_pid else None,
        "ok": bool(netlog_path.exists() and type(pytest_pid) is int and pytest_pid > 0
                   and pytest_pid in init_pids and pytest_pid in end_pids),
    }
    problems = []
    if not text:
        problems.append("missing_jsonl")
    if not parsed["session"]:
        problems.append("missing_session")
    if parsed["session"] and parsed["session"].get("exitstatus") != returncode:
        problems.append("session_exit_mismatch")
    if parsed["malformed_jsonl_lines"]:
        problems.append("malformed_jsonl")
    if not isinstance(returncode, int) or returncode < 0:
        problems.append("signal_or_no_returncode")
    if not parsed["collection_finished"]:
        problems.append("missing_collection_finished")
    if returncode == 2:
        problems.append("collection_error")
    if any(report.get("outcome") != "passed" for report in parsed["collection_reports"]):
        problems.append("collection_error")
    if parsed["collection_finished"] and not parsed["collected"]:
        problems.append("empty_collection")
    if not snapshot:
        problems.append("missing_runtime_snapshot")
    elif not snapshot_declared_match:
        problems.append("snapshot_environment_mismatch")
    if not round_meta_match:
        problems.append("round_meta_mismatch")
    if not boot.get("ok"):
        problems.append("boot_probe_failed")
    if not delta["ok"]:
        problems.append("source_tree_changed")
    if not outcome.get("process_group_cleanup", False):
        problems.append("process_group_not_clean")
    if not network["ok"]:
        problems.append("network_evidence_missing")
    if outcome.get("timed_out"):
        status = "timeout"
    elif problems:
        status = "failed"
    else:
        status = "completed"
    return {
        "row_key": row["row_key"], "call_kind": "suite", "status": status,
        "problems": problems,
        "returncode": returncode, "timed_out": bool(outcome.get("timed_out")),
        "duration_seconds": outcome.get("duration_seconds"),
        "process_group_cleanup": bool(outcome.get("process_group_cleanup")),
        "tree_before_sha256": before,
        "tree_after_sha256": common.tree_hash(source),
        "tree_after_ok": delta["ok"], "tree_delta": delta,
        "environment": env, "environment_spec": environment_spec(env),
        "declared_snapshot_environment": expected_snapshot,
        "launch_to_snapshot_delta": {
            key: {"launch": env.get(key), "snapshot": snapshot_env.get(key)}
            for key in sorted(set(env) | set(snapshot_env))
            if env.get(key) != snapshot_env.get(key) or (key in env) != (key in snapshot_env)},
        "argv": argv, "cwd": str(source), "boot": boot,
        "boot_views": {
            "launch_matches_spec": True,
            "boot_env_matches_launch": bool(boot.get("env_matches_launch")),
            "snapshot_env_matches_launch": bool(snapshot_match),
            "snapshot_env_matches_declared": bool(snapshot_declared_match),
            "round_meta_matches": bool(round_meta_match),
        },
        "network": network,
        "node_outcomes": parsed["node_outcomes"], "collected": parsed["collected"],
        "collection_finished": parsed["collection_finished"],
        "collection_reports": parsed["collection_reports"],
        "session": parsed["session"], "snapshot": parsed["snapshot"],
        "malformed_jsonl_lines": parsed["malformed_jsonl_lines"],
        "report_jsonl_sha256": common.sha256_bytes(text.encode()) if text else None,
    }


def execute_witness(row, run_dir: pathlib.Path, interpreter: pathlib.Path,
                    runner=None) -> dict:
    run = runner or _popen
    blob = (HERE / row["tree"]["path"]).read_bytes()
    if common.sha256_bytes(blob) != row["tree"]["tar_sha256"]:
        raise RuntimeError(f"tarball_hash_mismatch:{row['row_key']}")
    source = run_dir / "src"
    if source.exists():
        shutil.rmtree(source)
    common.unpack_tree(blob, source)
    before_hashes = source_hashes(source)
    before = common.tree_hash(source)
    if before != row["tree"]["tree_sha256"]:
        raise RuntimeError(f"tree_hash_mismatch_before:{row['row_key']}")
    env = build_environment(row, run_dir)
    witness_out = run_dir / "witness.json"
    argv = [str(interpreter), str(HERE / "witness_probes.py"),
            "--case", row["case"], "--product-root", str(source),
            "--json-out", str(witness_out)]
    outcome = run(argv, source, env, TIMEOUT_SECONDS)
    delta = tree_delta(source, before_hashes, source)
    payload = read_json(witness_out) if witness_out.exists() else None
    (run_dir / "stdout.txt").write_text(outcome.get("stdout") or "", encoding="utf-8")
    (run_dir / "stderr.txt").write_text(outcome.get("stderr") or "", encoding="utf-8")
    problems = []
    returncode = outcome.get("returncode")
    if returncode != 0:
        problems.append("witness_nonzero_exit")
    if outcome.get("timed_out"):
        problems.append("witness_timeout")
    if not outcome.get("process_group_cleanup", False):
        problems.append("witness_process_group_not_clean")
    if not isinstance(payload, dict):
        problems.append("witness_payload_missing")
    else:
        if payload.get("case") != row["case"]:
            problems.append("witness_case_mismatch")
        root = payload.get("product_root")
        if not root or pathlib.Path(root).resolve() != source.resolve():
            problems.append("witness_product_root_mismatch")
        if not isinstance(payload.get("errors"), dict) or payload["errors"]:
            problems.append("witness_probe_errors")
    if not delta["ok"]:
        problems.append("source_tree_changed")
    return {
        "row_key": row["row_key"], "call_kind": "witness",
        "status": "completed" if not problems else "failed",
        "problems": problems,
        "returncode": returncode, "timed_out": bool(outcome.get("timed_out")),
        "duration_seconds": outcome.get("duration_seconds"),
        "process_group_cleanup": bool(outcome.get("process_group_cleanup")),
        "tree_before_sha256": before,
        "tree_after_sha256": common.tree_hash(source),
        "tree_after_ok": delta["ok"], "tree_delta": delta,
        "environment": env, "argv": argv, "cwd": str(source),
        "witness": payload,
    }


def dispatch(row, state_root: pathlib.Path, interpreters: dict, runner=None):
    base = state_root / "runs" / run_id_of(row)
    attempt = 1
    if base.exists():
        suffix = time.strftime("orphan-%Y%m%dT%H%M%SZ", time.gmtime())
        base.rename(base.with_name(base.name + "-" + suffix))
        attempt = 2
    run_dir = base / f"attempt-{attempt:03d}"
    for name in ("home", "tmp", "bin"):
        (run_dir / name).mkdir(parents=True, exist_ok=True)
    if row["call_kind"] == "suite":
        record = execute_suite(row, run_dir, interpreters[row["project"]], runner)
    else:
        record = execute_witness(row, run_dir, interpreters[row["project"]], runner)
    record["attempt"] = attempt
    record["run_dir"] = str(run_dir)
    return record


# ------------------------------------------------------------------ clearance

def clearance_problems(clearance_path, mode: str = "run") -> list[str]:
    problems = []
    if not clearance_path:
        return ["clearance_missing"]
    path = pathlib.Path(clearance_path)
    if not path.is_file():
        return [f"clearance_not_found:{path}"]
    document = read_json(path)
    if document.get("batch_id") != BATCH_ID:
        problems.append("clearance_batch_mismatch")
    if document.get("authorized") is not True:
        problems.append("clearance_not_authorized")
    if mode == "run" and document.get("execution_authorized") is not True:
        problems.append("execution_not_authorized")
    if mode == "prepare" and document.get("preparation_authorized") is not True:
        problems.append("preparation_not_authorized")
    if document.get("signed_by") in (None, "", "author", "implementer"):
        problems.append("signer_not_independent")
    if not COMMITMENT_PATH.exists():
        problems.append("commitment_missing")
        return problems
    if document.get("commitment_sha256") != sha(COMMITMENT_PATH):
        problems.append("commitment_sha_mismatch")
    commitment = read_json(COMMITMENT_PATH)
    actual_rows = rows_hash(read_json(ROWS_PATH))
    if document.get("rows_sha256") != actual_rows:
        problems.append("rows_drift")
    if commitment.get("rows_sha256") != actual_rows:
        problems.append("commitment_rows_drift")
    counts = document.get("counts") or {}
    actual_counts = {"suite": len(read_json(ROWS_PATH)["suite_rows"]),
                     "witness": len(read_json(ROWS_PATH)["witness_rows"])}
    if counts.get("suite") != actual_counts["suite"] or \
            counts.get("witness") != actual_counts["witness"]:
        problems.append("counts_mismatch")
    actual_package = common.package_digest(HERE, exclude={COMMITMENT_PATH})
    if commitment.get("package_digest") != actual_package:
        problems.append("package_digest_drift")
    rules = commitment.get("rules") or {}
    for key, name in (("adapter_sha256", "s3_adapter.py"),
                      ("adjudication_sha256", "s3_adjudicate.py"),
                      ("witness_script_sha256", "witness_probes.py")):
        path = HERE / name
        if not path.is_file() or rules.get(key) != sha(path):
            problems.append(f"rule_drift:{name}")
    return problems


# ------------------------------------------------------------------ prepare

def interpreter_record() -> dict:
    path = FROZEN_INTERPRETER
    if not path.is_file():
        raise RuntimeError(f"frozen_interpreter_missing:{path}")
    probe = subprocess.run([str(path), "-c", "import platform;print(platform.python_version())"],
                           capture_output=True, text=True, timeout=60)
    if probe.returncode or probe.stdout.strip() != "3.11.9":
        raise RuntimeError(f"frozen_interpreter_version:{probe.stdout.strip()}")
    return {"path": str(path), "sha256": sha(path), "version": "3.11.9"}


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def wheel_metadata(path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as archive:
        name = next((item for item in archive.namelist()
                     if item.endswith(".dist-info/METADATA")), None)
        if name is None:
            raise RuntimeError(f"wheel_metadata_missing:{path}")
        text = archive.read(name).decode("utf-8", "replace")
    package = version = None
    for line in text.splitlines():
        if line.startswith("Name: "):
            package = line[6:].strip()
        elif line.startswith("Version: "):
            version = line[9:].strip()
    if not package or not version:
        raise RuntimeError(f"wheel_metadata_incomplete:{path}")
    return normalize_name(package), version


def expected_install_set(tool: dict) -> dict:
    expected = {}
    for wheel in tool["wheels"]:
        name, version = wheel_metadata(WS / wheel["path"])
        if name in expected and expected[name] != version:
            raise RuntimeError(f"duplicate_wheel_name:{name}")
        expected[name] = version
    expected["pyhost-observer"] = "0.2.0"
    return dict(sorted(expected.items()))


def installed_set(venv_python) -> dict:
    probe = subprocess.run(
        [str(venv_python), "-c",
         "import json,importlib.metadata as m;"
         "print(json.dumps({d.metadata['Name']:d.version for d in m.distributions()}))"],
        capture_output=True, text=True, timeout=120)
    if probe.returncode:
        raise RuntimeError(f"inventory_failed:{probe.stderr[-400:]}")
    return {normalize_name(name): version for name, version in json.loads(probe.stdout).items()}


def compare_install_sets(expected: dict, observed: dict) -> list[str]:
    problems = []
    missing = sorted(set(expected) - set(observed))
    extra = sorted(set(observed) - set(expected))
    wrong = sorted(name for name in set(expected) & set(observed)
                   if expected[name] != observed[name])
    if missing:
        problems.append(f"missing:{','.join(missing)}")
    if extra:
        problems.append(f"extra:{','.join(extra)}")
    if wrong:
        problems.append("wrong_version:" + ",".join(
            f"{name}={observed[name]}!={expected[name]}" for name in wrong))
    return problems


def observer_state(purelib, tool: dict) -> dict:
    state = {}
    for name, binding in tool["observer"].items():
        path = pathlib.Path(purelib) / name
        state[name] = {"present": path.is_file(),
                       "sha256": sha(path) if path.is_file() else None,
                       "matches_binding": path.is_file() and sha(path) == binding["sha256"]}
    for name, expected in observer_distribution_files(purelib, tool).items():
        path = pathlib.Path(purelib) / "pyhost_observer-0.2.0.dist-info" / name
        state["pyhost_observer-0.2.0.dist-info/" + name] = {
            "present": path.is_file(), "sha256": sha(path) if path.is_file() else None,
            "matches_binding": path.is_file() and path.read_text(encoding="utf-8") == expected}
    return state


def observer_distribution_files(purelib, tool: dict) -> dict:
    """复用冻结 matrix_runner/observer_install.py 的 0.2.0 发行版元数据约定。"""
    dist = "pyhost_observer-0.2.0.dist-info"
    files = {
        "METADATA": "Metadata-Version: 2.1\nName: pyhost-observer\nVersion: 0.2.0\n"
                    "Summary: PyHost-Context 最终矩阵的只读 observer（pytest 插件 + 网络拒绝记录，create_connection 按文档契约）\n",
        "WHEEL": "Wheel-Version: 1.0\nGenerator: pyhost-context-matrix-observer-installer\n"
                 "Root-Is-Purelib: true\nTag: py3-none-any\n",
        "INSTALLER": "pyhost-context-matrix-observer-installer\n",
    }
    lines = []
    for name in sorted(tool["observer"]):
        path = pathlib.Path(purelib) / name
        lines.append(f"{name},,{sha(path) if path.is_file() else 'missing'}")
    for name in ("METADATA", "WHEEL", "INSTALLER"):
        lines.append(f"{dist}/{name},,{common.sha256_bytes(files[name].encode('utf-8'))}")
    files["RECORD"] = "\n".join(lines) + "\n"
    return files


def expected_pytest11(tool: dict) -> list:
    entries = {}
    for wheel in tool["wheels"]:
        with zipfile.ZipFile(WS / wheel["path"]) as archive:
            for name in archive.namelist():
                if not name.endswith(".dist-info/entry_points.txt"):
                    continue
                parser = configparser.ConfigParser(interpolation=None)
                parser.optionxform = str
                parser.read_string(archive.read(name).decode("utf-8"))
                if parser.has_section("pytest11"):
                    for plugin, value in parser.items("pytest11"):
                        if plugin in entries:
                            raise RuntimeError(f"duplicate_pytest11:{plugin}")
                        entries[plugin] = value.strip()
    return sorted([[name, value] for name, value in entries.items()])


def verify_pytest11(venv_python, tool: dict) -> list:
    probe = subprocess.run(
        [str(venv_python), "-c", "import json,importlib.metadata as m;"
         "print(json.dumps(sorted([e.name,e.value] for d in m.distributions() "
         "for e in d.entry_points if e.group=='pytest11')))"],
        capture_output=True, text=True, timeout=120)
    observed = json.loads(probe.stdout) if not probe.returncode else None
    if observed != expected_pytest11(tool):
        raise RuntimeError(f"pytest11_inventory_mismatch:{observed}")
    return observed


def verify_dependencies(venv_python) -> None:
    checked = subprocess.run(["uv", "--offline", "pip", "check", "--python", str(venv_python)],
                             capture_output=True, text=True, timeout=120)
    if checked.returncode:
        raise RuntimeError(f"dependency_check_failed:{checked.stderr[-800:]}")


def prepare(state_root: pathlib.Path) -> dict:
    """离线准备：冻结解释器、逐件校验 wheel/observer、安装并精确对账。"""
    toolchains = read_json(INPUTS / "toolchains.json")["toolchains"]
    interpreter = interpreter_record()
    results = {}
    for project, tool in toolchains.items():
        for wheel in tool["wheels"]:
            path = WS / wheel["path"]
            if not path.is_file() or sha(path) != wheel["sha256"] \
                    or path.stat().st_size != wheel["bytes"]:
                raise RuntimeError(f"wheel_binding_mismatch:{project}:{wheel['filename']}")
        for name, binding in tool["observer"].items():
            path = WS / binding["path"]
            if not path.is_file() or sha(path) != binding["sha256"]:
                raise RuntimeError(f"observer_binding_mismatch:{project}:{name}")
        expected = expected_install_set(tool)
        venv_dir = state_root / "venvs" / project / "venv"
        venv_python = venv_dir / "bin" / "python"
        profile_path = state_root / "venvs" / project / "profile.json"
        if venv_python.exists():
            if not profile_path.exists():
                raise RuntimeError(f"venv_present_without_profile:{project}")
            profile = read_json(profile_path)
            if profile.get("interpreter_sha256") != interpreter["sha256"] \
                    or profile.get("declared") != tool:
                raise RuntimeError(f"venv_profile_mismatch:{project}")
            observed = installed_set(venv_python)
            problems = compare_install_sets(expected, observed)
            if problems:
                raise RuntimeError(f"venv_install_set_mismatch:{project}:{problems}")
            site = subprocess.run(
                [str(venv_python), "-c",
                 "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
                capture_output=True, text=True, timeout=60)
            purelib = pathlib.Path(site.stdout.strip())
            observer = observer_state(purelib, tool)
            if not all(item["matches_binding"] for item in observer.values()):
                raise RuntimeError(f"venv_observer_drift:{project}")
            plugin_names = verify_pytest11(venv_python, tool)
            verify_dependencies(venv_python)
            results[project] = {"venv_python": str(venv_python), "reused": True,
                                "installed": observed, "observer": observer,
                                "pytest11_plugins": plugin_names,
                                "profile_sha256": sha(profile_path)}
            continue
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        created = subprocess.run([str(FROZEN_INTERPRETER), "-m", "venv", "--without-pip",
                                  str(venv_dir)], capture_output=True, text=True, timeout=300)
        if created.returncode:
            raise RuntimeError(f"venv_create_failed:{project}:{created.stderr[-400:]}")
        wheel_paths = [str(WS / wheel["path"]) for wheel in tool["wheels"]]
        installed = subprocess.run(["uv", "pip", "install", "--python", str(venv_python),
                                    "--offline", "--no-index", "--no-deps", *wheel_paths],
                                   capture_output=True, text=True, timeout=1800)
        if installed.returncode:
            raise RuntimeError(f"wheel_install_failed:{project}:{installed.stderr[-800:]}")
        site = subprocess.run([str(venv_python), "-c",
                               "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
                              capture_output=True, text=True, timeout=60)
        purelib = pathlib.Path(site.stdout.strip())
        for name, binding in tool["observer"].items():
            shutil.copyfile(WS / binding["path"], purelib / name)
        dist = purelib / "pyhost_observer-0.2.0.dist-info"
        dist.mkdir(parents=True, exist_ok=True)
        for name, content in observer_distribution_files(purelib, tool).items():
            (dist / name).write_text(content, encoding="utf-8")
        observer = observer_state(purelib, tool)
        if not all(item["matches_binding"] for item in observer.values()):
            raise RuntimeError(f"observer_install_mismatch:{project}")
        observed = installed_set(venv_python)
        problems = compare_install_sets(expected, observed)
        if problems:
            raise RuntimeError(f"install_set_mismatch:{project}:{problems}")
        if observed.get("pytest") != tool["pytest"]:
            raise RuntimeError(f"pytest_version_mismatch:{project}:{observed.get('pytest')}")
        plugin_names = verify_pytest11(venv_python, tool)
        verify_dependencies(venv_python)
        profile = {"project": project, "interpreter": interpreter,
                   "interpreter_sha256": interpreter["sha256"],
                   "declared": tool, "expected_install_set": expected,
                   "installed": observed, "observer": observer,
                   "pytest11_plugins": plugin_names,
                   "candidate_installed": False}
        write_json(profile_path, profile)
        results[project] = {"venv_python": str(venv_python), "reused": False,
                            "installed": observed, "observer": observer,
                            "pytest11_plugins": plugin_names,
                            "profile_sha256": sha(profile_path)}
    return {"interpreter": interpreter, "projects": results}


# ------------------------------------------------------------------ main

def status(rows_doc) -> dict:
    return {
        "batch_id": rows_doc["batch_id"],
        "execution_authorized": rows_doc["execution_authorized"],
        "counts": rows_doc["counts"],
        "rows_sha256": rows_hash(rows_doc),
        "commitment_sha256": sha(COMMITMENT_PATH) if COMMITMENT_PATH.exists() else None,
        "cleared": False,
        "note": "S3 执行未授权；本命令不创建运行目录。",
    }


def plan_of(rows) -> list[dict]:
    return [{"order": row["order"], "run_id": run_id_of(row), "row_key": row["row_key"],
             "call_kind": row["call_kind"]} for row in rows]


def ledger_identity_problems(ledger, document, rows) -> list[str]:
    problems = []
    if ledger.get("batch_id") != document["batch_id"]:
        problems.append("ledger_batch_mismatch")
    if ledger.get("commitment_sha256") != (sha(COMMITMENT_PATH) if COMMITMENT_PATH.exists() else None):
        problems.append("ledger_commitment_mismatch")
    if ledger.get("rows_sha256") != rows_hash(document):
        problems.append("ledger_rows_mismatch")
    plan = plan_of(rows)
    if ledger.get("plan_sha256") != canonical_sha(plan):
        problems.append("ledger_plan_mismatch")
    for run_id, record in (ledger.get("records") or {}).items():
        expected = {item["run_id"]: item for item in plan}
        if run_id not in expected:
            problems.append(f"ledger_foreign_record:{run_id}")
        elif record.get("row_key") != expected[run_id]["row_key"]:
            problems.append(f"ledger_row_identity:{run_id}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--state-root", type=pathlib.Path)
    parser.add_argument("--clearance", type=pathlib.Path)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    rows_doc, rows = load_rows()
    if args.status:
        print(json.dumps(status(rows_doc), ensure_ascii=False, indent=2))
        return 0
    if args.prepare:
        if not args.state_root:
            parser.error("--prepare requires --state-root")
        problems = clearance_problems(args.clearance, mode="prepare")
        if problems:
            print(json.dumps({"refused": True, "mode": "prepare", "problems": problems,
                              "preparation_authorized": False}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(prepare(args.state_root), ensure_ascii=False, indent=2))
        return 0
    if not args.run:
        parser.print_help()
        return 0
    if not args.state_root:
        parser.error("--run requires --state-root")
    problems = clearance_problems(args.clearance, mode="run")
    if problems:
        print(json.dumps({"refused": True, "problems": problems,
                          "execution_authorized": False}, ensure_ascii=False, indent=2))
        return 2
    toolchains = read_json(INPUTS / "toolchains.json")["toolchains"]
    interpreters = {project: pathlib.Path(args.state_root) / "venvs" / project / "venv" / "bin" / "python"
                    for project in toolchains}
    for project, path in interpreters.items():
        if not path.exists():
            print(json.dumps({"refused": True, "problems": [f"venv_missing:{project}"]},
                             ensure_ascii=False, indent=2))
            return 2
    ledger_path = args.state_root / "ledger.json"
    if ledger_path.exists():
        ledger = read_json(ledger_path)
        identity_problems = ledger_identity_problems(ledger, rows_doc, rows)
        if identity_problems:
            print(json.dumps({"refused": True, "problems": identity_problems},
                             ensure_ascii=False, indent=2))
            return 2
    else:
        ledger = {"batch_id": rows_doc["batch_id"],
                  "commitment_sha256": sha(COMMITMENT_PATH),
                  "rows_sha256": rows_hash(rows_doc),
                  "plan_sha256": canonical_sha(plan_of(rows)),
                  "plan": plan_of(rows), "records": {}}
        write_json(ledger_path, ledger)
    attempts = 0
    failures = 0
    for row in rows:
        run_id = run_id_of(row)
        if run_id in ledger["records"]:
            continue
        if args.limit is not None and attempts >= args.limit:
            break
        try:
            record = dispatch(row, args.state_root, interpreters)
            if record.get("status") != "completed":
                failures += 1
        except Exception as exc:  # noqa: BLE001 - 记账后终止本轮
            record = {"row_key": row["row_key"], "call_kind": row["call_kind"],
                      "status": "technical_failure",
                      "error": f"{type(exc).__name__}: {exc}"}
            failures += 1
        record["run_id"] = run_id
        record["finished_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        ledger["records"][run_id] = record
        write_json(ledger_path, ledger)
        attempts += 1
    completed = sum(1 for record in ledger["records"].values()
                    if record.get("status") == "completed")
    not_executed = [item["run_id"] for item in ledger["plan"]
                    if item["run_id"] not in ledger["records"]]
    summary = {"executed_this_call": attempts,
               "attempts_recorded": len(ledger["records"]),
               "completed": completed,
               "not_executed": len(not_executed),
               "complete": completed == len(ledger["plan"]),
               "failed_this_call": failures,
               "not_executed_run_ids": not_executed[:20],
               "ledger": str(ledger_path)}
    write_json(args.state_root / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
