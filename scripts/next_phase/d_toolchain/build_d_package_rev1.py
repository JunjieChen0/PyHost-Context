"""ClawsGO Science Agent：打包 D 档 REV1 修正交付（单个 ZIP）。"""
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
STAGE = pathlib.Path("/tmp/d_prep_rev1_stage")
OUT = ROOT / "deliverables" / "PyHost_NextExperiment_D_Preparation_REV1_20261018.zip"
RULING = pathlib.Path("/tmp/dprep_rev/REVIEWER001_D_PREPARATION_RULING_20261018.json")
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
    copy(NEXT / "D_README_D_PREPARATION_REV1.md", STAGE / "README.md")
    for name in ["D_PREPARATION_REPORT_REV1_20261018.md",
                 "D_PROJECT_DIFFERENCES_REV1_20261018.md",
                 "D_PROJECT_DIFFERENCES_REV1_20261018.json",
                 "D_PRE_RUN_COMMITMENT_REV1_20261018.md",
                 "D_PRE_RUN_COMMITMENT_REV1_20261018.json",
                 "D_ERRATA_20261018.md",
                 "D_STRUCTLOG_CONFIG_WITNESS_20261018.json",
                 "D_PLUGIN_DEFAULTS_20261018.json",
                 "D_TEST_EVIDENCE_20261018.json",
                 "D_R0_WITNESSES_20261018.json"]:
        copy(NEXT / name, STAGE / name)
    locks = json.loads((STATE / "D_RESOLUTION_LOCKS_REV1.json").read_text())
    copy(STATE / "D_RESOLUTION_LOCKS_REV1.json",
         STAGE / "locks" / "D_RESOLUTION_LOCKS_WITH_CANDIDATE.json")
    for project in locks["projects"]:
        if project.get("status") == "locked":
            (STAGE / "locks" / f"{project['project']}.json").write_text(
                json.dumps(project, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
    copy(NEXT / "locks_rev1" / "structlog_R0.json",
         STAGE / "locks" / "structlog_R0.json")
    for name in ["D_WHEEL_MANIFEST_REV1.json", "D_OFFICIAL_METADATA_INDEX_REV1.json",
                 "D_PLUGIN_MAP_REV1.json", "D_LOCK_VALIDATION_REV1.json",
                 "D_LOCAL_WHEEL_INVENTORY.json"]:
        target_name = {"D_WHEEL_MANIFEST_REV1.json": "D_WHEEL_MANIFEST.json",
                       "D_OFFICIAL_METADATA_INDEX_REV1.json": "D_OFFICIAL_METADATA_INDEX.json",
                       "D_PLUGIN_MAP_REV1.json": "D_PLUGIN_MAP.json",
                       "D_LOCK_VALIDATION_REV1.json": "D_LOCK_VALIDATION.json",
                       "D_LOCAL_WHEEL_INVENTORY.json": "D_LOCAL_WHEEL_INVENTORY.json"}[name]
        copy(STATE / name, STAGE / "artifacts" / target_name)
    index = json.loads((STATE / "D_OFFICIAL_METADATA_INDEX_REV1.json").read_text())
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
    corrections = {
        "schema_version": "pyhost-context/d-rev1-corrections/1.0",
        "structlog": {"r0_preserved": "locks/structlog_R0.json",
                      "r1": "locks/structlog.json",
                      "derived_constraint": "pytest-asyncio>=0.24",
                      "witness": "D_STRUCTLOG_CONFIG_WITNESS_20261018.json"},
        "cryptography": {
            "replaced": "cryptography-50.0.2-cp311-abi3-manylinux_2_34_x86_64.whl",
            "replacement": "cryptography-50.0.2-cp311-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
            "sha256": "630ebfea3bf689d075f82316324ff7433dc447fe6bc1bfc76524b74b4a9567d2",
            "bytes": 4719841,
            "metadata_sha256": "047cfc71226409de4318b360aef15c63a2adf609731e3dcf7fe932f14ea042ee",
            "projects": ["twine", "hatch"],
        },
    }
    (STAGE / "corrections").mkdir(parents=True, exist_ok=True)
    (STAGE / "corrections" / "REV1_CORRECTIONS.json").write_text(
        json.dumps(corrections, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    copy(HERE / "D_ACQUISITION_ROOTS.json", STAGE / "inputs" / "D_ACQUISITION_ROOTS.json")
    if RULING.is_file():
        copy(RULING, STAGE / "inputs" / RULING.name)
        (STAGE / "inputs" / "REVIEWER001_RULING.sha256").write_text(
            f"{sha(RULING)}  {RULING.name}\n", encoding="utf-8")
    tools = ["resolve_with_pip.py", "resolve_structlog_r1.py", "build_rev1_locks.py",
             "fetch_official_metadata.py", "finalize_locks.py", "acquire_d_wheels.py",
             "build_plugin_map.py", "stage_and_validate.py", "build_r0_witnesses.py",
             "build_d_differences.py", "build_d_commitment.py", "build_d_report.py",
             "d_adapter.py", "run_d_admission.py", "test_d_adapter.py", "test_d_entry.py"]
    for name in tools:
        copy(HERE / name, STAGE / "tools" / name)
    copy(HERE / "README_INSTRUMENT.md", STAGE / "tools" / "README_INSTRUMENT.md")
    for name in ["run_extension.py", "run_matrix.py"]:
        copy(HERE / "stubs" / name, STAGE / "tools" / "stubs" / name)
    copy(HERE / "recompute_d_preparation.py", STAGE / "recompute_d_preparation.py")
    members = []
    for path in sorted(STAGE.rglob("*")):
        if path.is_file():
            members.append({"path": str(path.relative_to(STAGE)),
                            "sha256": sha(path), "bytes": path.stat().st_size})
    (STAGE / "PACKAGE_MANIFEST.json").write_text(
        json.dumps({"schema_version": "pyhost-context/d-package-manifest/1.0",
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
