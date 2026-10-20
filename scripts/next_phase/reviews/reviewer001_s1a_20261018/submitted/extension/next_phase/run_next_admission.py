"""ClawsGO Science Agent：新阶段 S1a 准入的实际执行入口。

本模块是**薄包装**：A5—A8 全部委托已审仪器
``extension/tooling/run_extension.py::admit_one``（A5 环境、至多 2 次 A6 收集、1 次 A7
基线、2 次 A8 恢复），矩阵单轮仍走 ``matrix/tooling/run_matrix.py::run_one``。本模块只补
新阶段特有的事：

1. 消费冻结的 29 个位置与 8/4 配额（``admission/scheduler.py``）；
2. 把新候选的工件与依赖闭包放到**隔离路径**，并在 A6/A7 之前逐件绑定；
3. 新档案 ``next-admission/2026-10-18-s1a``、新账本与续跑校验；
4. 只接受 Reviewer 001 签署的 S1a 放行件；本文件不生成、不代签放行件。

Reviewer 001 S1 审核后的修正（R1—R3）：

* 稳定导入路径，不依赖宿主 PYTHONPATH；
* A7 元数据改用本阶段治理件与 S1a 放行件，v1.1 仅登记为仪器协议；
* A6 收集与 A7 基线统一受 240 秒上限约束（适配器只改超时，不改仪器函数体）；
* 候选级承诺落盘并传给仪器；M1 补装前前瞻修订承诺；
* 授权门要求完整绑定集合与静态承诺相符，拒绝空绑定；
* 工件身份按 Name/Version、字节数、唯一顶层 PKG-INFO 与成员路径重新核验；
* 预期技术失败记成一次有证据的准入尝试并继续；仪器异常单独记录后中止；
* 每次尝试独立目录，绝不覆盖旧产物；
* 续跑严格绑定批次、承诺、位置、项目、版本、组、池槽与规范家族。
"""
import argparse
import hashlib
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE
EXT = NEXT.parent
ROOT = EXT.parent

# R1：稳定导入路径；不依赖实施机未记录的 PYTHONPATH。
for _path in (EXT / "tooling", ROOT / "tooling", ROOT / "tooling" / "admission_runner",
              ROOT / "matrix" / "tooling"):
    if _path.is_dir() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

sys.path.insert(0, str(NEXT))
sys.path.insert(0, str(NEXT / "admission"))
from admission import scheduler  # noqa: E402

BATCH_ID = "next-admission-20261018"
RUNNER_PROFILE = "next-admission/2026-10-18-s1a"
A6_TIMEOUT_SECONDS = 240
A7_TIMEOUT_SECONDS = 240
POOLS = NEXT / "POOLS_20261018.json"
PREREG = NEXT / "PRE_REGISTRATION_20261018.json"
WITNESSES = NEXT / "STATIC_WITNESSES_20261018.json"
STATE = NEXT / "state"
RUNS = STATE / "runs" / BATCH_ID
LEDGER = RUNS / "ledger.json"
PLAN_DRAFT = STATE / "plans" / "next_admission_plan_draft.json"
CANDIDATES_DOC = STATE / "candidates_next.json"
ARTIFACTS = STATE / "artifacts"
WHEELHOUSE = STATE / "wheelhouse"
VENVS = STATE / "venvs"
BATCHES = STATE / "batches"
CLEARANCE = NEXT / "effective" / "REVIEWER001_NEXT_ADMISSION_CLEARANCE.json"
CLEARANCE_TEMPLATE = STATE / "plans" / "next_admission_clearance_request.json"

FRAME = EXT / "frame" / "frame_20261015.json"
FINAL_SAMPLE = EXT / "freeze" / "final_sample_20261015.json"
CONDITION_PROTOCOL = ROOT / "matrix" / "protocol" / "matrix_protocol_v0.4_reviewed.json"
B0_DOCUMENT = ROOT / "admission_freeze" / "b0_environment.json"
EFFECTIVE = EXT / "effective" / "EFFECTIVE_PROTOCOL_v1.2_Reviewer001.json"
INSTRUMENT_PROTOCOL = EXT / "effective" / "EFFECTIVE_PROTOCOL_v1.1_Reviewer001.json"
OBSERVER_RESOURCES = (ROOT / "tooling" / "admission_runner" / "integration" / "jsonl_producer.py",
                      ROOT / "matrix" / "tooling" / "matrix_runner" / "netwatch_sitecustomize.py")

REQUIRED_DECISION = "APPROVED_FOR_NEXT_ADMISSION"
AUTHOR_IDENTITY = "ClawsGO Science Agent"
ALLOWED_SKIP_REASONS = {"group_full", "family_cap", "static_ineligible"}


