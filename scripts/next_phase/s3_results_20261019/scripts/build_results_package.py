"""ClawsGO Science Agent：打包 S3 执行结果与原始证据为单个 ZIP。

只读状态目录与签署输入；不执行候选、不修改任何签署件。
证据排除每轮物化源码（src/）、home/、tmp/、bin/；账本记录其树哈希。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
NEXT = ROOT.parent
WS = ROOT.parents[3]
STATE = NEXT / "state" / "s3_reviewer001_20261019"
INPUTS = NEXT / "s3_inputs_reviewer001_20261019"
REVIEW = NEXT / "reviewer001_s3_execution_20261019"
STAGE = pathlib.Path("/tmp/s3_results_stage")
OUT = WS / "deliverables" / "PyHost_S3_Execution_Results_20261019.zip"
FIXED = (2026, 10, 19, 0, 0, 0)
RUN_FILES = ("report.jsonl", "netlog.jsonl", "stdout.txt", "stderr.txt",
             "boot.json", "round_meta.json", "tree_delta.json")


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_tree(source: pathlib.Path, target: pathlib.Path,
              ignore=("__pycache__", ".pytest_cache")) -> None:
    for path in sorted(source.rglob("*")):
        if not path.is_file() or any(part in ignore for part in path.parts):
            continue
        out = target / path.relative_to(source)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, out)


def main() -> int:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    for name in ("README.md", "S3_EXECUTION_REPORT_20261019.md"):
        shutil.copyfile(ROOT / name, STAGE / name)
    copy_tree(HERE, STAGE / "scripts")
    copy_tree(ROOT / "results", STAGE / "results",
              ignore=("__pycache__", "recomputed"))
    (STAGE / "provenance").mkdir()
    for name in ("REVIEWER001_S3_CLEARANCE_20261019.json",
                 "INSTALL_RECEIPT_20261019T110350586457Z.json"):
        shutil.copyfile(REVIEW / name, STAGE / "provenance" / name)
    for name in ("reviewer_verify_review.json", "reviewer_recompute_review.json"):
        shutil.copyfile(ROOT / "provenance" / name, STAGE / "provenance" / name)
    for name in ("REVIEWER001_RULING.md", "REVIEWER001_SIGNOFF.json",
                 "FINDINGS.json", "PATCH_RECORD.json"):
        shutil.copyfile(pathlib.Path("/tmp/s3rev3") / name,
                        STAGE / "provenance" / ("reviewer001_" + name))

    evidence = STAGE / "evidence"
    evidence.mkdir()
    shutil.copyfile(STATE / "ledger.json", evidence / "ledger.json")
    shutil.copyfile(STATE / "summary.json", evidence / "execution_summary.json")
    runs = evidence / "runs"
    for run_id, record in sorted(json.loads(
            (STATE / "ledger.json").read_text(encoding="utf-8"))["records"].items()):
        source = pathlib.Path(record["run_dir"])
        target = runs / run_id / source.name
        target.mkdir(parents=True)
        for name in RUN_FILES:
            path = source / name
            if path.is_file():
                shutil.copyfile(path, target / name)

    copy_tree(INPUTS, STAGE / "inputs_snapshot")

    members = []
    for path in sorted(STAGE.rglob("*")):
        if path.is_file() and path.name != "PACKAGE_MANIFEST.json":
            members.append({"path": str(path.relative_to(STAGE)),
                            "sha256": sha(path), "bytes": path.stat().st_size})
    manifest = {
        "schema_version": "pyhost-context/S3-execution-results/1.0",
        "batch_id": "next-S3-20261019",
        "execution_authorized_by": "Reviewer 001 REVIEWER001_S3_CLEARANCE_20261019",
        "counts": {"calls": 240, "suite": 192, "witness": 48,
                   "completed": 220, "failed": 20},
        "rows_sha256": "0d52fb0345e541f01e57d9d15bf7d861f6a9fbf8448e26ef79b5d3b39fdd172e",
        "commitment_sha256": "b35e6ad3fd0ac3ae2da1d84e26f45970afc4d6ef253554b7181e82ce0d49b840",
        "excluded_from_evidence": ["runs/*/attempt-*/src", "runs/*/attempt-*/home",
                                   "runs/*/attempt-*/tmp", "runs/*/attempt-*/bin"],
        "members": members,
    }
    (STAGE / "PACKAGE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
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
