"""ClawsGO Science Agent：把 S3 输入与接受记录打包为单个 ZIP。

只复制本目录（及复核件副本）；不执行候选、不修改任何输入。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
WS = HERE.parents[3]
REVIEW = pathlib.Path("/tmp/s3rev2")
STAGE = pathlib.Path("/tmp/s3_package_stage_rev2")
OUT = WS / "deliverables" / "PyHost_S3_Inputs_REV2_20261019.zip"
FIXED = (2026, 10, 19, 0, 0, 0)
REVIEW_FILES = {
    "README.md": "review/README.md",
    "MANIFEST.json": "review/MANIFEST.json",
    "REVIEWER001_RULING.md": "review/REVIEWER001_RULING.md",
    "REVIEWER001_SIGNOFF.json": "review/REVIEWER001_SIGNOFF.json",
    "FINDINGS.json": "review/FINDINGS.json",
    "replay_rev1_remaining.py": "review/replay_rev1_remaining.py",
    "verify_review.py": "review/verify_review.py",
    "observations/author_checks.json": "review/observations/author_checks.json",
    "observations/delta_from_original.json":
        "review/observations/delta_from_original.json",
    "observations/remaining_counterexamples.json":
        "review/observations/remaining_counterexamples.json",
    "observations/native_collection_error.json":
        "review/observations/native_collection_error.json",
    "observations/native_collection_error.jsonl":
        "review/observations/native_collection_error.jsonl",
    "observations/native_collection_error.stdout.txt":
        "review/observations/native_collection_error.stdout.txt",
    "observations/native_collection_error.stderr.txt":
        "review/observations/native_collection_error.stderr.txt",
    "observations/native_parameter_order.json":
        "review/observations/native_parameter_order.json",
    "observations/native_parameter_order.stdout.txt":
        "review/observations/native_parameter_order.stdout.txt",
    "observations/native_parameter_order.stderr.txt":
        "review/observations/native_parameter_order.stderr.txt",
    "observations/original_counterexample_replay.json":
        "review/observations/original_counterexample_replay.json",
    "observations/static_inputs.json": "review/observations/static_inputs.json",
    "reference/b0_environment.json": "review/reference/b0_environment.json",
    "reference/test_param_order.py": "review/reference/test_param_order.py",
    "reference/verify_static_inputs.py": "review/reference/verify_static_inputs.py",
    "reference/reproduce_original_gaps.py":
        "review/reference/reproduce_original_gaps.py",
    "reference/CASE_TABLES_20261018_REV1.json":
        "review/reference/CASE_TABLES_20261018_REV1.json",
}


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def main() -> int:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    for path in sorted(HERE.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or ".pytest_cache" in path.parts or path.suffix == ".pyc":
            continue
        target = STAGE / path.relative_to(HERE)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    for source, target in REVIEW_FILES.items():
        path = REVIEW / source
        if path.is_file():
            out = STAGE / target
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, out)
    members = []
    for path in sorted(STAGE.rglob("*")):
        if path.is_file() and path.name != "PACKAGE_MANIFEST.json":
            members.append({"path": str(path.relative_to(STAGE)),
                            "sha256": sha(path), "bytes": path.stat().st_size})
    manifest = {"schema_version": "pyhost-context/S3-inputs-package/1.0",
                "batch_id": "next-S3-20261019",
                "execution_authorized": False,
                "members": members}
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
                      "bytes": OUT.stat().st_size,
                      "members": len(members)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
