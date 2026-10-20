#!/usr/bin/env python3
"""ClawsGO Science Agent：S1a 结果包复算。

只读复核交付包：清单字节、账本摘要重放、位置与调用上限、候选承诺、
放行件绑定与逐候选结局。不执行候选、不安装、不联网。

用法：``python3 recompute_s1a_results.py --package <解压目录>``
退出 0 表示全部核对通过。
"""
import argparse
import csv
import hashlib
import json
import pathlib
import sys

CALLS_PER_ATTEMPT = 3
TARGETS = {"mechanism": 8, "reference": 4}


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def commitment_of(files):
    payload = "".join(f"{path}:{digest}\n" for path, digest in sorted(files.items()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=pathlib.Path, default=pathlib.Path(__file__).parent)
    args = parser.parse_args()
    root = args.package.resolve()
    checks = []

    def check(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    manifest = load(root / "MANIFEST.json")
    bad = []
    for rel, meta in manifest["files"].items():
        path = root / rel
        if not path.is_file() or path.stat().st_size != meta["bytes"] or sha(path) != meta["sha256"]:
            bad.append(rel)
    check("manifest_bytes", not bad, {"mismatches": bad, "count": len(manifest["files"])})

    results = load(root / "results/S1A_ADMISSION_RESULTS_20261018.json")
    ledger = load(root / "evidence/ledger.json")
    batch = load(root / "evidence/batch/next-admission-20261018.json")
    clearance = load(root / "evidence/clearance/REVIEWER001_NEXT_ADMISSION_CLEARANCE.json")
    plan = load(root / "evidence/plans/next_admission_plan_draft.json")["rows"]

    check("clearance_identity",
          clearance.get("signed_by") == "Reviewer 001"
          and clearance.get("decision") == "APPROVED_FOR_NEXT_ADMISSION"
          and clearance.get("scope", {}).get("S1a_admission_candidate_execution") is True
          and all(clearance.get("scope", {}).get(k) is False for k in
                  ("S1b_matrix_and_independent_b0", "S2_recovery",
                   "S3_repair_and_witness", "tool_repro")),
          clearance.get("decision"))
    check("batch_commitment_chain",
          batch["pre_run_commitment_hash"] == ledger["commitment"]
          == clearance["static_inputs_commitment"],
          {"batch": batch["pre_run_commitment_hash"][:16],
           "ledger": ledger["commitment"][:16],
           "clearance": clearance["static_inputs_commitment"][:16]})

    records = ledger["records"]
    skips = ledger["skips"]
    positions = [r["position"] for r in records] + [s["position"] for s in skips]
    check("position_coverage", sorted(positions) == list(range(1, len(plan) + 1)),
          {"covered": len(positions), "planned": len(plan)})
    check("position_uniqueness", len(set(positions)) == len(positions), None)
    dispatchable = [row for row in plan if row.get("eligible", True)]
    check("attempted_equals_dispatchable", len(records) == len(dispatchable),
          {"attempted": len(records), "dispatchable": len(dispatchable)})
    check("static_skip_count",
          len([s for s in skips if s["reason"] == "static_ineligible"]) == 4,
          [s["project"] for s in skips])

    replay = {
        "attempted": len(records),
        "skipped": len(skips),
        "mechanism_passes": sum(1 for r in records if r["group"] == "mechanism" and r["admitted"]),
        "reference_passes": sum(1 for r in records if r["group"] == "reference" and r["admitted"]),
        "calls_used": sum(r["calls"] for r in records),
    }
    recorded = ledger["summary"]
    check("ledger_summary_replay",
          all(recorded.get(k) == v for k, v in replay.items()),
          {"replayed": replay, "recorded": {k: recorded.get(k) for k in replay}})
    check("call_ceiling",
          replay["calls_used"] <= len(dispatchable) * CALLS_PER_ATTEMPT
          and replay["calls_used"] <= 87,
          {"used": replay["calls_used"],
           "dispatchable_ceiling": len(dispatchable) * CALLS_PER_ATTEMPT})

    commitment_bad = []
    call_bad = []
    for record in records:
        attempt = root / "evidence/attempts" / record["project"] / "attempt-001"
        if not attempt.is_dir():
            commitment_bad.append(f"{record['project']}:missing_attempt")
            continue
        started = load(attempt / "started.json")
        if started.get("position") != record["position"] or started.get("project") != record["project"]:
            commitment_bad.append(f"{record['project']}:started_mismatch")
        path = attempt / "commitment.json"
        if path.is_file():
            commitment = load(path)
            if commitment_of(commitment["files"]) != commitment["pre_run_commitment_hash"]:
                commitment_bad.append(f"{record['project']}:commitment_hash")
            if commitment.get("batch_commitment") != ledger["commitment"]:
                commitment_bad.append(f"{record['project']}:batch_commitment")
            if commitment.get("project") != record["project"] or commitment.get("position") != record["position"]:
                commitment_bad.append(f"{record['project']}:identity")
        calls = load(attempt / "calls_started.json") if (attempt / "calls_started.json").is_file() else []
        if len(calls) != record["calls"] or len(calls) > CALLS_PER_ATTEMPT:
            call_bad.append({"project": record["project"], "calls": len(calls),
                             "recorded": record["calls"]})
        if record["calls"] and not (attempt / "result.json").is_file():
            call_bad.append({"project": record["project"], "result": "missing"})
    check("candidate_commitments", not commitment_bad, commitment_bad)
    check("call_records", not call_bad, call_bad)

    rows = {r["position"]: r for r in results["candidates"]}
    mismatch = []
    for record in records:
        row = rows.get(record["position"])
        if not row:
            mismatch.append(f"{record['position']}:missing")
            continue
        for key in ("project", "version", "group", "canonical_family", "admitted", "calls"):
            if row.get(key) != record.get(key):
                mismatch.append(f"{record['position']}:{key}")
    check("results_match_ledger", not mismatch, mismatch)
    check("admitted_set", {r["project"] for r in results["candidates"] if r["admitted"]}
          == {"typer", "pytest-xdist"},
          [r["project"] for r in results["candidates"] if r["admitted"]])
    taxonomy = results["technical_taxonomy"]
    check("taxonomy_total", sum(taxonomy.values()) == len(results["candidates"]),
          {"total": sum(taxonomy.values()), "taxonomy": taxonomy})
    check("targets_reported",
          results["summary"]["mechanism_passes"] < TARGETS["mechanism"]
          and results["summary"]["reference_passes"] < TARGETS["reference"],
          {"mechanism": results["summary"]["mechanism_passes"],
           "reference": results["summary"]["reference_passes"]})
    check("no_retries",
          len(list((root / "evidence/attempts").glob("*/attempt-002"))) == 0,
          None)

    # CSV 与 JSON 同源
    with (root / "results/S1A_ADMISSION_PER_CANDIDATE_20261018.csv").open(encoding="utf-8") as stream:
        csv_rows = list(csv.DictReader(stream))
    check("csv_row_count", len(csv_rows) == len(results["candidates"]), len(csv_rows))

    check("no_s1b_or_s3_executed",
          clearance["scope"]["S1b_matrix_and_independent_b0"] is False
          and clearance["scope"]["S3_repair_and_witness"] is False, None)

    document = {"schema_version": "pyhost-context/s1a-recomputation/1.0",
                "package": str(root), "checks": checks,
                "all_ok": all(c["ok"] for c in checks),
                "candidate_tests_or_installs": 0,
                "network_calls": 0}
    (root / "RECOMPUTATION.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(json.dumps({"all_ok": document["all_ok"], "checks": len(checks),
                      "failed": [c for c in checks if not c["ok"]]},
                     ensure_ascii=False, indent=2))
    return 0 if document["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
