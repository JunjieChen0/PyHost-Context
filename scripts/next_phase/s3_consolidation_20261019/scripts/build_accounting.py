"""ClawsGO Science Agent：汇总既有证据的总分母、分组、端点与成本。

只读；从已签认分析文件与账本读取计数，不执行候选、不联网、不改写旧记录。
"""
from __future__ import annotations

import hashlib
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
EXT = ROOT.parents[1]  # pyhost-context/extension
NEXT = EXT / "next_phase"


def read(path: pathlib.Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build() -> dict:
    exploration = read(EXT / "freeze/ledgers/exploration_20261015.json")
    exp_analysis = read(EXT / "freeze/analysis/exploration_20261015_reviewed_v2.json")
    validation = read(EXT / "freeze/analysis/validation_7_20261016_screening.json")
    closure = read(EXT / "freeze/analysis/validation_closure20_20261017.json")
    s1b = read(NEXT / "state/s1b_20261019/ledger.json")
    s1a = read(NEXT / "state/runs/next-admission-20261018/ledger.json")
    d_admission = read(NEXT / "state/next-D-admission-reviewed-20261018/ledger.json")
    native = read(EXT / "state/runs/native-tool-hunt24-20261017/ledger.json")
    s3_old = read(NEXT / "state/s3_reviewer001_20261019/ledger.json")
    s3_fix = read(NEXT / "state/s3_corrective_20261019/ledger.json")
    s3_closure = read(ROOT / "provenance/REVIEWER001_S3_CORRECTIVE_CLOSURE_20261019.json")

    cohorts = {
        "exploration": {
            "projects": 18,
            "units": exp_analysis["summary"]["units_total"],
            "runs": len(exploration["records"]),
            "matrix_runs": 810,
            "b0_runs": 810,
            "units_confirmed": exp_analysis["summary"]["units_confirmed"],
            "units_with_flips": exp_analysis["summary"]["units_with_flips"],
        },
        "exploration_recovery": {"runs": 12, "blocks": 4},
        "exploration_repair": {"runs": 28, "units": 2},
        "validation": {
            "projects": 7,
            "units": validation["summary"]["units_total"],
            "runs": validation["rounds_analyzed"],
            "units_confirmed": validation["summary"]["units_confirmed"],
            "matrix_runs": 315,
            "b0_runs": 315,
        },
        "validation_closure": {
            "runs": closure["rounds_analyzed"],
            "recovery_runs": len(closure["recovery"]["blocks"]) * 3,
            "repair_runs": closure["repair"]["planned_runs"],
        },
        "s1b": {"projects": 5, "units": 40, "runs": len(s1b["records"])},
        "s1a_admission": {
            "positions": s1a["summary"]["planned_positions"],
            "attempted": s1a["summary"]["attempted"],
            "calls": s1a["summary"]["calls_used"],
            "admitted": s1a["summary"]["mechanism_passes"]
            + s1a["summary"]["reference_passes"],
        },
        "d_admission": {
            "projects": len(d_admission["plan"]),
            "calls": d_admission.get("suite_call_count", 14),
        },
        "native_tool": {
            "projects": len(native.get("cohort_projects", [])),
            "ledger_records": len(native["records"]),
            "pytest_calls": 310,
            "final_reports": 21,
        },
        "s3_original": {"calls": len(s3_old["records"]), "suite": 192, "witness": 48},
        "s3_corrective": {
            "calls": len(s3_fix["records"]),
            "suite": 32,
            "witness": 24,
        },
    }

    scientific = (
        cohorts["exploration"]["runs"]
        + cohorts["exploration_recovery"]["runs"]
        + cohorts["exploration_repair"]["runs"]
        + cohorts["validation"]["runs"]
        + cohorts["validation_closure"]["runs"]
        + cohorts["s1b"]["runs"]
        + cohorts["s3_original"]["calls"]
        + cohorts["s3_corrective"]["calls"]
    )
    admission = cohorts["s1a_admission"]["calls"] + cohorts["d_admission"]["calls"]
    total = scientific + admission + cohorts["native_tool"]["pytest_calls"]

    endpoints = {
        "E4": {"units": 2, "projects": ["rich-argparse"], "conditions": ["K05_NO_COLOR", "K07_COLUMNS"]},
        "E3": {"units": 1, "projects": ["rich-rst"], "conditions": ["K07_COLUMNS"]},
        "E2": {"signals": 5, "projects": ["typer", "twine", "rich-toolkit"]},
        "screening_confirmed": {"units": 1, "projects": ["rich-rst"]},
        "repair": {"exploration": "6/6 limited", "validation": "3/3 limited"},
        "s3": {
            "retained_readings": 9,
            "independent_blindspots": s3_closure.get(
                "C_M2_independent_original_suite_blindspots", 1),
            "signed_same_context_degradation": s3_closure.get(
                "same_context_degradation_signed", False),
            "qualified_C_M2_cell_labels": s3_closure.get(
                "C_M2_qualified_repair_cell_labels"),
        },
        "G1_satisfied": s3_closure.get("G1_satisfied"),
        "functional_defect_confirmed": s3_closure.get("functional_defects_confirmed"),
        "S2_authorized": s3_closure.get("S2_authorized"),
    }
    cost = {
        "scientific_runs": scientific,
        "admission_calls": admission,
        "native_tool_calls": cohorts["native_tool"]["pytest_calls"],
        "process_invocations_total": total,
        "sealed_feasibility_processes_excluded": 337,
        "extension_tree_bytes": int(sum(p.stat().st_size for p in EXT.rglob("*") if p.is_file())),
        "review_directories": len([p for p in (EXT / "reviews").iterdir() if p.is_dir()]),
        "review_entries": len(list((EXT / "reviews").iterdir())),
        "s3_original_state_bytes": int(sum(
            p.stat().st_size for p in (NEXT / "state/s3_reviewer001_20261019").rglob("*") if p.is_file())),
        "s3_corrective_state_bytes": int(sum(
            p.stat().st_size for p in (NEXT / "state/s3_corrective_20261019").rglob("*") if p.is_file())),
    }
    sources = {
        "exploration_ledger": sha(EXT / "freeze/ledgers/exploration_20261015.json"),
        "exploration_analysis": sha(EXT / "freeze/analysis/exploration_20261015_reviewed_v2.json"),
        "validation_screening": sha(EXT / "freeze/analysis/validation_7_20261016_screening.json"),
        "validation_closure": sha(EXT / "freeze/analysis/validation_closure20_20261017.json"),
        "s1b_ledger": sha(NEXT / "state/s1b_20261019/ledger.json"),
        "s1a_ledger": sha(NEXT / "state/runs/next-admission-20261018/ledger.json"),
        "d_ledger": sha(NEXT / "state/next-D-admission-reviewed-20261018/ledger.json"),
        "native_ledger": sha(EXT / "state/runs/native-tool-hunt24-20261017/ledger.json"),
        "s3_original_ledger": sha(NEXT / "state/s3_reviewer001_20261019/ledger.json"),
        "s3_corrective_ledger": sha(NEXT / "state/s3_corrective_20261019/ledger.json"),
        "s3_corrective_closure": sha(
            ROOT / "provenance/REVIEWER001_S3_CORRECTIVE_CLOSURE_20261019.json"),
    }
    return {
        "schema_version": "pyhost-context/evidence-accounting/1.0",
        "sealed_feasibility": {
            "projects": 6, "core": 270, "recovery": 12, "witness": 54,
            "pilot": 1, "processes": 337, "in_extension_denominator": False,
        },
        "cohorts": cohorts,
        "totals": {
            "scientific_runs": scientific,
            "admission_calls": admission,
            "native_tool_calls": cohorts["native_tool"]["pytest_calls"],
            "process_invocations": total,
        },
        "endpoints": endpoints,
        "cost": cost,
        "sources": sources,
    }


def main() -> int:
    payload = build()
    out = ROOT / "results" / "evidence_accounting.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(json.dumps({"out": str(out), "scientific_runs": payload["totals"]["scientific_runs"],
                      "process_invocations": payload["totals"]["process_invocations"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
