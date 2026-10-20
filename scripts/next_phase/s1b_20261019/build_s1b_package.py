"""ClawsGO Science Agent：打包 S1b 准备交付（单个 ZIP）。"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
WS = PROJECT.parent
STAGE = pathlib.Path("/tmp/s1b_stage")
OUT = WS / "deliverables" / "PyHost_S1b_Preparation_20261019.zip"
FIXED = (2026, 10, 19, 0, 0, 0)
INPUT_FILES = ["DECLARED_PYTEST_METADATA_AMENDMENT.json",
               "D_QUALIFIED_AND_S1B_PREPARATION.json",
               "REVIEWER001_D_RESULTS_CLOSURE_20261019.json",
               "pytest_runner_transition.py",
               "REVIEWER001_REVIEW_20261019.md"]
LOCK_FILES = {
    "locks/D_RESOLUTION_LOCKS_REV1.json":
        PROJECT / "extension/next_phase/state/d_toolchain/D_RESOLUTION_LOCKS_REV1.json",
    "locks/D_PLUGIN_MAP_REV1.json":
        PROJECT / "extension/next_phase/state/d_toolchain/D_PLUGIN_MAP_REV1.json",
    "locks/candidates_next.json":
        PROJECT / "extension/next_phase/state/candidates_next.json",
    "locks/S1A_ADMISSION_RESULTS_20261018.json":
        PROJECT / "extension/next_phase/S1A_ADMISSION_RESULTS_20261018.json",
}


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def run_tests():
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "test_s1b_adapter.py"],
                          cwd=str(HERE), capture_output=True, text=True, timeout=300)
    match = re.search(r"(\d+) passed", proc.stdout)
    return {"file": "test_s1b_adapter.py", "returncode": proc.returncode,
            "passed": int(match.group(1)) if match else 0,
            "summary": proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "",
            "candidate_runs": 0}


def main():
    roster = read(HERE / "S1B_ROSTER_20261019.json")
    toolchains = read(HERE / "S1B_TOOLCHAINS_20261019.json")
    schedule = read(HERE / "S1B_SCHEDULE_20261019.json")
    commitment = read(HERE / "S1B_PRE_RUN_COMMITMENT_20261019.json")
    tests = run_tests()
    evidence = {
        "schema_version": "pyhost-context/S1b-preparation-evidence/1.0",
        "batch_id": roster["batch_id"],
        "generated_at_utc": "2026-10-19T00:00:00Z",
        "no_candidate_execution": True,
        "tests": tests,
        "roster_sha256": sha(HERE / "S1B_ROSTER_20261019.json"),
        "toolchains_sha256": sha(HERE / "S1B_TOOLCHAINS_20261019.json"),
        "schedule_sha256": schedule["schedule_sha256"],
        "pre_run_commitment_hash": commitment["pre_run_commitment_hash"],
        "commitment_files": len(commitment["files"]),
        "projects": [
            {"position": row["position"], "project": row["project"],
             "version": row["version"], "group": row["group"],
             "profile": row["profile"], "pytest": row["pytest_version"],
             "install_files": len(toolchains["projects"][row["project"]]["install_files"]),
             "expected_distributions":
                 len(toolchains["projects"][row["project"]]["expected_distributions"]),
             "plugin_loads": toolchains["projects"][row["project"]]["plugin_loads"],
             "sdist_sha256": toolchains["projects"][row["project"]]["source"]["sha256"]}
            for row in roster["projects"]],
        "schedule": {"main_runs": 225, "control_runs": 225, "total_runs": 450,
                     "seed": schedule["seed"],
                     "first_five": [e["run_id"] for e in schedule["entries"][:5]]},
        "amended_rule": toolchains["amended_transition_rule"],
        "sealed_before_execution": commitment.get("sealed_before_execution"),
        "supersedes_commitment": (commitment.get("supersedes") or {}).get("pre_run_commitment_hash"),
    }
    (HERE / "S1B_EVIDENCE_20261019.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")

    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    for name in ("README.md", "S1B_PREPARATION_REPORT_20261019.md",
                 "S1B_ROSTER_20261019.json", "S1B_TOOLCHAINS_20261019.json",
                 "S1B_SCHEDULE_20261019.json", "S1B_PRE_RUN_COMMITMENT_20261019.json",
                 "S1B_EVIDENCE_20261019.json", "s1b_adapter.py",
                 "S1B_PREREGISTERED_PREDICTIONS_20261019.json",
                 "S1B_REPAIR_MUTATION_ADJUDICATION_20261019.json",
                 "test_s1b_adapter.py", "build_s1b_inputs.py",
                 "recompute_s1b_preparation.py"):
        copy(HERE / name, STAGE / name)
    for name in INPUT_FILES:
        target = "inputs/REVIEW.md" if name == "REVIEWER001_REVIEW_20261019.md" \
            else f"inputs/{name}"
        copy(HERE / "inputs" / name, STAGE / target)
    for target, source in LOCK_FILES.items():
        copy(source, STAGE / target)

    members = []
    for path in sorted(STAGE.rglob("*")):
        if path.is_file():
            members.append({"path": str(path.relative_to(STAGE)),
                            "sha256": sha(path), "bytes": path.stat().st_size})
    (STAGE / "PACKAGE_MANIFEST.json").write_text(
        json.dumps({"schema_version": "pyhost-context/S1b-preparation-package/1.0",
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
    print(json.dumps({"zip": str(OUT), "sha256": sha(OUT), "bytes": OUT.stat().st_size,
                      "members": len(members), "tests": tests}, ensure_ascii=False,
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
