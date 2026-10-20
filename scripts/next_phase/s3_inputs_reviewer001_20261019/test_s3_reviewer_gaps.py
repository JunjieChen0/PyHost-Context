"""ClawsGO Science Agent：Reviewer 001 R1—R5 反例的拒绝回归。

每个用例对应复核包 ``synthetic_counterexamples.json`` 的一个 id；断言的是
“原反例不再成立”，不是“缺口仍可复现”。正控制（PC_*）保持成立。
全部合成：不导入、不执行真实候选。
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import pathlib
import sys
from unittest.mock import patch

import pytest
from reviewer_test_support import qualify_comparison

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import s3_adapter as adapter  # noqa: E402
import s3_adjudicate as adjudicate  # noqa: E402
import witness_probes as probes  # noqa: E402

ROWS = json.loads((HERE / "S3_ROWS_20261019.json").read_text(encoding="utf-8"))
SPEC = json.loads((HERE / "inputs/witness_spec.json").read_text(encoding="utf-8"))["spec"]


def row(case="A", context="A-C1", product="M1", suite="R2_local_isolation"):
    return next(item for item in ROWS["suite_rows"]
                if (item["case"], item["context"], item["product_variant"],
                    item["suite_variant"]) == (case, context, product, suite))


def signature_longrepr(item):
    location = item["expected"]["failure_signature"]["assertion_locations"][0]
    if location["lines"]:
        return f"{location['file']}:{location['lines'][0]}: AssertionError: x"
    return f"{location['file']}: AssertionError in {location['function']}"


def result(nodes=None, rc=None, longrepr="AssertionError: x", session=True,
           malformed=0, status="completed"):
    nodes = nodes or {}
    if rc is None:
        rc = 1 if any(value == "failed" for value in nodes.values()) else 0
    return {"status": status, "returncode": rc, "timed_out": False,
            "tree_after_ok": True, "process_group_cleanup": True,
            "malformed_jsonl_lines": malformed, "collection_finished": True,
            "collected": list(nodes),
            "session": {"$report_type": "SessionFinished", "exitstatus": rc} if session else None,
            "node_outcomes": {node: {"phase_details": {
                "setup": {"outcome": "passed"},
                "call": {"outcome": outcome,
                         "longrepr": longrepr if outcome == "failed" else None},
                "teardown": {"outcome": "passed"}},
                "longrepr": longrepr if outcome == "failed" else None}
                for node, outcome in nodes.items()}}


# ------------------------------------------------------------------ R1

def test_j1_unrelated_exception_rejected():
    item = row()
    target = item["expected"]["target_nodes"][0]
    cell = adjudicate.suite_cell(item, result({target: "failed"},
                                              longrepr="RuntimeError: unrelated"), result({}))
    assert cell["detection"] is False
    assert cell["failure_signature_matches"] is False


def test_j3_skipped_call_not_assertion_executed():
    item = row()
    target = item["expected"]["target_nodes"][0]
    cell = adjudicate.suite_cell(item, result({target: "skipped"}), result({}))
    assert cell["target_assertion_executed"] == "not_executed"
    assert cell["target_behaviour_executed"] is False


def test_j4_missing_health_reference_rejected():
    item = row()
    target = item["expected"]["target_nodes"][0]
    cell = adjudicate.suite_cell(item, result({target: "failed"},
                                               longrepr=signature_longrepr(item)), None)
    assert cell["detection"] is False
    assert cell["health_reference_usable"] is False


def test_j5_missing_s0_not_blind_spot():
    item = row()
    cell = adjudicate.suite_cell(item, result({}), result({}))
    decision = adjudicate.classify(cell, None, {"detection": True, "state": "detected"})
    assert decision["verdict"] == "NOT_EVALUABLE_MISSING_S0"
    invalid = {"verdict": "TECHNICAL_FAILURE", "health_reference_usable": False}
    assert adjudicate.classify(cell, invalid,
                               {"detection": True})["verdict"] == "NOT_EVALUABLE_MISSING_S0"


def test_j6_unknown_activation_not_retained():
    item = row()
    target = item["expected"]["target_nodes"][0]
    s0_item = row(suite="S0_original")
    s0 = adjudicate.suite_cell(s0_item,
                               result({target: "failed"},
                                      longrepr=signature_longrepr(s0_item)),
                               result({}))
    cell = adjudicate.suite_cell(item, result({target: "failed"},
                                              longrepr=signature_longrepr(item)), result({}))
    decision = adjudicate.classify(cell, s0, {"detection": None, "state": "unknown"})
    assert decision["verdict"] != "RETAINED"
    assert decision["verdict"] == "ACTIVATION_UNKNOWN"


def test_j9_parameterized_child_matched():
    item = row(case="C", context="C-C1", product="M1",
               suite="R3_explicit_parameterization")
    base = item["expected"]["target_nodes"][0]
    child = base + "[columns_unset]"
    s0 = adjudicate.suite_cell(item, result({child: "passed"}), result({child: "passed"}))
    s0["health_reference_usable"] = True
    s0["detection"] = True
    cell = adjudicate.suite_cell(item, result({child: "failed"},
                                              longrepr=signature_longrepr(item)),
                                 result({child: "passed"}))
    assert cell["target_behaviour_executed"] is True
    assert cell["detection"] is True
    decision = adjudicate.classify(cell, s0, {"detection": True, "state": "detected"})
    assert decision["verdict"] != "CHECK_NOT_EXECUTED"


# ------------------------------------------------------------------ R2

def test_j7_control_failure_blocks_retained():
    item = row()
    target = item["expected"]["target_nodes"][0]
    s0_item = row(suite="S0_original")
    s0 = adjudicate.suite_cell(s0_item,
                               result({target: "failed"},
                                      longrepr=signature_longrepr(s0_item)),
                               result({}))
    cell = adjudicate.suite_cell(item, result({target: "failed"},
                                              longrepr=signature_longrepr(item)), result({}))
    control = {"verdict": "CONTROL_FAILURE"}
    assert adjudicate.classify(cell, s0, {"detection": True},
                               control)["verdict"] == "NOT_EVALUABLE_CONTROL_FAILURE"


def test_j8_equal_pass_real_rows_reachable():
    results = {item["row_key"]: result({}) for item in ROWS["suite_rows"]}
    for item in ROWS["witness_rows"]:
        results[item["row_key"]] = {"status": "completed", "witness": None}
    document = adjudicate.adjudicate(ROWS, results, SPEC)
    assert document["equal_pass"] == []
    assert any(x.get("reason") == "activation_unknown"
               for x in document["equal_pass_exclusions"])
    suites = {suite for entry in document["equal_pass"]
              for suite in entry["jointly_evaluable_suites"]}
    assert not suites
    assert all("R1_global_fix" not in entry["jointly_evaluable_suites"]
               for entry in document["equal_pass"])


def test_pc_equal_pass_same_green_different_kills():
    cells = [
        {"case": "A", "context": "A-C1", "mutation_id": "A-M1",
         "suite_variant": "S0_original", "applicability": "full", "detection": True},
        {"case": "A", "context": "A-C1", "mutation_id": "A-M1",
         "suite_variant": "R2_local_isolation", "applicability": "full",
         "covers_requested_branch": True, "detection": False},
    ]
    output = adjudicate.equal_pass_comparison(*qualify_comparison(cells))
    assert output and output[0]["identical"] is False
    same = [dict(cell, detection=False) for cell in cells]
    assert adjudicate.equal_pass_comparison(*qualify_comparison(same))[0]["identical"] is True


# ------------------------------------------------------------------ R3

@pytest.mark.parametrize("case,context", [("A", "A-C1"), ("B", "B-C1"), ("C", "C-C1")])
def test_w1_empty_payload_not_detection(case, context):
    verdict = adjudicate.witness_verdict(case, context, "M1",
                                         {"case": case, "errors": {}, "observations": {}}, SPEC)
    assert verdict["detection"] is None
    assert verdict["state"] == "technical_gap"


def test_w2_bold_cyan_is_cyan():
    payload = {"case": "A", "errors": {}, "observations": {"colour": {
        "colour_present": True, "argparse_args_sgr": "1;36"}}}
    verdict = adjudicate.witness_verdict("A", "A-C1", "M1", payload, SPEC)
    assert verdict["detection"] is False
    payload["observations"]["colour"]["argparse_args_sgr"] = "35"
    assert adjudicate.witness_verdict("A", "A-C1", "M1", payload, SPEC)["detection"] is True


def test_w3_sibling_prefix_import_rejected(tmp_path):
    module = type("M", (), {"__file__": str(tmp_path / "product-elsewhere/mod.py")})()
    with patch.dict(sys.modules, {"synthetic_product": module}):
        assert probes.import_provenance(tmp_path / "product", "synthetic_product") is None


def test_e2_wrong_witness_not_completed(tmp_path):
    item = ROWS["witness_rows"][0]
    run_dir = tmp_path / "run"
    for name in ("home", "tmp", "bin"):
        (run_dir / name).mkdir(parents=True, exist_ok=True)

    def runner(argv, cwd, env, timeout):
        pathlib.Path(argv[argv.index("--json-out") + 1]).write_text(json.dumps(
            {"case": "WRONG", "product_root": "/wrong", "errors": {},
             "observations": {}}), encoding="utf-8")
        return {"returncode": 1, "stdout": "", "stderr": "", "timed_out": False,
                "duration_seconds": 0.01, "process_group_cleanup": True}

    record = adapter.execute_witness(item, run_dir, pathlib.Path("/synthetic/python"), runner)
    assert record["status"] != "completed"
    assert "witness_case_mismatch" in record["problems"]


# ------------------------------------------------------------------ R4

def test_j2_incomplete_session_not_usable():
    assert not adjudicate.row_is_usable(result({}, rc=-9, session=None, malformed=1))


def test_e1_missing_jsonl_not_completed(tmp_path):
    item = row(suite="S0_original")
    run_dir = tmp_path / "run"
    for name in ("home", "tmp", "bin"):
        (run_dir / name).mkdir(parents=True, exist_ok=True)

    def runner(argv, cwd, env, timeout):
        return {"returncode": 2, "stdout": "", "stderr": "collection error",
                "timed_out": False, "duration_seconds": 0.01,
                "process_group_cleanup": True}

    record = adapter.execute_suite(item, run_dir, pathlib.Path("/synthetic/python"), runner)
    assert record["status"] == "failed"
    assert "missing_jsonl" in record["problems"]
    assert not adjudicate.row_is_usable(record)


def test_e3_b0_environment_reused():
    env = adapter.build_environment(row(suite="S0_original"), pathlib.Path("/tmp/env"))
    assert "LC_ALL" not in env
    assert env["TERM"] == "xterm-256color"
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert "PYHOST_NETLOG" in env and "PYHOST_ROUND_META" in env
    assert "PYTHONPATH" not in env
    assert env["LANG"] == "C.UTF-8" and env["PYTHONHASHSEED"] == "0"


# ------------------------------------------------------------------ R5

def clearance_document():
    return {"batch_id": adapter.BATCH_ID, "authorized": True,
            "execution_authorized": True, "signed_by": "synthetic-control",
            "commitment_sha256": adapter.sha(adapter.COMMITMENT_PATH),
            "rows_sha256": adapter.rows_hash(ROWS), "counts": ROWS["counts"]}


def test_pc_clearance_unchanged_accepted(tmp_path):
    path = tmp_path / "clearance.json"
    path.write_text(json.dumps(clearance_document()), encoding="utf-8")
    assert adapter.clearance_problems(path, mode="run") == []


def test_g1_rows_drift_rejected(tmp_path):
    path = tmp_path / "clearance.json"
    path.write_text(json.dumps(clearance_document()), encoding="utf-8")
    tampered = copy.deepcopy(ROWS)
    tampered["suite_rows"][0]["context_environment"] = {"state": "set", "value": "unexpected"}
    altered = tmp_path / "altered.json"
    altered.write_text(json.dumps(tampered), encoding="utf-8")
    original = adapter.ROWS_PATH
    try:
        adapter.ROWS_PATH = altered
        assert "rows_drift" in adapter.clearance_problems(path)
    finally:
        adapter.ROWS_PATH = original


def test_g2_package_rule_drift_rejected(tmp_path):
    path = tmp_path / "clearance.json"
    path.write_text(json.dumps(clearance_document()), encoding="utf-8")
    (tmp_path / "changed_adjudicator.py").write_text("# changed\n", encoding="utf-8")
    original = adapter.HERE
    try:
        adapter.HERE = tmp_path
        problems = adapter.clearance_problems(path)
        assert "package_digest_drift" in problems
        assert any(problem.startswith("rule_drift:") for problem in problems)
    finally:
        adapter.HERE = original


def test_g3_prepare_gated(tmp_path):
    with (patch.object(adapter, "prepare", return_value={}) as prepare,
          patch.object(sys, "argv", ["s3_adapter.py", "--prepare",
                                     "--state-root", str(tmp_path / "prep")]),
          contextlib.redirect_stdout(io.StringIO())):
        code = adapter.main()
    assert code == 2
    assert prepare.call_count == 0


def make_state(tmp_path):
    tools = json.loads((HERE / "inputs/toolchains.json").read_text(encoding="utf-8"))["toolchains"]
    state = tmp_path / "state"
    for project in tools:
        python = state / "venvs" / project / "venv/bin/python"
        python.parent.mkdir(parents=True, exist_ok=True)
        python.touch()
    clearance = tmp_path / "clearance.json"
    clearance.write_text(json.dumps(clearance_document()), encoding="utf-8")
    return state, clearance


def invoke(state, clearance, rows, fake_dispatch):
    with (patch.object(adapter, "load_rows", return_value=(ROWS, rows)),
          patch.object(adapter, "dispatch", side_effect=fake_dispatch) as dispatch,
          patch.object(sys, "argv", ["s3_adapter.py", "--run", "--state-root", str(state),
                                     "--clearance", str(clearance)]),
          contextlib.redirect_stdout(io.StringIO())):
        code = adapter.main()
    return code, dispatch.call_count


def test_g4_foreign_ledger_rejected(tmp_path):
    state, clearance = make_state(tmp_path)
    item = row(suite="S0_original")
    run_id = adapter.run_id_of(item)
    adapter.write_json(state / "ledger.json", {
        "batch_id": "FOREIGN_BATCH", "commitment_sha256": "FOREIGN",
        "rows_sha256": ROWS["rows_sha256"],
        "records": {run_id: {"status": "completed", "row_key": "FOREIGN_ROW"}}})
    code, calls = invoke(state, clearance, [item], lambda *args: result())
    assert code == 2
    assert calls == 0


def test_g5_technical_failure_returns_nonzero(tmp_path):
    state, clearance = make_state(tmp_path)
    item = row(suite="S0_original")

    def fail(*args):
        raise RuntimeError("synthetic instrument failure")

    code, calls = invoke(state, clearance, [item], fail)
    assert calls == 1
    assert code == 1
    ledger = json.loads((state / "ledger.json").read_text())
    record = ledger["records"][adapter.run_id_of(item)]
    assert record["status"] == "technical_failure"


def test_g6_timeout_preserved_and_not_retried(tmp_path):
    state, clearance = make_state(tmp_path)
    item = row(suite="S0_original")
    run_id = adapter.run_id_of(item)
    ledger = {"batch_id": adapter.BATCH_ID,
              "commitment_sha256": adapter.sha(adapter.COMMITMENT_PATH),
              "rows_sha256": adapter.rows_hash(ROWS),
              "plan_sha256": adapter.canonical_sha(adapter.plan_of([item])),
              "plan": adapter.plan_of([item]),
              "records": {run_id: {"status": "timeout", "timed_out": True,
                                   "row_key": item["row_key"], "sentinel": "old"}}}
    adapter.write_json(state / "ledger.json", ledger)
    code, calls = invoke(state, clearance, [item], lambda *args: result())
    assert calls == 0
    saved = json.loads((state / "ledger.json").read_text())
    assert saved["records"][run_id].get("sentinel") == "old"


def test_pc_same_batch_completed_skip(tmp_path):
    state, clearance = make_state(tmp_path)
    item = row(suite="S0_original")
    run_id = adapter.run_id_of(item)
    ledger = {"batch_id": adapter.BATCH_ID,
              "commitment_sha256": adapter.sha(adapter.COMMITMENT_PATH),
              "rows_sha256": adapter.rows_hash(ROWS),
              "plan_sha256": adapter.canonical_sha(adapter.plan_of([item])),
              "plan": adapter.plan_of([item]),
              "records": {run_id: {"status": "completed", "row_key": item["row_key"]}}}
    adapter.write_json(state / "ledger.json", ledger)
    code, calls = invoke(state, clearance, [item], lambda *args: result())
    assert code == 0
    assert calls == 0


def test_pc_timeout_unusable():
    assert not adjudicate.row_is_usable({"status": "completed", "timed_out": True})


# --------------------------------------------------- original witness control

def mutant_payload(case, context, product):
    if case == "A":
        state = {"A-C1": "unset", "A-C2": "set", "A-C3": "empty", "A-C4": "set"}[context]
        if product == "M1":
            return {"case": "A", "errors": {}, "observations": {"colour": {
                "colour_present": True, "argparse_args_sgr": "35"}}}
        if product == "M2":
            present = state == "set"
            return {"case": "A", "errors": {}, "observations": {"colour": {
                "colour_present": present, "argparse_args_sgr": "36" if present else None}}}
        present = state != "empty"
        return {"case": "A", "errors": {}, "observations": {"colour": {
            "colour_present": present, "argparse_args_sgr": "36" if present else None}}}
    if case == "B":
        columns = {"B-C1": 80, "B-C2": 40, "B-C3": 80, "B-C4": 120}[context]
        return {"case": "B", "errors": {}, "observations": {"width": {
            "formatter_width_default": 80,
            "formatter_width_explicit_70": 60 if product == "M2" else 70,
            "argparse_width_explicit_70": columns - 2 if product == "M3" else 70,
            "max_help_line_width_default": 80,
            "max_help_line_width_explicit_70": 70,
            "argparse_max_line_width_explicit_70": columns - 2 if product == "M3" else 70}}}
    width = {"C-C1": 80, "C-C2": 40, "C-C3": 80, "C-C4": 120}[context]
    text = "Parse Error"
    if product in ("M1", "M3"):
        text = "Error parsing reStructuredText" if product == "M3" else "Parse Error"
    return {"case": "C", "errors": {}, "observations": {"panel": {
        "exit_code": 1, "max_line_width": 80 if product == "M2" else width,
        "unwrapped": text, "repeat_stable": True}}}


def original_payload(case, context):
    if case == "A":
        present = context in ("A-C1", "A-C3")
        return {"case": "A", "errors": {}, "observations": {"colour": {
            "colour_present": present,
            "argparse_args_sgr": "36" if present else None}}}
    if case == "B":
        columns = {"B-C1": 80, "B-C2": 40, "B-C3": 80, "B-C4": 120}[context]
        return {"case": "B", "errors": {}, "observations": {"width": {
            "formatter_width_default": columns - 2,
            "formatter_width_explicit_70": 70,
            "argparse_width_explicit_70": 70,
            "max_help_line_width_default": columns - 2,
            "max_help_line_height_explicit_70": 70,
            "max_help_line_width_explicit_70": 70,
            "argparse_max_line_width_explicit_70": 70}}}
    width = {"C-C1": 80, "C-C2": 40, "C-C3": 80, "C-C4": 120}[context]
    return {"case": "C", "errors": {}, "observations": {"panel": {
        "exit_code": 1, "max_line_width": width,
        "unwrapped": "Error parsing reStructuredText ValueError: Fake parse failure",
        "repeat_stable": True}}}


def test_witness_original_control_required():
    results = {}
    for item in ROWS["witness_rows"]:
        if item["product_variant"] == "original":
            payload = original_payload(item["case"], item["context"])
            if item["case"] == "A" and item["context"] == "A-C1":
                payload = {"case": "A", "errors": {}, "observations": {}}
        else:
            payload = mutant_payload(item["case"], item["context"], item["product_variant"])
        results[item["row_key"]] = {"status": "completed", "witness": payload}
    for item in ROWS["suite_rows"]:
        results.setdefault(item["row_key"], result({}))
    document = adjudicate.adjudicate(ROWS, results, SPEC)
    broken = [cell for cell in document["cells"]
              if cell["case"] == "A" and cell["context"] == "A-C1"]
    assert broken
    assert all(cell["witness_state"] == "control_broken" for cell in broken)
    assert all(cell["verdict"] != "RETAINED" for cell in broken)


def test_full_240_control_failure_and_retained_path():
    results = {}
    for item in ROWS["suite_rows"]:
        nodes = {}
        if item["product_variant"] != "original":
            nodes = {node: "failed" for node in item["expected"]["target_nodes"]}
        if (item["product_variant"] == "original"
                and item["case"] == "A" and item["context"] == "A-C1"
                and item["suite_variant"] == "R2_local_isolation"):
            nodes = {"tests/synthetic.py::test_non_target": "failed"}
        if (item["case"] == "B" and item["context"] == "B-C1"
                and item["product_variant"] == "M1"
                and item["suite_variant"] == "R3_explicit_parameterization"):
            nodes = {node: "passed" for node in item["expected"]["target_nodes"]}
        longrepr = (signature_longrepr(item)
                    if item["product_variant"] != "original" and nodes
                    else "AssertionError: x")
        results[item["row_key"]] = result(nodes, longrepr=longrepr)
    for item in ROWS["witness_rows"]:
        payload = (original_payload(item["case"], item["context"])
                   if item["product_variant"] == "original"
                   else mutant_payload(item["case"], item["context"],
                                       item["product_variant"]))
        results[item["row_key"]] = {"status": "completed", "witness": payload}
    document = adjudicate.adjudicate(ROWS, results, SPEC)
    control = [entry for entry in document["controls_original_product"]
               if (entry["case"], entry["context"], entry["suite_variant"])
               == ("A", "A-C1", "R2_local_isolation")][0]
    assert control["verdict"] == "CONTROL_FAILURE"
    failed_cell = [cell for cell in document["cells"]
                   if cell["row_key"] == "A|A-C1|M1|R2_local_isolation|r1"][0]
    assert failed_cell["verdict"] == "NOT_EVALUABLE_CONTROL_FAILURE"
    retained = [cell for cell in document["cells"]
                if cell["row_key"] == "A|A-C1|M1|R3_explicit_parameterization|r1"][0]
    assert retained["verdict"] == "RETAINED"
    assert document["equal_pass"]
    assert any(entry["identical"] is False for entry in document["equal_pass"])
