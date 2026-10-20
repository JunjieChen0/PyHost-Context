"""ClawsGO Science Agent：打包 D 档准备交付（单个 ZIP）。"""
import hashlib
import json
import pathlib
import shutil
import zipfile

from packaging.utils import canonicalize_name

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
STATE = NEXT / "state" / "d_toolchain"
ROOT = NEXT.parents[2]
STAGE = pathlib.Path("/tmp/d_prep_stage")
OUT = ROOT / "deliverables" / "PyHost_NextExperiment_D_Preparation_20261018.zip"
CLEARANCE = pathlib.Path("/tmp/drev/REVIEWER001_D_TOOLCHAIN_ACQUISITION_CLEARANCE_20261018.json")
FIXED = (2026, 10, 18, 0, 0, 0)


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def main():
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    copy(NEXT / "D_README_D_PREPARATION.md", STAGE / "README.md")
    for name in ["D_PREPARATION_REPORT_20261018.md",
                 "D_PROJECT_DIFFERENCES_20261018.md",
                 "D_PROJECT_DIFFERENCES_20261018.json",
                 "D_PRE_RUN_COMMITMENT_20261018.md",
                 "D_PRE_RUN_COMMITMENT_20261018.json",
                 "D_R0_WITNESSES_20261018.json"]:
        copy(NEXT / name, STAGE / name)
    locks = json.loads((STATE / "D_RESOLUTION_LOCKS_WITH_CANDIDATE.json").read_text())
    copy(STATE / "D_RESOLUTION_LOCKS_WITH_CANDIDATE.json",
         STAGE / "locks" / "D_RESOLUTION_LOCKS_WITH_CANDIDATE.json")
    for project in locks["projects"]:
        if project.get("status") == "locked":
            (STAGE / "locks" / f"{project['project']}.json").write_text(
                json.dumps(project, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
    for name in ["D_WHEEL_MANIFEST.json", "D_OFFICIAL_METADATA_INDEX.json",
                 "D_PLUGIN_MAP.json", "D_LOCK_VALIDATION.json",
                 "D_LOCAL_WHEEL_INVENTORY.json"]:
        copy(STATE / name, STAGE / "artifacts" / name)
    index = json.loads((STATE / "D_OFFICIAL_METADATA_INDEX.json").read_text())
    for entry in index["entries"]:
        name = canonicalize_name(entry["name"])
        source = STATE / "official" / name / f"{entry['version']}.json"
        target = STAGE / "artifacts" / "official" / name / f"{entry['version']}.json"
        if not target.is_file():
            copy(source, target)
        entry["metadata_path"] = str(target.relative_to(STAGE))
    (STAGE / "artifacts" / "D_OFFICIAL_METADATA_INDEX.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    witness_root = STATE / "r0_witness"
    if witness_root.is_dir():
        for path in sorted(witness_root.rglob("*")):
            if path.is_file():
                copy(path, STAGE / "artifacts" / "r0_witness" /
                     path.relative_to(witness_root))
    copy(HERE / "D_ACQUISITION_ROOTS.json", STAGE / "inputs" / "D_ACQUISITION_ROOTS.json")
    if CLEARANCE.is_file():
        copy(CLEARANCE, STAGE / "inputs" / CLEARANCE.name)
        (STAGE / "inputs" / "REVIEWER001_CLEARANCE.sha256").write_text(
            f"{sha(CLEARANCE)}  {CLEARANCE.name}\n", encoding="utf-8")
    for name in ["resolve_with_pip.py", "fetch_official_metadata.py", "finalize_locks.py",
                 "acquire_d_wheels.py", "build_plugin_map.py", "stage_and_validate.py",
                 "build_r0_witnesses.py", "build_d_differences.py",
                 "build_d_commitment.py", "build_d_report.py",
                 "d_adapter.py", "test_d_adapter.py"]:
        copy(HERE / name, STAGE / "tools" / name)
    for name in ["run_extension.py", "run_matrix.py"]:
        copy(HERE / "stubs" / name, STAGE / "tools" / "stubs" / name)
    copy(HERE / "recompute_d_preparation.py", STAGE / "recompute_d_preparation.py")
    members = []
    for path in sorted(STAGE.rglob("*")):
        if path.is_file():
            members.append({"path": str(path.relative_to(STAGE)),
                            "sha256": sha(path), "bytes": path.stat().st_size})
    manifest = {"schema_version": "pyhost-context/d-package-manifest/1.0",
                "members": members}
    (STAGE / "PACKAGE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(STAGE.rglob("*")):
            if not path.is_file():
                continue
            arcname = str(path.relative_to(STAGE))
            info = zipfile.ZipInfo(arcname, date_time=FIXED)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    print(json.dumps({"zip": str(OUT), "sha256": sha(OUT),
                      "bytes": OUT.stat().st_size, "members": len(members)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
