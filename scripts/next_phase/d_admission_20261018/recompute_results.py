#!/usr/bin/env python3
"""ClawsGO Science Agent：D 档一次性 B0 准入结果离线复算。

只读随包文件；不联网、不安装、不导入、不执行任何候选项目。
校验：包清单、签署绑定、账本、逐项判定、锁哈希、安装集合、A7 JSONL 哈希、
A8 两次恢复与最终对账，并重算 182 件 wheel 的去重总字节。
"""
from __future__ import annotations

import hashlib
import json
import re
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
PROJECTS = ("ipython", "structlog", "rich-toolkit", "twine", "rich-click", "hatch", "pdm")
OBSERVER = {"pyhost-observer": "0.2.0"}


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def canon(name):
    return re.sub(r"[-_.]+", "-", str(name)).lower()


def decision(outcome):
    """与冻结 run_extension.admission_decision 相同的规则。"""
    base = outcome.get("A7_baseline") or {}
    accounting = outcome.get("environment_accounting") or {}
    refusals = []
    if not outcome.get("A5_environment_ok"):
        refusals.append("A5_environment")
    if not outcome.get("A6_collect_ok"):
        refusals.append("A6_collect")
    if not outcome.get("A8_restore_ok"):
        refusals.append("A8_restore")
    if base.get("timed_out"):
        refusals.append("A7_timeout")
    if base.get("session_exitstatus") is None:
        refusals.append("A7_incomplete_session")
    if base.get("within_max_seconds") is not True:
        refusals.append("A7_over_time_budget")
    if base.get("perturbation_valid") is not True:
        refusals.append("A7_perturbation_invalid_or_missing")
    if accounting.get("accounted") is not True:
        refusals.append("environment_not_accounted")
    return (not refusals), refusals


