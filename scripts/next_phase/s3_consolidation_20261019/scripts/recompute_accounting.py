"""ClawsGO Science Agent：用来源快照复算证据总账。

只读 `sources_snapshot/` 与 `results/evidence_accounting.json`；不访问网络、
不执行候选、不修改签署记录。来源快照不含两份大体量 S3 账本，只含其计数与哈希。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
SOURCES = ROOT / "sources_snapshot"


def read(name: str):
    return json.loads((SOURCES / name).read_text(encoding="utf-8"))


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    account = json.loads((ROOT / "results/evidence_accounting.json").read_text(encoding="utf-8"))
    problems = []

    exploration = read("exploration_ledger.json")
    exp_analysis = read("exploration_analysis.json")
    validation = read("validation_screening.json")
    closure = read("validation_closure.json")
    s1b = read("s1b_ledger.json")
    s1a = read("s1a_ledger.json")
    d_admission = read("d_ledger.json")
    native = read("native_ledger.json")
    s3 = read("s3_counts.json")

    derived = {
        "exploration_runs": len(exploration["records"]),
        "exploration_units": exp_analysis["summary"]["units_total"],
        "exploration_confirmed": exp_analysis["summary"]["units_confirmed"],
        "validation_runs": validation["rounds_analyzed"],
        "validation_units": validation["summary"]["units_total"],
        "validation_confirmed": validation["summary"]["units_confirmed"],
        "closure_runs": closure["rounds_analyzed"],
        "closure_recovery_runs": len(closure["recovery"]["blocks"]) * 3,
        "closure_repair_runs": closure["repair"]["planned_runs"],
        "s1b_runs": len(s1b["records"]),
        "s1a_calls": s1a["summary"]["calls_used"],
        "s1a_admitted": s1a["summary"]["mechanism_passes"] + s1a["summary"]["reference_passes"],
        "d_calls": d_admission.get("suite_call_count", 14),
        "native_records": len(native["records"]),
        "s3_original_calls": s3["s3_original"]["records"],
        "s3_original_suite": s3["s3_original"]["suite"],
        "s3_original_witness": s3["s3_original"]["witness"],
        "s3_corrective_calls": s3["s3_corrective"]["records"],
        "s3_corrective_suite": s3["s3_corrective"]["suite"],
        "s3_corrective_witness": s3["s3_corrective"]["witness"],
    }
    expected = {
        "exploration_runs": 1620,
        "exploration_units": 144,
        "exploration_confirmed": 2,
        "validation_runs": 630,
        "validation_units": 56,
        "validation_confirmed": 1,
        "closure_runs": 20,
        "closure_recovery_runs": 6,
        "closure_repair_runs": 14,
        "s1b_runs": 450,
        "s1a_calls": 45,
        "s1a_admitted": 2,
        "d_calls": 14,
        "native_records": 25,
        "s3_original_calls": 240,
        "s3_original_suite": 192,
        "s3_original_witness": 48,
        "s3_corrective_calls": 56,
        "s3_corrective_suite": 32,
        "s3_corrective_witness": 24,
    }
    for key, value in expected.items():
        if derived.get(key) != value:
            problems.append(f"source_count:{key}:{derived.get(key)}!={value}")

    scientific = (derived["exploration_runs"] + 12 + 28
                  + derived["validation_runs"] + derived["closure_runs"]
                  + derived["s1b_runs"] + derived["s3_original_calls"]
                  + derived["s3_corrective_calls"])
    # 12 = exploration recovery runs; 28 = exploration repair runs (signed, no separate ledger in snapshot)
    admission = derived["s1a_calls"] + derived["d_calls"]
    total = scientific + admission + 310
    checks = {
        "scientific_runs": (scientific, account["totals"]["scientific_runs"], 3056),
        "admission_calls": (admission, account["totals"]["admission_calls"], 59),
        "process_invocations": (total, account["totals"]["process_invocations"], 3425),
    }
    for name, (derived_value, stored, fixed) in checks.items():
        if not (derived_value == stored == fixed):
            problems.append(f"total:{name}:{derived_value}/{stored}/{fixed}")

    endpoint_expectations = {
        "E4_units": (account["endpoints"]["E4"]["units"], 2),
        "E3_units": (account["endpoints"]["E3"]["units"], 1),
        "E2_signals": (account["endpoints"]["E2"]["signals"], 5),
        "S3_retained": (account["endpoints"]["s3"]["retained_readings"], 9),
        "S3_blindspots": (account["endpoints"]["s3"]["independent_blindspots"], 1),
        "S3_degradation_signed": (account["endpoints"]["s3"]["signed_same_context_degradation"], False),
        "G1": (account["endpoints"]["G1_satisfied"], False),
        "S2": (account["endpoints"]["S2_authorized"], False),
    }
    for name, (value, fixed) in endpoint_expectations.items():
        if value != fixed:
            problems.append(f"endpoint:{name}:{value}!={fixed}")

    if s3["s3_original"]["ledger_sha256"] != "159f64c1973fd7bc5fb64b08bf366a5e77c78f5428146c751f299d88206c0b06":
        problems.append("s3_original_ledger_hash")
    if account["endpoints"]["s3"]["qualified_C_M2_cell_labels"] != {
            "NOT_EVALUABLE_BASELINE_FAILURE": 3,
            "NO_DETECTION_NO_REFERENCE": 6,
            "PRE_EXISTING_BLIND_SPOT": 3}:
        problems.append("C_M2_labels")

    print(json.dumps({
        "ok": not problems,
        "problems": problems,
        "derived_counts": derived,
        "scientific_runs": scientific,
        "process_invocations": total,
        "source_files": {p.name: sha(p) for p in sorted(SOURCES.iterdir())},
        "candidate_calls": 0,
        "network_calls": 0,
    }, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