class PreparationFailure(RuntimeError):
    """一次预期内的技术失败；由主循环记成准入尝试，不终止批次。"""

    def __init__(self, code, detail, attempt_dir=None, calls=0):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.attempt_dir = attempt_dir
        self.calls = calls


def now_utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_of(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def load_json(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def write_json(path, document):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def write_new(path, document):
    """只新建；同名产物已存在即拒绝，避免静默覆盖旧尝试。"""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def commitment_of(files):
    """与既有批次同一算法：按路径排序的 `path:sha256\\n` 拼接后一次 SHA-256。"""
    payload = "".join(f"{path}:{digest}\n" for path, digest in sorted(files.items()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def static_inputs():
    """本入口实际读取/导入的规范文件、观测器资源与仪器协议。"""
    import closure
    files = closure.local_import_closure(pathlib.Path(__file__).resolve())
    files.update({
        str(POOLS.relative_to(ROOT)): sha256_of(POOLS),
        str(PREREG.relative_to(ROOT)): sha256_of(PREREG),
        str(FRAME.relative_to(ROOT)): sha256_of(FRAME),
        str(FINAL_SAMPLE.relative_to(ROOT)): sha256_of(FINAL_SAMPLE),
        str(CONDITION_PROTOCOL.relative_to(ROOT)): sha256_of(CONDITION_PROTOCOL),
        str(B0_DOCUMENT.relative_to(ROOT)): sha256_of(B0_DOCUMENT),
        str(EFFECTIVE.relative_to(ROOT)): sha256_of(EFFECTIVE),
        str(INSTRUMENT_PROTOCOL.relative_to(ROOT)): sha256_of(INSTRUMENT_PROTOCOL),
    })
    if WITNESSES.is_file():
        files[str(WITNESSES.relative_to(ROOT))] = sha256_of(WITNESSES)
    for resource in OBSERVER_RESOURCES:
        files[str(resource.relative_to(ROOT))] = sha256_of(resource)
    return dict(sorted(files.items()))


def input_roles():
    roles = {path: "implementation_closure" for path in static_inputs()}
    roles[str(EFFECTIVE.relative_to(ROOT))] = "phase_governance"
    roles[str(INSTRUMENT_PROTOCOL.relative_to(ROOT))] = "instrument_protocol"
    roles[str(POOLS.relative_to(ROOT))] = "frozen_candidate_pools"
    roles[str(PREREG.relative_to(ROOT))] = "preregistration"
    roles[str(FRAME.relative_to(ROOT))] = "frame"
    roles[str(WITNESSES.relative_to(ROOT))] = "static_witnesses"
    for resource in OBSERVER_RESOURCES:
        roles[str(resource.relative_to(ROOT))] = "observer_resource"
    return roles


# ------------------------------------------------------------------ 授权

def clearance_gate():
    problems = []
    if not CLEARANCE.is_file():
        return {"ok": False, "problems": ["S1a_clearance_missing"], "reviewer_signed": False}
    doc = load_json(CLEARANCE)
    if doc.get("signed_by") != "Reviewer 001":
        problems.append("clearance_not_signed_by_reviewer001")
    if doc.get("decision") != REQUIRED_DECISION:
        problems.append(f"clearance_decision:{doc.get('decision')}")
    if doc.get("batch_id") != BATCH_ID:
        problems.append(f"clearance_batch:{doc.get('batch_id')}")
    scope = doc.get("scope") or {}
    if scope.get("S1a_admission_candidate_execution") is not True:
        problems.append("S1a_not_authorized")
    for flag in ("S1b_matrix_and_independent_b0", "S2_recovery", "S3_repair_and_witness",
                 "tool_repro"):
        if scope.get(flag) is not False:
            problems.append(f"out_of_scope_authorized:{flag}")
    bound = doc.get("bound_files")
    if not isinstance(bound, dict) or not bound:
        problems.append("clearance_bound_files_empty")
        bound = {}
    current = static_inputs()
    missing = sorted(set(current) - set(bound))
    if missing:
        problems.append("clearance_bound_files_incomplete:" + ",".join(missing[:5]))
    for path, digest in sorted(bound.items()):
        target = ROOT / path
        if not target.is_file() or sha256_of(target) != digest:
            problems.append(f"bound_file_drift:{path}")
    if doc.get("static_inputs_commitment") != commitment_of(current):
        problems.append("clearance_static_commitment_mismatch")
    return {"ok": not problems, "problems": problems, "reviewer_signed": True,
            "clearance_sha256": sha256_of(CLEARANCE),
            "authorization_is_not_execution_result": True}


def require_clearance():
    gate = clearance_gate()
    if not gate["ok"]:
        raise SystemExit(json.dumps({"status": "refused", **gate}, ensure_ascii=False, indent=2))
    return gate


# ------------------------------------------------------------------ 计划

def load_plan():
    pools = load_json(POOLS)
    plan = scheduler.build_plan(pools)
    if WITNESSES.is_file():
        scheduler.apply_eligibility(plan, load_json(WITNESSES))
    return plan, pools


def plan_document(plan):
    files = static_inputs()
    return {
        "schema_version": "pyhost-context/next-admission-plan/1.1",
        "batch_id": BATCH_ID,
        "runner_profile": RUNNER_PROFILE,
        "written_at_utc": now_utc(),
        "authorized": False,
        "authorization_note": "本计划只是执行输入；真实准入需 Reviewer 001 签署的 S1a 放行件",
        "targets": scheduler.TARGET_PASSES,
        "family_cap": scheduler.FAMILY_CAP,
        "calls_per_attempt": scheduler.CALLS_PER_ATTEMPT,
        "call_ceiling_named_candidates": len(plan) * scheduler.CALLS_PER_ATTEMPT,
        "a6_timeout_seconds": A6_TIMEOUT_SECONDS,
        "a7_timeout_seconds": A7_TIMEOUT_SECONDS,
        "rows": plan,
        "static_inputs": files,
        "static_input_roles": input_roles(),
        "static_inputs_commitment": commitment_of(files),
        "ledger": str(LEDGER.relative_to(ROOT)),
        "state_root": str(STATE.relative_to(ROOT)),
        "candidate_tests_or_installs": 0,
    }


def write_plan_draft():
    plan, _ = load_plan()
    document = plan_document(plan)
    write_json(PLAN_DRAFT, document)
    print(json.dumps({key: document[key] for key in (
        "batch_id", "targets", "family_cap", "call_ceiling_named_candidates",
        "a6_timeout_seconds", "static_inputs_commitment")}, ensure_ascii=False, indent=2))
    return 0


def clearance_request():
    plan, _ = load_plan()
    document = {
        "schema_version": "pyhost-context/next-admission-clearance-request/1.1",
        "requested_by": AUTHOR_IDENTITY,
        "requested_at_utc": now_utc(),
        "batch_id": BATCH_ID,
        "decision_requested": REQUIRED_DECISION,
        "scope_requested": {
            "S1a_admission_candidate_execution": True,
            "S1b_matrix_and_independent_b0": False,
            "S2_recovery": False,
            "S3_repair_and_witness": False,
            "tool_repro": False,
        },
        "plan": str(PLAN_DRAFT.relative_to(ROOT)),
        "bound_files": plan_document(plan)["static_inputs"],
        "static_inputs_commitment": plan_document(plan)["static_inputs_commitment"],
        "note": "实施方不代签；本模板只列请求范围与需绑定的字节。",
    }
    write_json(CLEARANCE_TEMPLATE, document)
    print(json.dumps({"template": str(CLEARANCE_TEMPLATE.relative_to(ROOT)),
                      "bound_files": len(document["bound_files"])}, ensure_ascii=False))
    return 0


# ------------------------------------------------------------------ 账本

def empty_state():
    state = scheduler.empty_state()
    state["abort_history"] = []
    return state


def load_state(commitment):
    if not LEDGER.is_file():
        return empty_state()
    doc = load_json(LEDGER)
    if doc.get("batch_id") != BATCH_ID:
        raise SystemExit("账本批次不符，拒绝续跑")
    if doc.get("runner_profile") != RUNNER_PROFILE:
        raise SystemExit("账本执行档案不符，拒绝续跑")
    if commitment and doc.get("commitment") != commitment:
        raise SystemExit("账本承诺与当前输入不符，拒绝续跑")
    state = {"records": list(doc.get("records") or []),
             "skips": list(doc.get("skips") or []),
             "abort_history": list(doc.get("abort_history") or [])}
    check = scheduler.validate_resume(load_plan()[0], state, BATCH_ID, commitment)
    if not check["ok"]:
        raise SystemExit(json.dumps({"status": "ledger_inconsistent", **check},
                                    ensure_ascii=False, indent=2))
    replay = scheduler.summary(load_plan()[0], state)
    recorded = doc.get("summary") or {}
    for key in ("attempted", "skipped", "mechanism_passes", "reference_passes", "calls_used"):
        if recorded.get(key) != replay.get(key):
            raise SystemExit(f"账本摘要无法按记录重放：{key}")
    return state


def write_ledger(plan, state, commitment, status, abort=None):
    doc = {
        "schema_version": "pyhost-context/next-admission-ledger/1.1",
        "batch_id": BATCH_ID,
        "runner_profile": RUNNER_PROFILE,
        "commitment": commitment,
        "written_at_utc": now_utc(),
        "status": status,
        "plan_positions": [row["position"] for row in plan],
        "records": state["records"],
        "skips": state["skips"],
        "abort_history": state.get("abort_history", []),
        "summary": scheduler.summary(plan, state),
        "signature": {"records": len(state["records"]), "skips": len(state["skips"])},
        "candidate_tests_or_installs_recorded_by_ledger": len(state["records"]),
    }
    if abort:
        doc["abort"] = abort
    write_json(LEDGER, doc)
    return doc


def freeze_batch():
    gate = require_clearance()
    plan, _ = load_plan()
    files = static_inputs()
    document = {
        "schema_version": "pyhost-context/next-admission-batch-commitment/1.1",
        "batch_id": BATCH_ID,
        "runner_profile": RUNNER_PROFILE,
        "created_at_utc": now_utc(),
        "pre_run_commitment_hash": commitment_of(files),
        "algorithm": "sorted('{path}:{sha256}\\n') joined, then one SHA-256",
        "files": files,
        "input_roles": input_roles(),
        "clearance": str(CLEARANCE.relative_to(ROOT)),
        "clearance_sha256": gate["clearance_sha256"],
        "plan_positions": len(plan),
    }
    write_json(BATCHES / f"{BATCH_ID}.json", document)
    print(json.dumps({"commitment": document["pre_run_commitment_hash"],
                      "files": len(files)}, ensure_ascii=False))
    return 0


# ------------------------------------------------------------------ 工件与身份

def _artifact_source(row):
    cache = NEXT / "cache" / "sdists" / row["sdist"]["filename"]
    if cache.is_file() and sha256_of(cache) == row["sdist"]["sha256"]:
        return cache, "next_phase_sdist_cache"
    return None, None


def acquire_sdist(row):
    target_dir = ARTIFACTS / row["project"]
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / row["sdist"]["filename"]
    if target.is_file() and sha256_of(target) == row["sdist"]["sha256"] \
            and target.stat().st_size == row["sdist"]["bytes"]:
        return {"path": target, "host": "already_acquired"}
    source, host = _artifact_source(row)
    if source is None:
        import run_extension as rx
        raw, host = rx.fetch_artifact(row["sdist"]["url"], row["sdist"]["filename"],
                                      row["sdist"]["sha256"])
        if raw is None:
            raise PreparationFailure("sdist_acquisition_failed",
                                     f"{row['project']}:{row['sdist']['filename']}")
        target.write_bytes(raw)
    else:
        import shutil
        shutil.copyfile(source, target)
    if sha256_of(target) != row["sdist"]["sha256"] \
            or target.stat().st_size != row["sdist"]["bytes"]:
        raise PreparationFailure("sdist_identity_mismatch", row["project"])
    return {"path": target, "host": host}


def sdist_source_dir(path):
    import tarfile
    with tarfile.open(path) as tar:
        for member in tar.getmembers():
            parts = pathlib.PurePosixPath(member.name).parts
            if parts:
                return parts[0]
    raise PreparationFailure("empty_sdist", str(path))


def verify_artifact_identity(path, project, version):
    """复刻已审身份谓词：Name/Version、字节数、唯一顶层 PKG-INFO、成员路径。"""
    import tarfile
    from email.parser import BytesParser
    path = pathlib.Path(path)
    if not path.is_file():
        raise PreparationFailure("artifact_missing", str(path))
    raw_size = path.stat().st_size
    with tarfile.open(path) as tar:
        members = tar.getmembers()
        names = [m.name for m in members]
        bad = [name for name in names
               if pathlib.PurePosixPath(name).is_absolute()
               or ".." in pathlib.PurePosixPath(name).parts]
        if bad:
            raise PreparationFailure("artifact_member_path_unsafe", bad[0])
        links = [m.name for m in members if m.issym() or m.islnk()]
        metadata = [m for m in members if m.isfile()
                    and len(pathlib.PurePosixPath(m.name).parts) == 2
                    and pathlib.PurePosixPath(m.name).name == "PKG-INFO"]
        if len(metadata) != 1:
            raise PreparationFailure("pkg_info_not_unique", path.name)
        info = BytesParser().parsebytes(tar.extractfile(metadata[0]).read())
        regular = [m for m in members if m.isfile()]
    if len(info.get_all("Name", [])) != 1 or len(info.get_all("Version", [])) != 1:
        raise PreparationFailure("pkg_info_identity_not_unique", path.name)
    def canonical(value):
        import re
        return re.sub(r"[-_.]+", "-", value).lower()

    if canonical(info["Name"]) != canonical(project):
        raise PreparationFailure("pkg_info_project_mismatch",
                                 f"{info['Name']} != {project}")
    if info["Version"].strip() != str(version):
        raise PreparationFailure("pkg_info_version_mismatch",
                                 f"{info['Version']} != {version}")
    return {"project": project, "version": version, "bytes": raw_size,
            "metadata_member": metadata[0].name, "metadata_name": info["Name"],
            "metadata_version": info["Version"], "regular_files": len(regular),
            "links": links, "identity_verified": True}


def prepare_candidate(row, attempt_dir=None):
    """取回并校验 sdist，解析依赖闭包，写候选记录；不执行候选代码。"""
    import run_extension as rx
    try:
        acquired = acquire_sdist(row)
        source_dir = sdist_source_dir(acquired["path"])
        verify_artifact_identity(acquired["path"], row["project"], row["version"])
        record = {"project": row["project"], "version": row["version"],
                  "source_dir": source_dir, "sdist": row["sdist"], "resolve_ok": True}
        saved = (rx.ARTIFACTS, rx.WHEELHOUSE, rx.CANDIDATES)
        try:
            rx.ARTIFACTS, rx.WHEELHOUSE = ARTIFACTS, WHEELHOUSE
            resolved = rx.resolve_candidate(row, record)
        finally:
            rx.ARTIFACTS, rx.WHEELHOUSE, rx.CANDIDATES = saved
        if not resolved.get("ok"):
            raise PreparationFailure("dependency_resolution_failed",
                                     f"{row['project']}:{resolved.get('reason')}")
    except PreparationFailure as exc:
        exc.attempt_dir = attempt_dir
        raise
    except Exception as exc:  # 预期内的取回/解析失败
        raise PreparationFailure("candidate_preparation_failed",
                                 f"{type(exc).__name__}: {exc}", attempt_dir) from exc
    candidates = load_json(CANDIDATES_DOC) if CANDIDATES_DOC.is_file() else {"candidates": {}}
    candidates.setdefault("candidates", {})[row["project"]] = record
    candidates["batch_id"] = BATCH_ID
    write_json(CANDIDATES_DOC, candidates)
    lock = {
        "schema_version": "pyhost-context/next-candidate-environment-lock/1.1",
        "project": row["project"], "version": row["version"],
        "source_dir": source_dir,
        "sdist": {"filename": row["sdist"]["filename"],
                  "sha256": row["sdist"]["sha256"], "bytes": row["sdist"]["bytes"]},
        "sdist_host": acquired["host"],
        "resolved_distributions": record["dependencies"]["resolved_distributions"],
        "wheels": record["dependencies"]["wheels"],
        "extras_installed": record["dependencies"]["extras_installed"],
        "resolver_python_version": record["dependencies"]["resolver_python_version"],
        "runner_profile": RUNNER_PROFILE,
        "locked_before": ["A6_collection", "A7_baseline"],
        "frozen_stack": list(rx.FROZEN_STACK),
    }
    write_json(attempt_dir / "environment_lock.json", lock)
    return record, lock, acquired


# ------------------------------------------------------------------ 仪器适配

def _patch_instrument():
    """把已审仪器重定向到新阶段隔离路径；函数体逐字节未改。"""
    import contextlib
    import run_extension as rx
    import run_matrix as rm
    from matrix_runner import source_tree as st

    saved = {
        "rx": {name: getattr(rx, name) for name in
               ("ARTIFACTS", "WHEELHOUSE", "VENVS", "CANDIDATES", "RUNNER_PROFILE",
                "EFFECTIVE", "CLEARANCE")},
        "rm": {"VENVS": rm.VENVS},
        "st": {"sdist_bytes": st.sdist_bytes, "artifact_identity": st.artifact_identity},
        "subprocess_run": rx.subprocess.run,
    }

    def capped_run(argv, *args, **kwargs):
        if kwargs.get("timeout") == 600 and any("--collect-only" in str(item) for item in argv):
            kwargs["timeout"] = A6_TIMEOUT_SECONDS
        return saved["subprocess_run"](argv, *args, **kwargs)

    @contextlib.contextmanager
    def scope():
        rx.ARTIFACTS, rx.WHEELHOUSE = ARTIFACTS, WHEELHOUSE
        rx.VENVS, rx.CANDIDATES = VENVS, CANDIDATES_DOC
        rx.RUNNER_PROFILE = RUNNER_PROFILE
        rx.EFFECTIVE = EFFECTIVE
        rx.CLEARANCE = CLEARANCE
        rx.subprocess.run = capped_run
        rm.VENVS = VENVS

        def resolve(name):
            doc = load_json(CANDIDATES_DOC)["candidates"]
            for project, record in doc.items():
                if record["source_dir"] == name:
                    return project, record
            raise KeyError(f"source_dir_not_registered:{name}")

        def sdist_bytes(name):
            import tarfile
            project, record = resolve(name)
            path = ARTIFACTS / project / record["sdist"]["filename"]
            receipt = verify_artifact_identity(path, project, record["version"])
            files = {}
            with tarfile.open(path) as tar:
                for member in tar.getmembers():
                    if not member.isfile():
                        continue
                    parts = pathlib.PurePosixPath(member.name).parts
                    rel = "/".join(parts[1:]) if len(parts) > 1 else member.name
                    files[rel] = tar.extractfile(member).read()
            return path, files

        def identity(name):
            project, record = resolve(name)
            path = ARTIFACTS / project / record["sdist"]["filename"]
            try:
                receipt = verify_artifact_identity(path, project, record["version"])
            except PreparationFailure as exc:
                return {"request": name, "project": project,
                        "version": record["version"], "source_dir": name,
                        "artifact_path": str(path.relative_to(ROOT)),
                        "expected_sha256": record["sdist"]["sha256"],
                        "observed_sha256": sha256_of(path) if path.is_file() else None,
                        "bytes": path.stat().st_size if path.is_file() else None,
                        "identity_verified": False,
                        "identity_problems": [exc.code],
                        "registry": "next_phase/candidates_next.json"}
            return {"request": name, "project": project,
                    "version": record["version"], "source_dir": name,
                    "artifact_path": str(path.relative_to(ROOT)),
                    "expected_sha256": record["sdist"]["sha256"],
                    "observed_sha256": sha256_of(path),
                    "bytes": receipt["bytes"],
                    "metadata_name": receipt["metadata_name"],
                    "metadata_version": receipt["metadata_version"],
                    "links": receipt["links"],
                    "identity_verified": True,
                    "registry": "next_phase/candidates_next.json"}

        st.sdist_bytes = sdist_bytes
        st.artifact_identity = identity
        try:
            yield
        finally:
            for name, value in saved["rx"].items():
                setattr(rx, name, value)
            for name, value in saved["rm"].items():
                setattr(rm, name, value)
            for name, value in saved["st"].items():
                setattr(st, name, value)
            rx.subprocess.run = saved["subprocess_run"]

    return scope()


# ------------------------------------------------------------------ 尝试

def next_attempt_index(project):
    root = RUNS / project
    if not root.is_dir():
        return 1
    indexes = [int(path.parent.name.split("-")[-1]) for path in root.glob("attempt-*/started.json")]
    return max(indexes, default=0) + 1


def attempt_paths(project, index):
    attempt_dir = RUNS / project / f"attempt-{index:03d}"
    return {"dir": attempt_dir, "started": attempt_dir / "started.json",
            "commitment": attempt_dir / "commitment.json",
            "result": attempt_dir / "result.json",
            "runtime": attempt_dir / "runtime"}


def candidate_commitment(row, record, lock, batch_commitment):
    files = static_inputs()
    files[str((ARTIFACTS / row["project"] / row["sdist"]["filename"]).relative_to(ROOT))] = \
        row["sdist"]["sha256"]
    for name, digest in lock["wheels"].items():
        files[str((WHEELHOUSE / row["project"] / name).relative_to(ROOT))] = digest
    return {"schema_version": "pyhost-context/next-candidate-commitment/1.1",
            "batch_id": BATCH_ID, "position": row["position"],
            "project": row["project"], "version": row["version"],
            "batch_commitment": batch_commitment,
            "pre_run_commitment_hash": commitment_of(files), "files": files,
            "source_dir": record["source_dir"],
            "runner_profile": RUNNER_PROFILE}


def static_dependency_expectation(row):
    if not WITNESSES.is_file():
        return {"available": False}
    for item in load_json(WITNESSES).get("items", []):
        if item.get("project") == row["project"]:
            return {"available": True, "family": item.get("family"),
                    "dependency": item.get("dependency"),
                    "reads": item.get("reads", [])[:3]}
    return {"available": True, "family": row.get("family"), "not_found": True}


def dependency_agreement(row, lock):
    """静态证据的依赖版本与准入实际解析版本是否一致；不一致只登记，不改规则。"""
    expectation = static_dependency_expectation(row)
    dep = expectation.get("dependency") or {}
    name = (dep.get("name") or "").lower().replace("_", "-")
    if not name:
        return {"applicable": False, "reason": "no_dependency_channel"}
    resolved = {key.lower().replace("_", "-"): value
                for key, value in (lock.get("resolved_distributions") or {}).items()}
    actual = resolved.get(name)
    expected = dep.get("resolved_version") or dep.get("version")
    return {"applicable": True, "dependency": name, "static_version": expected,
            "resolved_version": actual,
            "match": bool(actual) and str(actual) == str(expected),
            "action_if_mismatch": "必须在扰动前按同一静态规则重新核对读取链，不得继续引用旧版本证据"}


def execute_attempt(row, batch_commitment, adapter=None):
    """执行一次准入尝试；`adapter` 仅用于合成测试注入假执行器。"""
    if adapter is not None:
        return adapter(row)
    gate = clearance_gate()
    if not gate["ok"]:
        return {"admitted": False, "status": "refused_not_authorized",
                "refusals": list(gate["problems"]), "calls": 0,
                "run_dir": None, "batch_id": BATCH_ID,
                "commitment": batch_commitment}
    import run_extension as rx
    index = next_attempt_index(row["project"])
    paths = attempt_paths(row["project"], index)
    write_new(paths["started"], {
        "schema_version": "pyhost-context/next-admission-attempt-start/1.1",
        "batch_id": BATCH_ID, "position": row["position"],
        "project": row["project"], "attempt_index": index,
        "batch_commitment": batch_commitment, "started_at_utc": now_utc(),
        "runner_profile": RUNNER_PROFILE,
    })
    try:
        record, lock, _acquired = prepare_candidate(row, paths["dir"])
    except PreparationFailure as exc:
        exc.attempt_dir = paths["dir"]
        raise
    commitment = candidate_commitment(row, record, lock, batch_commitment)
    commitment["environment_lock"] = str((paths["dir"] / "environment_lock.json").relative_to(ROOT))
    write_new(paths["commitment"], commitment)

    attempt_id = f"next-{row['project']}-adm-{index}"
    detail = {"project": row["project"], "admission_id": attempt_id,
              "attempt_index": index,
              "static_chain_dependency": static_dependency_expectation(row),
              "batch_commitment": batch_commitment,
              "candidate_commitment": commitment["pre_run_commitment_hash"],
              "a6_timeout_seconds": A6_TIMEOUT_SECONDS,
              "a7_timeout_seconds": A7_TIMEOUT_SECONDS}
    original_supplement = rx.supplemental_install

    def bound_supplement(*args, **kwargs):
        outcome = original_supplement(*args, **kwargs)
        supplied = outcome[1].get("wheels") or {}
        if supplied:
            amended = dict(commitment)
            amended["files"] = dict(commitment["files"])
            amended["files"].update({
                str((WHEELHOUSE / row["project"] / "m1" / name).relative_to(ROOT)): digest
                for name, digest in supplied.items()})
            amended["pre_run_commitment_hash"] = commitment_of(amended["files"])
            amended["supplement"] = {"wheels": supplied,
                                     "supersedes": commitment["pre_run_commitment_hash"]}
            write_new(paths["dir"] / "commitment-M1.json", amended)
            commitment.clear()
            commitment.update(amended)
        return outcome

    with _patch_instrument():
        protocol, b0 = rx.source_protocol([f"{row['project']}-{row['version']}"])
        rx.supplemental_install = bound_supplement
        try:
            observation = rx.admit_one(row["project"], protocol, b0, commitment,
                                       paths["runtime"], detail, admission_id=attempt_id)
        finally:
            rx.supplemental_install = original_supplement
    admitted, refusals = rx.admission_decision(detail)
    calls = len(detail.get("A6_collect_attempts") or []) + 1
    result = {
        "schema_version": "pyhost-context/next-admission-attempt/1.1",
        "batch_id": BATCH_ID, "position": row["position"], "project": row["project"],
        "version": row["version"], "attempt_index": index,
        "admitted": admitted, "refusals": refusals, "calls": calls,
        "status": "admission_evaluated",
        "detail": detail, "observation_run_id": observation.get("run_id"),
        "environment_lock": lock, "dependency_agreement": dependency_agreement(row, lock),
        "batch_commitment": batch_commitment,
        "candidate_commitment": commitment["pre_run_commitment_hash"],
        "a6_timeout_seconds": A6_TIMEOUT_SECONDS,
        "perturbation_executed": False,
    }
    write_new(paths["result"], result)
    return {"admitted": admitted, "status": "admission_evaluated", "refusals": refusals,
            "calls": calls, "run_dir": str(paths["dir"].relative_to(ROOT)),
            "attempt_index": index, "batch_id": BATCH_ID, "commitment": batch_commitment,
            "candidate_commitment": commitment["pre_run_commitment_hash"]}


def failure_outcome(exc, row, batch_commitment):
    run_dir = None
    if exc.attempt_dir is not None:
        run_dir = str(pathlib.Path(exc.attempt_dir).relative_to(ROOT))
    return {"admitted": False, "status": "technical_failure",
            "refusals": [exc.code], "calls": int(exc.calls or 0),
            "run_dir": run_dir, "batch_id": BATCH_ID,
            "commitment": batch_commitment,
            "failure_detail": {"code": exc.code, "message": str(exc.detail)[:500]}}


# ------------------------------------------------------------------ 主循环

def run(limit=None, adapter=None):
    gate = require_clearance()
    plan, _ = load_plan()
    files = static_inputs()
    commitment = commitment_of(files)
    batch_file = BATCHES / f"{BATCH_ID}.json"
    if not batch_file.is_file():
        raise SystemExit("批次承诺文件缺失；先执行 --freeze")
    if load_json(batch_file).get("pre_run_commitment_hash") != commitment:
        raise SystemExit("输入字节与冻结批次承诺不符，拒绝执行")
    state = load_state(commitment)
    write_ledger(plan, state, commitment, "running")
    executed = 0
    while True:
        action = scheduler.next_action(plan, state)
        if action["action"] == "stop":
            break
        row = action["row"]
        if action["action"] == "skip":
            scheduler.skip(state, row, action["reason"])
            write_ledger(plan, state, commitment, "running")
            continue
        if limit is not None and executed >= limit:
            break
        try:
            outcome = execute_attempt(row, commitment, adapter=adapter)
        except PreparationFailure as exc:
            outcome = failure_outcome(exc, row, commitment)
            if outcome["run_dir"]:
                write_json(pathlib.Path(ROOT) / outcome["run_dir"] / "failure.json",
                           outcome["failure_detail"])
        except Exception as exc:  # 仪器级异常：记录后中止，不伪装成技术失败
            abort = {"project": row["project"], "position": row["position"],
                     "error": f"{type(exc).__name__}: {exc}", "at_utc": now_utc()}
            state["abort_history"].append(abort)
            write_ledger(plan, state, commitment, "aborted", abort=abort)
            print(json.dumps({"status": "instrument_abort", **abort},
                             ensure_ascii=False, indent=2))
            return 3
        scheduler.settle(state, row, outcome)
        executed += 1
        write_ledger(plan, state, commitment, "running")
    doc = write_ledger(plan, state, commitment,
                       "completed" if scheduler.stop_reason(plan, state) else "partial")
    print(json.dumps({"clearance": gate["clearance_sha256"], "summary": doc["summary"]},
                     ensure_ascii=False, indent=2))
    return 0


def status():
    plan, _ = load_plan()
    gate = clearance_gate()
    state = load_state(None) if LEDGER.is_file() else empty_state()
    print(json.dumps({"batch_id": BATCH_ID, "clearance": gate,
                      "ledger": str(LEDGER.relative_to(ROOT)) if LEDGER.is_file() else None,
                      "summary": scheduler.summary(plan, state),
                      "abort_history": state.get("abort_history", []),
                      "planned_positions": len(plan)}, ensure_ascii=False, indent=2))
    return 0


def synthetic():
    import synthetic as proof
    document = proof.run_all()
    write_json(STATE / "proofs" / "SYNTHETIC_CONSUMPTION_PROOF.json", document)
    print(json.dumps(document["summary"], ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="PyHost-Context 新阶段 S1a 准入入口")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--plan", action="store_true", help="写出计划草稿（只读）")
    group.add_argument("--status", action="store_true", help="显示放行与账本状态（只读）")
    group.add_argument("--clearance-template", action="store_true", help="写出放行件申请模板")
    group.add_argument("--freeze", action="store_true", help="写批次承诺（需放行）")
    group.add_argument("--run", action="store_true", help="真实准入（需放行）")
    group.add_argument("--synthetic", action="store_true", help="合成消费证明（不执行候选）")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    if args.plan:
        return write_plan_draft()
    if args.status:
        return status()
    if args.clearance_template:
        return clearance_request()
    if args.freeze:
        return freeze_batch()
    if args.run:
        return run(limit=args.limit)
    if args.synthetic:
        return synthetic()
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
