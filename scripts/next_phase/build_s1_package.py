"""ClawsGO Science Agent：把 S1 补件打成单个可离线复算的 ZIP。"""
import hashlib
import json
import pathlib
import shutil
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
EXT = HERE.parent
ROOT = EXT.parent
OUTDIR = ROOT / "deliverables" / "PyHost_NextExperiment_S1_Inputs_Corrected_20261018"
ZIP = ROOT / "deliverables" / "PyHost_NextExperiment_S1_Inputs_Corrected_20261018.zip"

FILES = [
    "README.md",
    "extension/next_phase/PRE_REGISTRATION_20261018.json",
    "extension/next_phase/POOLS_20261018.json",
    "extension/next_phase/SCREEN_EVIDENCE_20261018.json",
    "extension/next_phase/STATIC_WITNESSES_20261018.json",
    "extension/next_phase/CASE_TABLES_20261018_REV1.json",
    "extension/next_phase/DERIVED_INDEX_20261018.json",
    "extension/next_phase/INPUT_CLOSURE_NEXT.json",
    "extension/next_phase/FACTUAL_ERRATA_AUTHOR_20261018.json",
    "extension/next_phase/S1_REVIEW_CORRECTIONS_20261018.json",
    "extension/next_phase/BUDGET_SCHEDULE_BINDINGS_20261018_REV1.json",
    "extension/next_phase/scan_candidate.py",
    "extension/next_phase/build_pools.py",
    "extension/next_phase/build_static_witnesses.py",
    "extension/next_phase/build_case_tables.py",
    "extension/next_phase/materialize_derived.py",
    "extension/next_phase/build_budget_rev1.py",
    "extension/next_phase/verify_s1_inputs.py",
    "extension/next_phase/run_next_admission.py",
    "extension/next_phase/closure.py",
    "extension/next_phase/test_next_admission.py",
    "extension/next_phase/test_reviewer001_s1_counterexamples.py",
    "extension/next_phase/replay_s1_counterexamples_fixed.py",
    "extension/next_phase/admission/__init__.py",
    "extension/next_phase/admission/scheduler.py",
    "extension/next_phase/admission/synthetic.py",
    "extension/next_phase/witnesses/repair_witness.py",
    "extension/next_phase/state/plans/next_admission_plan_draft.json",
    "extension/next_phase/state/plans/next_admission_clearance_request.json",
    "extension/next_phase/state/proofs/SYNTHETIC_CONSUMPTION_PROOF.json",
    "extension/frame/frame_20261015.json",
    "extension/freeze/final_sample_20261015.json",
    "extension/effective/EFFECTIVE_PROTOCOL_v1.1_Reviewer001.json",
    "extension/effective/EFFECTIVE_PROTOCOL_v1.2_Reviewer001.json",
    "matrix/protocol/matrix_protocol_v0.4_reviewed.json",
    "admission_freeze/b0_environment.json",
    "tooling/admission_runner/integration/jsonl_producer.py",
    "matrix/tooling/matrix_runner/netwatch_sitecustomize.py",
    "extension/tooling/run_extension.py",
    "extension/tooling/m2_sampling_rules.py",
    "matrix/tooling/run_matrix.py",
    "tooling/artifact_identity.py",
    "tooling/restore_source_tree.py",
    "tooling/admission_runner/env_state.py",
    "tooling/admission_runner/pytest_jsonl.py",
    "tooling/admission_runner/timeout_ledger.py",
    "admission_artifacts/rich-argparse/rich_argparse-1.8.0.tar.gz",
    "admission_artifacts/rich-rst/rich_rst-2.2.0.tar.gz",
    "extension/next_phase/cache/sdists/rich-15.0.0.tar.gz",
    "extension/next_phase/cache/sdists/structlog-26.1.0.tar.gz",
    "extension/next_phase/cache/sdists/pylint-4.1.3.tar.gz",
]
for name in ("launch.py", "slots.py", "schedule.py", "source_tree.py", "summary.py",
             "validity.py", "screening.py", "commitment.py", "signing.py",
             "observer_install.py", "load_probe.py", "__init__.py"):
    FILES.append(f"matrix/tooling/matrix_runner/{name}")


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def main():
    if OUTDIR.exists():
        shutil.rmtree(OUTDIR)
    OUTDIR.mkdir(parents=True)
    copied = []
    for rel in FILES:
        source = HERE / "S1_README.md" if rel == "README.md" else ROOT / rel
        if not source.is_file():
            raise SystemExit(f"missing:{rel}")
        target = OUTDIR / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append(rel)
    # 派生树（变异体）与逐树清单
    index = json.loads((HERE / "DERIVED_INDEX_20261018.json").read_text(encoding="utf-8"))
    for entry in index["derived"]:
        tree = ROOT / entry["tree"]
        for path in sorted(tree.rglob("*")):
            if path.is_file():
                rel = path.relative_to(ROOT)
                target = OUTDIR / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                copied.append(str(rel))
        manifest = tree.parent / f"{tree.name}.manifest.json"
        rel = manifest.relative_to(ROOT)
        target = OUTDIR / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(manifest, target)
        copied.append(str(rel))
    copied = sorted(set(copied))
    manifest = {"schema_version": "pyhost-context/s1-inputs-manifest/1.0",
                "files": {rel: {"bytes": (OUTDIR / rel).stat().st_size,
                                "sha256": sha(OUTDIR / rel)} for rel in copied},
                "count": len(copied)}
    (OUTDIR / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    if ZIP.exists():
        ZIP.unlink()
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(OUTDIR.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(OUTDIR).as_posix())
    print(json.dumps({"zip": str(ZIP.relative_to(ROOT)), "members": len(copied) + 1,
                      "bytes": ZIP.stat().st_size, "sha256": sha(ZIP)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
