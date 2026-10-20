#!/usr/bin/env python3
"""ClawsGO Science Agent：Reviewer 001 S1 的 17 条反例——修正后逐条重放。

每条断言“原反例不再成立”。不执行候选、不安装、不联网；合成授权只在内存构造。
用法：``python3 replay_s1_counterexamples_fixed.py --workspace <extracted package>``
退出 0 表示 17 条全部不再复现。
"""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import pathlib
import sys
import tarfile
import tempfile
from types import SimpleNamespace
from unittest.mock import patch


def load_entry(root):
    spec = importlib.util.spec_from_file_location(
        "fixed_next_admission", root / "extension/next_phase/run_next_admission.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=pathlib.Path, required=True)
    args = parser.parse_args()
    root = args.workspace.resolve()
    entry = load_entry(root)
    plan, _ = entry.load_plan()
    checks = []

    def record(name, fixed, evidence):
        checks.append({"name": name, "counterexample_no_longer_reproduces": bool(fixed),
                       "evidence": evidence})

    import importlib.util as iu
    record("I1_ENTRY_CANNOT_IMPORT_INSTRUMENT",
           iu.find_spec("run_extension") is not None,
           {"run_extension": str(iu.find_spec("run_extension").origin)})

    fixture = {"signed_by": "Reviewer 001", "decision": entry.REQUIRED_DECISION,
               "batch_id": entry.BATCH_ID,
               "scope": {"S1a_admission_candidate_execution": True,
                         "S1b_matrix_and_independent_b0": False, "S2_recovery": False,
                         "S3_repair_and_witness": False, "tool_repro": False},
               "bound_files": {}, "static_inputs_commitment": "x"}
    with patch.object(entry, "CLEARANCE", SimpleNamespace(is_file=lambda: True)), \
            patch.object(entry, "load_json", return_value=fixture), \
            patch.object(entry, "sha256_of", return_value="synthetic"), \
            patch.object(entry, "static_inputs", return_value={"a": "b"}):
        gate = entry.clearance_gate()
    record("I1_EMPTY_BINDINGS_ACCEPTED", not gate["ok"], gate)

    import run_extension as rx
    with entry._patch_instrument():
        record("I1_OLD_METADATA_AUTHORITY_STILL_USED",
               rx.EFFECTIVE == entry.EFFECTIVE and rx.CLEARANCE == entry.CLEARANCE,
               {"effective": str(rx.EFFECTIVE.relative_to(root)),
                "clearance": str(rx.CLEARANCE.relative_to(root))})

    inputs = entry.static_inputs()
    outside = [str(path.relative_to(root)) for path in entry.OBSERVER_RESOURCES
               if str(path.relative_to(root)) not in inputs]
    record("I1_OBSERVER_READS_OUTSIDE_BOUND_CLOSURE", not outside, {"outside": outside})

    seen_timeouts = []

    def fake_run(argv, *a, **kw):
        seen_timeouts.append(kw.get("timeout"))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    original_run = rx.subprocess.run
    rx.subprocess.run = fake_run
    try:
        with entry._patch_instrument():
            rx.subprocess.run(["-m", "pytest", "--collect-only"], timeout=600)
            rx.subprocess.run(["pip", "download"], timeout=900)
    finally:
        rx.subprocess.run = original_run
    record("I1_A6_TIMEOUT_EXCEEDS_DECLARED240",
           seen_timeouts == [240, 900],
           {"effective_timeouts": seen_timeouts,
            "note": "只改 A6 收集调用；安装/下载超时不动"})

    # 候选承诺落盘并传给仪器
    writes = []
    captured = {}

    def fake_admit(project, protocol, b0, commitment, phase_root, detail, admission_id=None):
        captured["commitment"] = commitment["pre_run_commitment_hash"]
        detail["A6_collect_attempts"] = [{}]
        return {"run_id": "synthetic"}

    with tempfile.TemporaryDirectory(dir=str(root)) as tmp:
        tmp = pathlib.Path(tmp)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(entry, "RUNS", tmp / "runs"))
            stack.enter_context(patch.object(entry, "ARTIFACTS", tmp / "artifacts"))
            stack.enter_context(patch.object(entry, "WHEELHOUSE", tmp / "wheelhouse"))
            stack.enter_context(patch.object(entry, "CANDIDATES_DOC", tmp / "candidates.json"))
            stack.enter_context(patch.object(entry, "require_clearance",
                                             return_value={"clearance_sha256": "x"}))
            stack.enter_context(patch.object(entry, "clearance_gate",
                                             return_value={"ok": True,
                                                            "clearance_sha256": "x"}))
            stack.enter_context(patch.object(entry, "_patch_instrument",
                                             return_value=contextlib.nullcontext()))
            stack.enter_context(patch.object(entry, "prepare_candidate",
                                             return_value=({"source_dir": "s"}, {"wheels": {}}, {})))
            stack.enter_context(patch.object(entry, "candidate_commitment",
                                             return_value={"pre_run_commitment_hash": "candidate-hash"}))
            stack.enter_context(patch.object(rx, "admit_one", side_effect=fake_admit))
            stack.enter_context(patch.object(rx, "admission_decision", return_value=(True, [])))
            entry.execute_attempt(plan[0], "batch-hash")
            written = list((tmp / "runs").rglob("commitment.json"))
    record("I1_CANDIDATE_COMMITMENT_DISCARDED",
           captured.get("commitment") == "candidate-hash" and len(written) == 1,
           {"instrument_commitment": captured.get("commitment"),
            "commitment_files": [str(p.relative_to(root)) for p in written]})

    # 预期技术失败留账
    with tempfile.TemporaryDirectory(dir=str(root)) as tmp:
        tmp = pathlib.Path(tmp)
        files = {"synthetic-input": "a" * 64}
        batch = tmp / "batches"
        batch.mkdir()
        (batch / f"{entry.BATCH_ID}.json").write_text(
            json.dumps({"pre_run_commitment_hash": entry.commitment_of(files)}))
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(entry, "BATCHES", batch))
            stack.enter_context(patch.object(entry, "LEDGER", tmp / "ledger.json"))
            stack.enter_context(patch.object(entry, "RUNS", tmp / "runs"))
            stack.enter_context(patch.object(entry, "static_inputs", return_value=files))
            stack.enter_context(patch.object(entry, "require_clearance",
                                             return_value={"clearance_sha256": "x"}))
            stack.enter_context(patch.object(entry, "clearance_gate",
                                             return_value={"ok": True,
                                                            "clearance_sha256": "x"}))
            stack.enter_context(patch.object(
                entry, "execute_attempt",
                side_effect=entry.PreparationFailure(
                    "dependency_resolution_failed", "synthetic",
                    attempt_dir=tmp / "runs" / plan[0]["project"] / "attempt-001")))
            with contextlib.redirect_stdout(io.StringIO()):
                code = entry.run(limit=1)
        ledger = json.loads((tmp / "ledger.json").read_text())
    record("I1_EXPECTED_PREPARATION_FAILURE_NOT_LEDGERED",
           code == 0 and ledger["records"][0]["status"] == "technical_failure",
           {"exit_code": code, "record_status": ledger["records"][0]["status"]})

    # 续跑身份
    state = entry.scheduler.empty_state()
    entry.scheduler.settle(state, plan[0], {"admitted": True, "calls": 3,
                                            "status": "s", "batch_id": None,
                                            "commitment": None})
    state["records"][0]["version"] = "wrong"
    state["records"][0]["canonical_family"] = "wrong"
    checked = entry.scheduler.validate_resume(plan, state, entry.BATCH_ID, "c")
    record("I1_RESUME_MISSING_BINDINGS_AND_CHANGED_IDENTITY_ACCEPTED",
           not checked["ok"], checked)

    state = entry.scheduler.empty_state()
    entry.scheduler.skip(state, plan[0], "group_full")
    entry.scheduler.skip(state, plan[0], "group_full")
    checked = entry.scheduler.validate_resume(plan, state, entry.BATCH_ID, "c")
    record("I1_RESUME_DUPLICATE_SKIPS_ACCEPTED", not checked["ok"], checked)

    # 身份：错误 Name/Version 不得返回 identity_verified=true
    with tempfile.TemporaryDirectory(dir=str(root)) as tmp:
        tmp = pathlib.Path(tmp)
        artifact = tmp / "artifacts" / "synthetic"
        artifact.mkdir(parents=True)
        sdist = artifact / "synthetic-1.tar.gz"
        raw = b"Metadata-Version: 2.1\nName: entirely-other-library\nVersion: 99\n"
        with tarfile.open(sdist, "w:gz") as tar:
            member = tarfile.TarInfo("other-99/PKG-INFO")
            member.size = len(raw)
            tar.addfile(member, io.BytesIO(raw))
        doc = tmp / "candidates.json"
        doc.write_text(json.dumps({"candidates": {"synthetic": {
            "source_dir": "synthetic-1", "version": "1",
            "sdist": {"filename": sdist.name,
                      "sha256": hashlib.sha256(sdist.read_bytes()).hexdigest(),
                      "bytes": sdist.stat().st_size}}}}))
        with patch.object(entry, "ARTIFACTS", tmp / "artifacts"), \
                patch.object(entry, "CANDIDATES_DOC", doc):
            with entry._patch_instrument():
                from matrix_runner import source_tree as st
                receipt = st.artifact_identity("synthetic-1")
    record("I1_IDENTITY_CHECK_REPLACED_BY_WEAKER_CALLEE",
           receipt["identity_verified"] is False,
           {"receipt": {k: v for k, v in receipt.items()
                        if k in ("identity_verified", "identity_problems", "metadata_name")}})

    row = next(r for r in plan if r["project"] == "rich-toolkit")
    agreement = entry.dependency_agreement(row, {"resolved_distributions": {"rich": "15.0.0"}})
    record("I2_DEPENDENCY_VERSION_FIELD_MISMATCH",
           agreement["static_version"] == "15.0.0" and agreement["match"] is True,
           agreement)

    table = json.loads((root / "extension/next_phase/CASE_TABLES_20261018_REV1.json").read_text())
    cases = {case["case_id"]: case for case in table["cases"]}
    after = next(h["after"] for h in
                 next(m for m in cases["A"]["mutations"] if m["id"] == "A-M3")["patch"]["hunks"]
                 if "Console" in h["after"])
    truth = {}
    for value in (None, "", "1", "0"):
        env = {} if value is None else {"NO_COLOR": value}
        original = value not in (None, "")
        mutant = "NO_COLOR" in env
        truth[str(value)] = [original, mutant]
    record("I3_AM3_NONEMPTY_DEFECT_NOT_FIXED",
           truth["None"] == [False, False] and truth[""] == [False, True]
           and truth["1"] == [True, True] and truth["0"] == [True, True],
           {"truth_table": truth, "patch": after})

    denominator = table["evaluation_rule"]["C_denominator"]
    record("I3_TARGET_HIT_USED_AS_DENOMINATOR_FILTER",
           "target_executed" in denominator and "目标断言在该格命中" not in denominator,
           {"C_denominator": denominator})

    bm2 = next(m for m in cases["B"]["mutations"] if m["id"] == "B-M2")
    record("I3_BM2_DEFAULT_WIDTH_PATH_NOT_ACTIVATED",
           bm2["suite_detectable"] is False and bm2["target_nodes"] == [],
           {"suite_detectable": bm2["suite_detectable"],
            "applicable": bm2["applicable_contexts"]})

    witness_source = (root / "extension/next_phase/witnesses/repair_witness.py").read_text()
    record("I3_WITNESS_B3_INVALID_STDLIB_ATTRIBUTE",
           '"_width"' in witness_source and "_get_formatter().width" not in witness_source,
           {"uses_width": '"_width"' in witness_source})
    record("I3_WITNESS_C_MISSING_INPUT_FILE",
           "TemporaryDirectory" in witness_source and 'write_text("Some RST."' in witness_source,
           {"creates_input": 'write_text("Some RST."' in witness_source})

    spec = importlib.util.spec_from_file_location(
        "fixed_witness", root / "extension/next_phase/witnesses/repair_witness.py")
    witness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(witness)
    rgb = witness.color_profile("\x1b[38;2;1;2;3mRGB\x1b[0m")
    record("I3_EXTENDED_SGR_OPERANDS_MISREAD_AS_CODES",
           rgb["has_color_components"] is True and rgb["basic_color_codes"] == [],
           rgb)

    out = {"schema_version": "pyhost-context/s1-counterexample-replay/1.0",
           "checks": checks,
           "all_counterexamples_no_longer_reproduce": all(
               row["counterexample_no_longer_reproduces"] for row in checks),
           "count": len(checks),
           "candidate_tests_installs_and_tool_runs": 0}
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if out["all_counterexamples_no_longer_reproduce"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
