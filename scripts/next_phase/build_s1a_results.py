"""ClawsGO Science Agent：S1a 准入结果的只读汇总与机械核对。

读取已完成的账本、逐尝试 receipt 与候选记录，重算摘要、调用计数与承诺，
输出 `S1A_ADMISSION_RESULTS.json` 与 `S1A_ADMISSION_PER_CANDIDATE.csv`。
本脚本不执行候选、不安装、不联网，也不改写任何既有证据。
"""
import csv
import hashlib
import json
import pathlib
import sys

NEXT = pathlib.Path(__file__).resolve().parent
EXT = NEXT.parent
ROOT = EXT.parent
sys.path.insert(0, str(NEXT))
sys.path.insert(0, str(NEXT / "admission"))

from admission import scheduler  # noqa: E402

BATCH_ID = "next-admission-20261018"
RUNS = NEXT / "state" / "runs" / BATCH_ID
LEDGER = RUNS / "ledger.json"
BATCH = NEXT / "state" / "batches" / f"{BATCH_ID}.json"
PLAN = NEXT / "state" / "plans" / "next_admission_plan_draft.json"
CANDIDATES = NEXT / "state" / "candidates_next.json"
CLEARANCE = NEXT / "effective" / "REVIEWER001_NEXT_ADMISSION_CLEARANCE.json"
DIAGNOSTICS = RUNS / "diagnostics" / "post_hoc" / "DIAGNOSTICS.json"
OUT_JSON = NEXT / "S1A_ADMISSION_RESULTS_20261018.json"
OUT_CSV = NEXT / "S1A_ADMISSION_PER_CANDIDATE_20261018.csv"


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def commitment_of(files):
    payload = "".join(f"{path}:{digest}\n" for path, digest in sorted(files.items()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_STEM_CACHE = {}


def local_module_stems(sdist):
    """sdist 内自带的模块/包名集合，用于区分“缺外部依赖”与“自身测试模块导入失败”。"""
    import tarfile
    name = (sdist or {}).get("filename")
    if not name:
        return frozenset()
    if name in _STEM_CACHE:
        return _STEM_CACHE[name]
    stems = set()
    for base in (NEXT / "state" / "artifacts", NEXT / "cache" / "sdists"):
        matches = list(base.glob(f"*/{name}")) + list(base.glob(name))
        for path in matches:
            if not path.is_file() or sha(path) != sdist.get("sha256"):
                continue
            with tarfile.open(path) as tar:
                for member in tar.getmembers():
                    parts = pathlib.PurePosixPath(member.name).parts
                    if len(parts) >= 2:
                        stems.add(parts[-1].removesuffix(".py"))
                        if member.isdir():
                            stems.add(parts[-1])
    _STEM_CACHE[name] = frozenset(stems)
    return _STEM_CACHE[name]


def classify(row, result, plan_row):
    """把仪器拒绝原因归类为可复算的技术类别；类别文字只描述证据，不改判据。"""
    detail = (result or {}).get("detail") or {}
    a6 = (detail.get("A6_collect_attempts") or [{}])[-1]
    a7 = detail.get("A7_baseline") or {}
    m1 = detail.get("M1") or {}
    refusals = row.get("refusals") or []
    if row["admitted"]:
        return "admitted"
    if "dependency_resolution_failed" in refusals:
        return "frozen_stack_resolution_impossible"
    if m1.get("applied") and not detail.get("A6_collect_ok"):
        return "declared_dependency_supplement_insufficient"
    if not detail.get("A6_collect_ok"):
        missing = a6.get("missing_modules") or []
        if missing:
            stems = local_module_stems(plan_row.get("sdist"))
            if all(module in stems for module in missing):
                return "local_test_module_import_error"
            return "missing_module_without_declaration_witness"
        summary = (a6.get("summary_line") or "").lower()
        if a6.get("returncode") == 5 or "no tests collected" in summary:
            return "no_tests_collected"
        if a6.get("returncode") not in (0, None) and a6.get("collected") is None:
            return "collection_aborted_by_project_config_or_plugin"
        if a6.get("collected") is not None and a6.get("collected") < 50:
            return "below_minimum_collected_gate"
        return "collection_error_other"
    if a7.get("timed_out"):
        return "baseline_timeout_240s"
    if a7.get("within_max_seconds") is not True:
        return "baseline_over_60s_gate"
    return "other_admission_refusal"


def artifact_scope(project, version, sdist):
    """只读核对实际工件字节：普通文件计数、链接成员单列、PKG-INFO 的 Name/Version。"""
    import tarfile
    from email.parser import BytesParser
    from packaging.utils import canonicalize_name
    name = (sdist or {}).get("filename")
    if not name:
        return {"available": False}
    for base in (NEXT / "state" / "artifacts" / project, NEXT / "cache" / "sdists"):
        path = base / name
        if path.is_file() and sha(path) == sdist.get("sha256"):
            regular, links = [], []
            with tarfile.open(path) as tar:
                for member in tar.getmembers():
                    if member.isfile():
                        regular.append(member.name)
                    elif member.issym() or member.islnk():
                        links.append({"name": member.name,
                                      "type": "symlink" if member.issym() else "hardlink",
                                      "target": member.linkname})
                metadata = next((m.name for m in tar.getmembers()
                                 if m.name.endswith("PKG-INFO") and m.isfile()), None)
                info = {}
                if metadata:
                    raw = tar.extractfile(metadata).read()
                    parsed = BytesParser().parsebytes(raw)
                    info = {"metadata_member": metadata,
                            "name": parsed.get("Name"), "version": parsed.get("Version")}
            matched = (canonicalize_name(info.get("name") or "") == canonicalize_name(project)
                       and str(info.get("version")) == str(version))
            return {"available": True, "path": str(path.relative_to(ROOT)),
                    "sha256": sdist.get("sha256"), "bytes": path.stat().st_size,
                    "regular_files": len(regular), "links": links, **info,
                    "identity_matches_plan": matched}
    return {"available": False, "reason": "artifact_bytes_not_found", "filename": name}


def main():
    problems = []
    ledger = load(LEDGER)
    plan = load(PLAN)["rows"]
    batch = load(BATCH)
    clearance = load(CLEARANCE)
    candidates = load(CANDIDATES)["candidates"]
    diagnostics = load(DIAGNOSTICS) if DIAGNOSTICS.is_file() else {"diagnostics": []}

    by_position = {row["position"]: row for row in plan}
    records = ledger["records"]
    skips = ledger["skips"]

    # 位置与配额
    positions = [r["position"] for r in records] + [s["position"] for s in skips]
    if sorted(positions) != list(range(1, len(plan) + 1)):
        problems.append("position_coverage")
    if len(set(positions)) != len(positions):
        problems.append("duplicate_positions")
    dispatchable = [row for row in plan if row.get("eligible", True)]
    if len(records) != len(dispatchable):
        problems.append(f"attempted_not_equal_dispatchable:{len(records)}!={len(dispatchable)}")
    static_skips = [s for s in skips if s["reason"] == "static_ineligible"]
    if len(static_skips) != len([r for r in plan if not r.get("eligible", True)]):
        problems.append("static_skip_mismatch")

    # 调用与承诺
    calls_by_kind = {"A6": 0, "A7": 0}
    total_calls = 0
    rows = []
    for record in records:
        plan_row = by_position[record["position"]]
        for field in ("project", "group", "version", "canonical_family"):
            if record.get(field) != plan_row.get(field):
                problems.append(f"record_plan_mismatch:{record['position']}:{field}")
        attempt = (ROOT / record["run_dir"]) if record.get("run_dir") else None
        result = load(attempt / "result.json") if attempt and (attempt / "result.json").is_file() else None
        started = load(attempt / "started.json") if attempt and (attempt / "started.json").is_file() else {}
        commitment = load(attempt / "commitment.json") if attempt and (attempt / "commitment.json").is_file() else None
        lock = load(attempt / "environment_lock.json") if attempt and (attempt / "environment_lock.json").is_file() else {}
        failure = load(attempt / "failure.json") if attempt and (attempt / "failure.json").is_file() else None
        calls = (result or {}).get("calls_started") or []
        for call in calls:
            calls_by_kind[call["kind"]] = calls_by_kind.get(call["kind"], 0) + 1
        total_calls += len(calls)
        if len(calls) != record["calls"]:
            problems.append(f"call_count_mismatch:{record['position']}")
        if len(calls) > scheduler.CALLS_PER_ATTEMPT:
            problems.append(f"call_cap:{record['position']}")
        if commitment:
            if commitment_of(commitment["files"]) != commitment["pre_run_commitment_hash"]:
                problems.append(f"candidate_commitment:{record['position']}")
            if commitment.get("batch_commitment") != ledger["commitment"]:
                problems.append(f"candidate_batch_binding:{record['position']}")
        detail = (result or {}).get("detail") or {}
        a6_attempts = detail.get("A6_collect_attempts") or []
        if len(a6_attempts) > 2:
            problems.append(f"a6_attempt_cap:{record['position']}")
        if result and len(a6_attempts) + (1 if result.get("admitted") or detail.get("A7_baseline") else 0) > 3:
            problems.append(f"call_cap_total:{record['position']}")

        a6_last = a6_attempts[-1] if a6_attempts else {}
        a7 = detail.get("A7_baseline") or {}
        dep = (result or {}).get("dependency_agreement") or {}
        candidate_record = candidates.get(record["project"]) or {}
        rows.append({
            "position": record["position"],
            "group": record["group"],
            "project": record["project"],
            "version": record["version"],
            "canonical_family": record["canonical_family"],
            "admitted": record["admitted"],
            "refusals": record["refusals"],
            "technical_class": classify(record, result, plan_row),
            "calls": record["calls"],
            "call_kinds": [c["kind"] for c in calls],
            "attempt_dir": record.get("run_dir"),
            "attempt_index": started.get("attempt_index"),
            "started_at_utc": started.get("started_at_utc"),
            "A6": {
                "attempts": len(a6_attempts),
                "returncode": a6_last.get("returncode"),
                "collected": a6_last.get("collected"),
                "missing_modules": a6_last.get("missing_modules"),
                "summary_line": a6_last.get("summary_line"),
                "stderr_tail": a6_last.get("stderr_tail"),
                "stdout_tail": a6_last.get("stdout_tail"),
            },
            "M1": detail.get("M1"),
            "A7": {
                "returncode": a7.get("returncode"),
                "timed_out": a7.get("timed_out"),
                "duration_seconds": a7.get("duration_seconds"),
                "within_max_seconds": a7.get("within_max_seconds"),
                "session_exitstatus": a7.get("session_exitstatus"),
                "collected": a7.get("collected"),
                "perturbation_valid": a7.get("perturbation_valid"),
                "network_denied": (a7.get("network") or {}).get("denied"),
                "network_allowed": (a7.get("network") or {}).get("allowed"),
            },
            "A8": {
                "restore_ok": detail.get("A8_restore_ok"),
                "restore_twice": detail.get("A8_restore_twice"),
            },
            "environment_accounted": (detail.get("environment_accounting") or {}).get("accounted"),
            "dependency_agreement": dep,
            "sdist": plan_row.get("sdist", {}),
            "artifact_scope": artifact_scope(record["project"], record["version"],
                                             plan_row.get("sdist") or {}),
            "sdist_host": lock.get("sdist_host"),
            "extras_installed": lock.get("extras_installed"),
            "resolved_distributions": lock.get("resolved_distributions"),
            "failure": failure,
            "candidate_commitment": (result or {}).get("candidate_commitment"),
            "result_sha256": sha(attempt / "result.json") if attempt and (attempt / "result.json").is_file() else None,
        })

    # 摘要重放
    state = {"records": records, "skips": skips, "abort_history": ledger.get("abort_history", [])}
    replayed = scheduler.summary(plan, state)
    for key in ("attempted", "skipped", "mechanism_passes", "reference_passes", "calls_used", "stop"):
        if replayed.get(key) != ledger.get("summary", {}).get(key):
            problems.append(f"ledger_summary_replay:{key}")
    if total_calls != ledger.get("summary", {}).get("calls_used"):
        problems.append("ledger_calls_used")
    if replayed["calls_used"] != sum(r["calls"] for r in records):
        problems.append("scheduler_calls_used")

    # 静态跳过
    skip_rows = [{"position": s["position"], "project": s["project"], "reason": s["reason"]}
                 for s in skips]

    # 技术类别分布
    taxonomy = {}
    for row in rows:
        taxonomy[row["technical_class"]] = taxonomy.get(row["technical_class"], 0) + 1

    # 依赖一致性：机制候选中带依赖通道者
    dep_rows = [{"project": r["project"], "admitted": r["admitted"],
                 **{k: r["dependency_agreement"].get(k) for k in
                    ("applicable", "dependency", "static_version", "resolved_version", "match")}}
                for r in rows if r["dependency_agreement"].get("applicable")]

    result = {
        "schema_version": "pyhost-context/s1a-admission-results/1.0",
        "batch_id": BATCH_ID,
        "runner_profile": ledger["runner_profile"],
        "generated_by": "ClawsGO Science Agent",
        "generated_at_utc": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
        "authorization": {
            "clearance_path": str(CLEARANCE.relative_to(ROOT)),
            "clearance_sha256": sha(CLEARANCE),
            "signed_by": clearance["signed_by"],
            "decision": clearance["decision"],
            "static_inputs_commitment": clearance["static_inputs_commitment"],
            "scope": clearance["scope"],
        },
        "batch_commitment": {
            "path": str(BATCH.relative_to(ROOT)),
            "pre_run_commitment_hash": batch["pre_run_commitment_hash"],
            "files": len(batch["files"]),
            "matches_clearance": batch["pre_run_commitment_hash"] == clearance["static_inputs_commitment"],
        },
        "environment": {
            "entry_python": sys.version.split()[0],
            "target_python": "3.11.9",
            "pytest": "7.2.0",
            "a6_timeout_seconds": 240,
            "a7_timeout_seconds": 240,
            "baseline_admission_max_seconds": 60,
            "minimum_collected": 50,
        },
        "summary": {
            **{k: replayed[k] for k in ("planned_positions", "attempted", "skipped",
                                        "mechanism_passes", "reference_passes", "calls_used",
                                        "stop", "targets", "family_cap", "family_pass_counts",
                                        "skip_reasons")},
            "dispatchable_candidates": len(dispatchable),
            "static_ineligible_positions": len(static_skips),
            "calls_by_kind": calls_by_kind,
            "call_ceiling_dispatchable": len(dispatchable) * scheduler.CALLS_PER_ATTEMPT,
            "call_ceiling_named": len(plan) * scheduler.CALLS_PER_ATTEMPT,
            "admitted_projects": [r["project"] for r in rows if r["admitted"]],
            "mechanism_target_met": replayed["mechanism_passes"] >= scheduler.TARGET_PASSES["mechanism"],
            "reference_target_met": replayed["reference_passes"] >= scheduler.TARGET_PASSES["reference"],
        },
        "technical_taxonomy": taxonomy,
        "dependency_agreement": {
            "rows": dep_rows,
            "note": "仅机制池静态见证带依赖通道者适用；不一致须在 S1b 扰动前按同一静态规则重核读取链。",
        },
        "skips": skip_rows,
        "candidates": rows,
        "post_hoc_diagnostics": diagnostics,
        "limitations": {
            "candidate_replacement": "无；静态排除 4 项不派发，技术失败不顺延补位。",
            "retries": "每个具名候选仅一次尝试；无 attempt-002；无为取得通过而重跑。",
            "s3_or_matrix_results": "未执行 S1b/S2/S3/repro；本文件只含 S1a 准入结局。",
            "exposure": "参照池 16 项均保留既有 B0 准入信息暴露标签；不声称未见过。",
            "artifact_scope": "普通文件视图；链接成员按候选逐条列在身份回执中。",
            "g1_or_evidence_grade_changed": False,
        },
        "problems": problems,
        "ok": not problems,
    }
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    with OUT_CSV.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["position", "group", "project", "version", "family", "admitted",
                         "technical_class", "refusals", "calls", "a6_rc", "a6_collected",
                         "missing_modules", "a7_seconds", "a7_timed_out", "a7_collected"])
        for row in rows:
            writer.writerow([row["position"], row["group"], row["project"], row["version"],
                             row["canonical_family"], row["admitted"], row["technical_class"],
                             ";".join(row["refusals"]), row["calls"],
                             row["A6"]["returncode"], row["A6"]["collected"],
                             ";".join(row["A6"]["missing_modules"] or []),
                             row["A7"]["duration_seconds"], row["A7"]["timed_out"],
                             row["A7"]["collected"]])
    print(json.dumps({"ok": not problems, "problems": problems, "summary": result["summary"],
                      "taxonomy": taxonomy,
                      "json": str(OUT_JSON), "csv": str(OUT_CSV),
                      "json_sha256": sha(OUT_JSON), "csv_sha256": sha(OUT_CSV)},
                     ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
