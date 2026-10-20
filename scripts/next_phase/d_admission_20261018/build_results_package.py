"""ClawsGO Science Agent：打包 D 档一次性 B0 准入结果（单个 ZIP）。"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
STATE = PROJECT / "extension/next_phase/state/next-D-admission-reviewed-20261018"
INPUTS = pathlib.Path("/tmp/drev1_clear/runtime/inputs")
REVIEW = pathlib.Path("/tmp/drev1_clear")
STAGE = pathlib.Path("/tmp/d_admission_stage")
OUT = PROJECT.parent / "deliverables" / "PyHost_D_Admission_Results_20261018.zip"
FIXED = (2026, 10, 18, 0, 0, 0)
PROJECTS = ("ipython", "structlog", "rich-toolkit", "twine", "rich-click", "hatch", "pdm")


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def state_files():
    yield STATE / "commitment.json", "state/commitment.json"
    yield STATE / "ledger.json", "state/ledger.json"
    for project in PROJECTS:
        base = STATE / project
        for name in ("attempt.json", "full_record.json", "A7_full_result.json",
                     "calls_started.json", "process_calls.json"):
            if (base / name).is_file():
                yield base / name, f"state/{project}/{name}"
        for path in sorted(base.glob("process-*")):
            if path.is_file():
                yield path, f"state/{project}/{path.name}"
        for path in sorted(base.glob("*.timeout.*")):
            if path.is_file():
                yield path, f"state/{project}/{path.name}"
        logs = STATE / "runtime" / project / "logs"
        for path in sorted(logs.glob("*")):
            if path.is_file():
                yield path, f"state/runtime/{project}/logs/{path.name}"


def results_document(ledger, locks, clearance):
    rows = []
    for record in ledger["records"]:
        project = record["project"]
        full = read(STATE / project / "full_record.json")
        outcome = full["outcome"]
        first = full["first"]
        lock = next(p for p in locks["projects"] if p["project"] == project)
        rows.append({
            "position": record["position"], "project": project, "version": record["version"],
            "lock_sha256": record["lock_sha256"], "pytest_version": lock["pytest_version"],
            "lock_rows": len(lock["lock"]["rows"]),
            "installed_final": outcome["installed_final"],
            "A5_environment_ok": outcome["A5_environment_ok"],
            "A6_collect_ok": outcome["A6_collect_ok"],
            "A7_baseline": outcome["A7_baseline"],
            "A8_restore_ok": outcome["A8_restore_ok"],
            "A8_restore_twice": full["record"]["A8_restore_twice"],
            "environment_accounting": outcome["environment_accounting"],
            "M1": outcome["M1"],
            "refusals": outcome["refusals"],
            "admitted": outcome["admitted"],
            "outcome_counts": _counts(first.get("summaries") or {}),
            "jsonl_sha256": first.get("jsonl_sha256"),
            "full_record_sha256": record["full_record_sha256"],
            "drift_keys": ((first.get("launch_env_three_view") or {}).get("transition") or {}
                           ).get("post_init_vs_pre_collection_delta"),
            "network": {"allowed": first["network"].get("allowed"),
                        "denied": first["network"].get("denied")},
        })
    unique = {}
    for lock in locks["projects"]:
        if lock.get("status") != "locked":
            continue
        for row in list(lock["lock"]["rows"]) + ([lock["candidate_wheel"]]
                                                 if lock.get("candidate_wheel") else []):
            unique[row["sha256"]] = row.get("bytes") or row.get("size")
    return {
        "schema_version": "pyhost-context/D-admission-results/1.0",
        "batch_id": ledger["batch_id"],
        "status": ledger["status"],
        "entry": {"path": "review/run_d_reviewed.py",
                  "sha256": sha(REVIEW / "patch/run_d_reviewed.py")},
        "clearance": {"path": "review/REVIEWER001_D_ADMISSION_CLEARANCE.json",
                      "sha256": sha(REVIEW / "patch/REVIEWER001_D_ADMISSION_CLEARANCE.json"),
                      "decision": clearance["decision"],
                      "signed_at_utc": clearance["signed_at_utc"],
                      "signed_by": clearance["signed_by"]},
        "pre_run_commitment_hash": ledger["commitment"],
        "plan_order": [p["project"] for p in ledger["plan"]],
        "suite_call_count": ledger["suite_call_count"],
        "admitted_count": ledger["admitted_count"],
        "wheel_inventory": {"unique_wheels": len(unique),
                            "unique_bytes": sum(int(v) for v in unique.values() if v is not None)},
        "projects": rows,
    }


def _counts(summaries):
    counts = {}
    for item in summaries.values():
        key = item.get("summary")
        counts[key] = counts.get(key, 0) + 1
    return counts


def main():
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    for name in ("D_ADMISSION_REPORT_20261018.md", "D_ADMISSION_WITNESSES_20261018.md",
                 "D_ADMISSION_ERRATA_20261018.md"):
        copy(HERE / name, STAGE / name)
    copy(HERE / "README.md", STAGE / "README.md")
    copy(HERE / "recompute_results.py", STAGE / "recompute_results.py")

    for source, target in state_files():
        copy(source, STAGE / target)

    locks_src = INPUTS / "locks"
    copy(locks_src / "D_RESOLUTION_LOCKS_WITH_CANDIDATE.json",
         STAGE / "locks/D_RESOLUTION_LOCKS_WITH_CANDIDATE.json")
    for path in sorted(locks_src.glob("*.json")):
        if path.name != "D_RESOLUTION_LOCKS_WITH_CANDIDATE.json":
            copy(path, STAGE / "locks" / path.name)

    copy(INPUTS / "artifacts/D_PLUGIN_MAP.json", STAGE / "artifacts/D_PLUGIN_MAP.json")
    copy(INPUTS / "D_PLUGIN_DEFAULTS_20261018.json",
         STAGE / "inputs/D_PLUGIN_DEFAULTS_20261018.json")
    copy(INPUTS / "D_PRE_RUN_COMMITMENT_REV1_20261018.json",
         STAGE / "inputs/D_PRE_RUN_COMMITMENT_REV1_20261018.json")
    copy(INPUTS / "inputs/D_ACQUISITION_ROOTS.json",
         STAGE / "inputs/D_ACQUISITION_ROOTS.json")
    copy(INPUTS / "tools/run_d_admission.py", STAGE / "tools/run_d_admission.py")
    copy(INPUTS / "tools/d_adapter.py", STAGE / "tools/d_adapter.py")

    for name in ("REVIEW.md", "REVIEWER001_REVIEW_RECEIPT.json"):
        copy(REVIEW / name, STAGE / "review" / name)
    copy(REVIEW / "patch/REVIEWER001_D_ADMISSION_CLEARANCE.json",
         STAGE / "review/REVIEWER001_D_ADMISSION_CLEARANCE.json")
    copy(REVIEW / "patch/run_d_reviewed.py", STAGE / "review/run_d_reviewed.py")
    copy(REVIEW / "patch/INSTRUMENT_BINDINGS.json", STAGE / "review/INSTRUMENT_BINDINGS.json")
    copy(REVIEW / "patch/D_SOURCE_INPUTS.json", STAGE / "review/D_SOURCE_INPUTS.json")
    (STAGE / "review/PACKAGE_BINDINGS.json").write_text(json.dumps({
        "reviewer_input_zip": "PyHost_D_REV1_Reviewer001_Clearance_20261018.zip",
        "reviewer_input_zip_sha256":
            "457da93b9cd69b7a46b1bd1beb49e4e5f72adb92e637f547da33cc96d62a692f",
        "submitted_rev1_zip_sha256":
            "deabdbadbf962449980c84915082cb297e7d2a8f959ad3651bc377595652f311",
        "clearance_sha256": sha(REVIEW / "patch/REVIEWER001_D_ADMISSION_CLEARANCE.json"),
        "entry_sha256": sha(REVIEW / "patch/run_d_reviewed.py"),
        "pre_run_commitment_hash":
            read(REVIEW / "patch/REVIEWER001_D_ADMISSION_CLEARANCE.json")
            ["pre_signing_commitment_hash"],
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    ledger = read(STATE / "ledger.json")
    locks = read(INPUTS / "locks/D_RESOLUTION_LOCKS_WITH_CANDIDATE.json")
    clearance = read(REVIEW / "patch/REVIEWER001_D_ADMISSION_CLEARANCE.json")
    (STAGE / "D_ADMISSION_RESULTS_20261018.json").write_text(
        json.dumps(results_document(ledger, locks, clearance),
                   ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    members = []
    for path in sorted(STAGE.rglob("*")):
        if path.is_file():
            members.append({"path": str(path.relative_to(STAGE)),
                            "sha256": sha(path), "bytes": path.stat().st_size})
    (STAGE / "PACKAGE_MANIFEST.json").write_text(
        json.dumps({"schema_version": "pyhost-context/D-admission-package/1.0",
                    "members": members}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(STAGE.rglob("*")):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(str(path.relative_to(STAGE)), date_time=FIXED)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    print(json.dumps({"zip": str(OUT), "sha256": sha(OUT),
                      "bytes": OUT.stat().st_size, "members": len(members)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