def main():
    problems = []
    notes = []

    # 1. 包清单
    manifest = read(ROOT / "PACKAGE_MANIFEST.json")
    listed = {m["path"]: m for m in manifest["members"]}
    for name, item in listed.items():
        path = ROOT / name
        if not path.is_file():
            problems.append(f"member_missing:{name}")
        elif path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
            problems.append(f"member_drift:{name}")
    on_disk = {str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
               if p.is_file() and p.name != "PACKAGE_MANIFEST.json"
               and "__pycache__" not in p.parts}
    for name in sorted(on_disk - set(listed)):
        problems.append(f"unlisted_member:{name}")
    notes.append(f"manifest_members={len(listed)}")

    # 2. 签署放行件与其入口
    clearance = read(ROOT / "review/REVIEWER001_D_ADMISSION_CLEARANCE.json")
    if clearance.get("decision") != "APPROVED_FOR_BOUNDED_D_ADMISSION":
        problems.append("clearance_decision")
    if clearance.get("authorization", {}).get("D_admission") is not True:
        problems.append("clearance_D_admission")
    for key in ("S1b", "S2", "S3", "repro", "new_perturbation_runs"):
        if clearance.get("authorization", {}).get(key) is not False:
            problems.append(f"clearance_scope:{key}")
    bindings = clearance.get("bindings") or {}
    shipped = {
        "reviewer/run_d_reviewed.py": "review/run_d_reviewed.py",
        "reviewer/INSTRUMENT_BINDINGS.json": "review/INSTRUMENT_BINDINGS.json",
        "reviewer/D_SOURCE_INPUTS.json": "review/D_SOURCE_INPUTS.json",
        "inputs/tools/run_d_admission.py": "tools/run_d_admission.py",
        "inputs/tools/d_adapter.py": "tools/d_adapter.py",
        "inputs/locks/D_RESOLUTION_LOCKS_WITH_CANDIDATE.json":
            "locks/D_RESOLUTION_LOCKS_WITH_CANDIDATE.json",
        "inputs/inputs/D_ACQUISITION_ROOTS.json": "inputs/D_ACQUISITION_ROOTS.json",
        "inputs/D_PLUGIN_DEFAULTS_20261018.json": "inputs/D_PLUGIN_DEFAULTS_20261018.json",
        "inputs/D_PRE_RUN_COMMITMENT_REV1_20261018.json":
            "inputs/D_PRE_RUN_COMMITMENT_REV1_20261018.json",
        "inputs/artifacts/D_PLUGIN_MAP.json": "artifacts/D_PLUGIN_MAP.json",
    }
    checked = 0
    for bound, local in shipped.items():
        if digest(ROOT / local) != bindings.get(bound):
            problems.append(f"binding_drift:{bound}")
        checked += 1
    notes.append(f"signed_bindings_shipped_checked={checked}/{len(bindings)}")

    # 3. 账本与承诺
    ledger = read(ROOT / "state/ledger.json")
    commitment = read(ROOT / "state/commitment.json")
    if ledger.get("batch_id") != "next-D-admission-reviewed-20261018":
        problems.append("ledger_batch_id")
    if ledger.get("status") != "completed":
        problems.append("ledger_status")
    if ledger.get("commitment") != clearance.get("pre_signing_commitment_hash"):
        problems.append("ledger_commitment_vs_clearance")
    if commitment.get("pre_run_commitment_hash") != ledger.get("commitment"):
        problems.append("commitment_file")
    if commitment.get("files") != bindings:
        problems.append("commitment_bindings_vs_clearance")
    if [p["project"] for p in ledger.get("plan", [])] != list(PROJECTS):
        problems.append("ledger_plan_order")
    if ledger.get("not_run_positions") != []:
        problems.append("ledger_not_run")
    if ledger.get("suite_call_count") != 14:
        problems.append("ledger_suite_calls")
    if len(ledger.get("records") or []) != 7:
        problems.append("ledger_record_count")

    locks = read(ROOT / "locks/D_RESOLUTION_LOCKS_WITH_CANDIDATE.json")
    lock_by_project = {p["project"]: p for p in locks["projects"] if p.get("status") == "locked"}

    # 4. 逐项
    table = []
    for record in ledger["records"]:
        project = record["project"]
        base = ROOT / "state" / project
        full = read(base / "full_record.json")
        if digest(base / "full_record.json") != record.get("full_record_sha256"):
            problems.append(f"full_record_sha256:{project}")
        outcome = full["outcome"]
        admitted, refusals = decision(outcome)
        if admitted != outcome.get("admitted") or admitted != record.get("admitted"):
            problems.append(f"decision_mismatch:{project}")
        if set(refusals) != set(outcome.get("refusals") or []):
            problems.append(f"refusal_list_mismatch:{project}")
        if outcome.get("project") != project:
            problems.append(f"outcome_project:{project}")
        lock = lock_by_project[project]
        lock_sha = hashlib.sha256(
            json.dumps(lock, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if lock_sha != record.get("lock_sha256") or lock_sha != outcome.get("lock_sha256"):
            problems.append(f"lock_sha256:{project}")
        expected = {}
        rows = list(lock["lock"]["rows"])
        if lock.get("candidate_wheel"):
            rows.append(lock["candidate_wheel"])
        for row in rows:
            expected[canon(row["name"])] = row["version"]
        expected.update(OBSERVER)
        installed = {canon(k): str(v) for k, v in (outcome.get("installed_final") or {}).items()}
        if installed != expected:
            problems.append(f"installed_set:{project}")
        if installed.get("pytest") != lock["pytest_version"]:
            problems.append(f"pytest_version:{project}")
        accounting = outcome.get("environment_accounting") or {}
        if accounting.get("accounted") is not True or accounting.get("installed_count") != len(expected):
            problems.append(f"accounting:{project}")
        if (outcome.get("M1") or {}).get("applied") is not False:
            problems.append(f"M1_applied:{project}")
        a8 = (full["record"] or {}).get("A8_restore_twice") or []
        if len(a8) != 2 or any(x.get("identical_to_artifact") is not True for x in a8):
            problems.append(f"A8:{project}")
        if not outcome.get("A8_restore_ok"):
            problems.append(f"A8_flag:{project}")
        first = full.get("first") or {}
        jsonl = ROOT / "state/runtime" / project / "logs" / f"{project}-adm-0-a7.jsonl"
        if not jsonl.is_file():
            problems.append(f"jsonl_missing:{project}")
        elif digest(jsonl) != first.get("jsonl_sha256"):
            problems.append(f"jsonl_sha256:{project}")
        if first.get("malformed_jsonl_lines") not in (0,):
            problems.append(f"jsonl_malformed:{project}")
        network = first.get("network") or {}
        if network.get("allowed") not in (0,):
            problems.append(f"network_allowed:{project}")
        base_summary = outcome.get("A7_baseline") or {}
        counts = {}
        for item in (first.get("summaries") or {}).values():
            counts[item.get("summary")] = counts.get(item.get("summary"), 0) + 1
        if base_summary.get("collected") != first.get("collected"):
            problems.append(f"collected_summary:{project}")
        table.append({
            "position": record["position"], "project": project, "version": record["version"],
            "pytest": lock["pytest_version"], "admitted": admitted, "refusals": refusals,
            "collected": base_summary.get("collected"), "returncode": base_summary.get("returncode"),
            "duration_seconds": round(base_summary.get("duration_seconds") or 0, 2),
            "within_max_seconds": base_summary.get("within_max_seconds"),
            "perturbation_valid": base_summary.get("perturbation_valid"),
            "drift_keys": ((first.get("launch_env_three_view") or {}).get("transition") or {}
                           ).get("post_init_vs_pre_collection_delta"),
            "outcomes": counts,
            "network_denied": network.get("denied"),
            "jsonl_sha256": first.get("jsonl_sha256"),
        })

    # 5. 182 件 wheel 去重总字节
    unique = {}
    for lock in lock_by_project.values():
        rows = list(lock["lock"]["rows"])
        if lock.get("candidate_wheel"):
            rows.append(lock["candidate_wheel"])
        for row in rows:
            unique[row["sha256"]] = row.get("bytes") or row.get("size")
    total = sum(int(v) for v in unique.values() if v is not None)
    if len(unique) != 182 or total != 124210869:
        problems.append(f"wheel_total:{len(unique)}:{total}")
    notes.append(f"unique_wheels={len(unique)} total_bytes={total}")

    result = {"ok": not problems, "problems": problems, "notes": notes,
              "projects": len(table), "admitted": sum(1 for t in table if t["admitted"]),
              "table": table}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
