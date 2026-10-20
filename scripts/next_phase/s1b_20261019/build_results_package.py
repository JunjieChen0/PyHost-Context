"""ClawsGO Science Agent：打包 S1b 450 轮结果（单个 ZIP）。"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
STATE = HERE.parent / "state" / "s1b_20261019"
WS = HERE.parents[3]
OUT = WS / "deliverables" / "PyHost_S1b_Results_20261019.zip"
STAGE = pathlib.Path("/tmp/s1b_results_stage")
FIXED = (2026, 10, 19, 0, 0, 0)


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def main():
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    for name in ("S1B_RESULTS_README.md", "S1B_RESULTS_REPORT_20261019.md",
                 "S1B_REVIEWER_CORRECTIONS_ACCEPTED_20261019.md",
                 "analyze_s1b.py", "recompute_results.py"):
        copy(HERE / name, STAGE / ("README.md" if name == "S1B_RESULTS_README.md" else name))
    copy(HERE / "REVIEWER001_S1B_CLEARANCE_20261019.json",
         STAGE / "REVIEWER001_S1B_CLEARANCE_20261019.json")
    copy(HERE / "REVIEWER001_S1B_SCOPE_20261019.json",
         STAGE / "REVIEWER001_S1B_SCOPE_20261019.json")
    copy(HERE / "S1B_PRE_RUN_COMMITMENT_20261019.json",
         STAGE / "S1B_PRE_RUN_COMMITMENT_20261019.json")
    copy(HERE / "S1B_PREREGISTERED_PREDICTIONS_20261019.json",
         STAGE / "S1B_PREREGISTERED_PREDICTIONS_20261019.json")
    copy(HERE / "S1B_SCHEDULE_20261019.json", STAGE / "S1B_SCHEDULE_20261019.json")
    copy(HERE / "S1B_ROSTER_20261019.json", STAGE / "S1B_ROSTER_20261019.json")
    copy(HERE / "S1B_TOOLCHAINS_20261019.json", STAGE / "S1B_TOOLCHAINS_20261019.json")
    for name in ("ledger.json", "execution.lock", "S1B_ANALYSIS_20261019.json"):
        source = STATE / name
        if source.is_file():
            copy(source, STAGE / "state" / name)
    copied = 0
    for path in sorted((STATE / "results").rglob("*")):
        if path.is_file():
            copy(path, STAGE / "state" / "results" / path.relative_to(STATE / "results"))
            copied += 1
    members = []
    for path in sorted(STAGE.rglob("*")):
        if path.is_file():
            members.append({"path": str(path.relative_to(STAGE)),
                            "sha256": sha(path), "bytes": path.stat().st_size})
    (STAGE / "PACKAGE_MANIFEST.json").write_text(
        json.dumps({"schema_version": "pyhost-context/S1b-results-package/1.0",
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
                      "members": len(members), "result_files": copied},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
