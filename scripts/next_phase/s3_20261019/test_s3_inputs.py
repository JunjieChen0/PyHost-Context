"""ClawsGO Science Agent：S3 输入与判定函数的合成回归（不导入、不执行候选）。

运行：``python3 -m pytest test_s3_inputs.py -q``
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import s3_adjudicate as adjudicate  # noqa: E402
import s3_adapter as adapter  # noqa: E402
import s3_common as common  # noqa: E402

ROWS = json.loads((HERE / "S3_ROWS_20261019.json").read_text(encoding="utf-8"))
SPEC = json.loads((HERE / "inputs/witness_spec.json").read_text(encoding="utf-8"))["spec"]
TREES = json.loads((HERE / "inputs/trees.json").read_text(encoding="utf-8"))["trees"]
PRODUCTS = json.loads((HERE / "inputs/products.json").read_text(encoding="utf-8"))["products"]


def row_by(case, context, product, suite):
    for row in ROWS["suite_rows"]:
        if (row["case"], row["context"], row["product_variant"], row["suite_variant"]) == \
                (case, context, product, suite):
            return row
    raise KeyError((case, context, product, suite))


def test_row_counts_and_labels():
    assert ROWS["counts"] == {"suite": 192, "witness": 48, "total": 240}
    keys = [row["row_key"] for row in ROWS["suite_rows"] + ROWS["witness_rows"]]
    assert len(keys) == len(set(keys)) == 240
    keep = ("row_key", "case", "context", "product_variant", "suite_variant",
            "repeat", "label_sha256", "call_kind", "order")
    bare = [{key: row[key] for key in keep if key in row}
            for row in ROWS["suite_rows"] + ROWS["witness_rows"]]
    digest = common.sha256_bytes(
        json.dumps(bare, sort_keys=True, ensure_ascii=False).encode())
    assert digest == "1861bab19983fff880828f117bf777da7c84249f2efd36e855f8bb8ffdf4fc79"


def test_every_row_bound_to_existing_tree():
    for row in ROWS["suite_rows"] + ROWS["witness_rows"]:
        path = HERE / row["tree"]["path"]
        assert path.is_file(), row["row_key"]
        assert common.sha256_file(path) == row["tree"]["tar_sha256"], row["row_key"]
        assert row["tree"]["tree_sha256"] == TREES[
            f"{row['case']}/{row['product_variant']}__"
            f"{row.get('suite_variant', 'S0_original')}"]["tree_sha256"]


def test_tree_roundtrip_and_s0_identity(tmp_path):
    for key, tree in TREES.items():
        blob = (HERE / tree["path"]).read_bytes()
        target = tmp_path / key.replace("/", "__")
        common.unpack_tree(blob, target)
        assert common.tree_hash(target) == tree["tree_sha256"]
        case, variant_suite = key.split("/")
        variant, suite = variant_suite.split("__")
        if suite == "S0_original":
            assert tree["tree_sha256"] == PRODUCTS[case]["variants"][variant]["tree_sha256"]


def test_nine_named_mutants_present_and_distinct():
    seen = {}
    for case in ("A", "B", "C"):
        for variant in ("M1", "M2", "M3"):
            info = PRODUCTS[case]["variants"][variant]
            assert info["patch_sha256"]
            assert info["tree_sha256"] not in seen
            seen[info["tree_sha256"]] = f"{case}/{variant}"
    assert len(seen) == 9


def test_suite_patch_hashes_recompute():
    suites = json.loads((HERE / "inputs/suites.json").read_text(encoding="utf-8"))["suites"]
    for case, entry in suites.items():
        for name, suite in entry.items():
            payload = {"source_artifact_sha256": suite.get("source_artifact_sha256"),
                       "changed_files": suite["changed_files"]}
            if name == "S0_original":
                continue
            assert suite["patch_sha256"] == common.sha256_bytes(
                json.dumps(payload, sort_keys=True, ensure_ascii=False).encode())


def test_witness_spec_covers_all_products_and_contexts():
    for case in ("A", "B", "C"):
        assert set(SPEC[case]["mutation_checks"]) == {"M1", "M2", "M3"}
        for context in SPEC[case]["expected_original"]:
            assert "context_environment" in SPEC[case]["expected_original"][context]


# ---------------------------------------------------------------- adjudication

def signature_longrepr(row):
    locations = row["expected"]["failure_signature"]["assertion_locations"]
    location = locations[0]
    if location["lines"]:
        return f"{location['file']}:{location['lines'][0]}: AssertionError: cyan != magenta"
    return f"{location['file']}: AssertionError in {location['function']}"


def make_result(nodes, rc=None, timed_out=False, status="completed",
                longrepr=None, session=True, malformed=0):
    if rc is None:
        rc = 1 if any(value in ("failed", "error") for value in nodes.values()) else 0
    return {
        "status": status, "returncode": rc, "timed_out": timed_out,
        "tree_after_ok": True, "process_group_cleanup": True,
        "malformed_jsonl_lines": malformed,
        "collection_finished": True, "collected": list(nodes),
        "session": ({"$report_type": "SessionFinished", "exitstatus": rc}
                    if session else None),
        "node_outcomes": {
            node: {"phase_details": {"setup": {"outcome": "passed"},
                                     "call": {"outcome": outcome,
                                              "longrepr": longrepr if outcome == "failed" else None},
                                     "teardown": {"outcome": "passed"}},
                   "longrepr": longrepr if outcome == "failed" else None}
            for node, outcome in nodes.items()}}


def cell_for(row, nodes, baseline_nodes=None, **kwargs):
    baseline = make_result(baseline_nodes or {})
    return adjudicate.suite_cell(row, make_result(nodes, **kwargs), baseline)


def test_row_is_usable_requirements():
    assert adjudicate.row_is_usable(make_result({}))
    assert not adjudicate.row_is_usable(make_result({}, rc=-9, session=None, malformed=1))
    assert not adjudicate.row_is_usable(make_result({}, timed_out=True))
    assert not adjudicate.row_is_usable({"status": "completed", "returncode": 0})


def test_signature_requires_registered_location_and_exception():
    row = row_by("A", "A-C1", "M1", "S0_original")
    target = row["expected"]["target_nodes"][0]
    unrelated = make_result({target: "failed"}, longrepr="RuntimeError: unrelated")
    cell = adjudicate.suite_cell(row, unrelated, make_result({}))
    assert cell["detection"] is False
    assert cell["failure_signature_matches"] is False
    matched = make_result({target: "failed"}, longrepr=signature_longrepr(row))
    cell = adjudicate.suite_cell(row, matched, make_result({}))
    assert cell["detection"] is True
    assert cell["failure_signature_matches"] is True


def test_skipped_call_is_not_assertion_executed():
    row = row_by("A", "A-C1", "M1", "S0_original")
    target = row["expected"]["target_nodes"][0]
    cell = adjudicate.suite_cell(row, make_result({target: "skipped"}), make_result({}))
    assert cell["target_behaviour_executed"] is False
    assert cell["target_assertion_executed"] == "not_executed"


def test_missing_health_reference_blocks_detection():
    row = row_by("A", "A-C1", "M1", "S0_original")
    target = row["expected"]["target_nodes"][0]
    cell = adjudicate.suite_cell(row, make_result({target: "failed"},
                                                  longrepr=signature_longrepr(row)), None)
    assert cell["detection"] is False
    assert cell["health_reference_usable"] is False


def test_background_failure_is_not_detection():
    row = row_by("A", "A-C2", "M1", "S0_original")
    target = row["expected"]["target_nodes"][0]
    baseline = make_result({target: "failed"}, rc=1)
    cell = adjudicate.suite_cell(row, make_result({target: "failed"}, rc=1,
                                                  longrepr=signature_longrepr(row)), baseline)
    assert cell["background_failure"] is True
    assert cell["detection"] is False


def test_retained_requires_activation():
    row = row_by("A", "A-C1", "M1", "R2_local_isolation")
    target = row["expected"]["target_nodes"][0]
    baseline = make_result({})
    s0_row = row_by("A", "A-C1", "M1", "S0_original")
    s0_mutant = adjudicate.suite_cell(
        s0_row, make_result({target: "failed"},
                            longrepr=signature_longrepr(s0_row)), baseline)
    repair = adjudicate.suite_cell(row, make_result({target: "failed"},
                                                    longrepr=signature_longrepr(row)), baseline)
    assert adjudicate.classify(repair, s0_mutant,
                               {"state": "detected", "detection": True})["verdict"] == "RETAINED"
    assert adjudicate.classify(repair, s0_mutant,
                               {"state": "unknown", "detection": None})["verdict"] == "ACTIVATION_UNKNOWN"
    miss = adjudicate.suite_cell(row, make_result({target: "passed"}), baseline)
    assert adjudicate.classify(miss, s0_mutant,
                               {"state": "detected", "detection": True})["verdict"] == "DEGRADED"
    assert adjudicate.classify(miss, s0_mutant,
                               {"state": "not_detected", "detection": False})["verdict"] == \
        "MUTATION_NOT_ACTIVE_IN_CONTEXT"


def test_missing_s0_is_not_blind_spot():
    row = row_by("A", "A-C1", "M1", "R2_local_isolation")
    cell = adjudicate.suite_cell(row, make_result({}), make_result({}))
    assert adjudicate.classify(cell, None, {"detection": True})["verdict"] == \
        "NOT_EVALUABLE_MISSING_S0"
    invalid_s0 = {"verdict": "TECHNICAL_FAILURE", "health_reference_usable": False}
    assert adjudicate.classify(cell, invalid_s0, {"detection": True})["verdict"] == \
        "NOT_EVALUABLE_MISSING_S0"


def test_control_failure_blocks_retained():
    row = row_by("A", "A-C1", "M1", "R2_local_isolation")
    target = row["expected"]["target_nodes"][0]
    baseline = make_result({})
    s0_row = row_by("A", "A-C1", "M1", "S0_original")
    s0_mutant = adjudicate.suite_cell(
        s0_row, make_result({target: "failed"},
                            longrepr=signature_longrepr(s0_row)), baseline)
    repair = adjudicate.suite_cell(row, make_result({target: "failed"},
                                                    longrepr=signature_longrepr(row)), baseline)
    control = {"verdict": "CONTROL_FAILURE"}
    assert adjudicate.classify(repair, s0_mutant,
                               {"detection": True}, control)["verdict"] == \
        "NOT_EVALUABLE_CONTROL_FAILURE"


def test_parameterized_target_identity_matched():
    row = row_by("C", "C-C1", "M1", "R3_explicit_parameterization")
    base = row["expected"]["target_nodes"][0]
    child = base + "[columns_unset]"
    baseline = make_result({child: "passed"})
    cell = adjudicate.suite_cell(row, make_result(
        {child: "failed"}, longrepr=signature_longrepr(row)), baseline)
    assert cell["observed_target_children"] == [child]
    assert cell["target_behaviour_executed"] is True
    assert cell["target_assertion_executed"] == "executed_registered_assertion"
    assert cell["detection"] is True
    # 无约束前缀不匹配：只允许登记的参数 id。
    bogus = base + "[columns_999]"
    cell = adjudicate.suite_cell(row, make_result({bogus: "failed"}), baseline)
    assert cell["observed_target_children"] == []
    # 已有参数的基节点：A 的 test_user_usage[default] + fixture 参数。
    arow = row_by("A", "A-C1", "M1", "R3_explicit_parameterization")
    abase = [node for node in arow["expected"]["target_nodes"] if node.endswith("[default]")][0]
    achild = abase[:-1] + "-no_color_absent]"
    assert adjudicate.match_target(achild, arow["expected"]["identity"]) == abase
    assert adjudicate.match_target(abase[:-1] + "-columns_40]", arow["expected"]["identity"]) is None


def test_equal_pass_real_rows_non_empty():
    results = {}
    for row in ROWS["suite_rows"]:
        nodes = {}
        results[row["row_key"]] = make_result(nodes)
    for row in ROWS["witness_rows"]:
        results[row["row_key"]] = {"status": "completed", "witness": None}
    document = adjudicate.adjudicate(ROWS, results, SPEC)
    assert document["equal_pass"]
    suites = {suite for item in document["equal_pass"]
              for suite in item["jointly_evaluable_suites"]}
    assert "S0_original" in suites
    assert all("R1_global_fix" not in item["jointly_evaluable_suites"]
               for item in document["equal_pass"])


def test_equal_pass_kill_sets_differ_when_same_green():
    cells = [
        {"case": "A", "context": "A-C1", "mutation_id": "A-M1",
         "suite_variant": "S0_original", "applicability": "full", "detection": True},
        {"case": "A", "context": "A-C1", "mutation_id": "A-M1",
         "suite_variant": "R2_local_isolation", "applicability": "full",
         "covers_requested_branch": True, "detection": False},
    ]
    rows = adjudicate.equal_pass_comparison(cells)
    assert rows and rows[0]["identical"] is False
    assert rows[0]["jointly_evaluable_suites"] == ["R2_local_isolation", "S0_original"]


# -------------------------------------------------------------------- witnesses

def valid_payload(case, context):
    if case == "A":
        colour = context in ("A-C1", "A-C3")
        return {"case": "A", "errors": {}, "observations": {"colour": {
            "colour_present": colour, "argparse_args_sgr": "36" if colour else None}}}
    if case == "B":
        columns = {"B-C1": 80, "B-C2": 40, "B-C3": 80, "B-C4": 120}[context]
        return {"case": "B", "errors": {}, "observations": {"width": {
            "formatter_width_default": columns - 2,
            "formatter_width_explicit_70": 70,
            "argparse_width_explicit_70": 70,
            "max_help_line_width_default": columns - 2,
            "max_help_line_width_explicit_70": 70,
            "argparse_max_line_width_explicit_70": 70}}}
    width = {"C-C1": 80, "C-C2": 40, "C-C3": 80, "C-C4": 120}[context]
    return {"case": "C", "errors": {}, "observations": {"panel": {
        "exit_code": 1, "max_line_width": width,
        "unwrapped": "Error parsing reStructuredText ValueError: Fake parse failure",
        "repeat_stable": True}}}


def test_witness_payload_validation():
    for case, context in (("A", "A-C1"), ("B", "B-C1"), ("C", "C-C1")):
        good = adjudicate.witness_verdict(case, context, "M1", valid_payload(case, context), SPEC)
        assert good["state"] != "technical_gap"
        empty = adjudicate.witness_verdict(case, context, "M1",
                                           {"case": case, "errors": {}, "observations": {}}, SPEC)
        assert empty["detection"] is None
        assert empty["state"] == "technical_gap"


def test_witness_colour_decode():
    payload = valid_payload("A", "A-C1")
    payload["observations"]["colour"]["argparse_args_sgr"] = "1;36"
    assert adjudicate.witness_verdict("A", "A-C1", "M1", payload, SPEC)["detection"] is False
    payload["observations"]["colour"]["argparse_args_sgr"] = "35"
    assert adjudicate.witness_verdict("A", "A-C1", "M1", payload, SPEC)["detection"] is True
    payload["observations"]["colour"]["argparse_args_sgr"] = "38;5;36"
    assert adjudicate.witness_verdict("A", "A-C1", "M1", payload, SPEC)["detection"] is False
    del payload["observations"]["colour"]["argparse_args_sgr"]
    assert adjudicate.witness_verdict("A", "A-C1", "M1", payload, SPEC)["state"] == "technical_gap"


def test_witness_b_and_c_rules():
    payload = valid_payload("B", "B-C1")
    payload["observations"]["width"]["formatter_width_default"] = 80
    payload["observations"]["width"]["max_help_line_width_default"] = 80
    assert adjudicate.witness_verdict("B", "B-C1", "M1", payload, SPEC)["detection"] is True
    assert adjudicate.witness_verdict("B", "B-C1", "M2", payload, SPEC)["state"] == \
        "not_activated_by_declaration"
    c_payload = valid_payload("C", "C-C1")
    c_payload["observations"]["panel"]["unwrapped"] = "Parse Error"
    assert adjudicate.witness_verdict("C", "C-C1", "M1", c_payload, SPEC)["detection"] is True
    assert adjudicate.witness_verdict("C", "C-C1", "M3", c_payload, SPEC)["detection"] is True
    c_payload = valid_payload("C", "C-C2")
    c_payload["observations"]["panel"]["max_line_width"] = 80
    assert adjudicate.witness_verdict("C", "C-C2", "M2", c_payload, SPEC)["detection"] is True


# ---------------------------------------------------------------------- adapter

def test_environment_three_state():
    row = row_by("A", "A-C3", "M1", "S0_original")
    env = adapter.build_environment(row, pathlib.Path("/tmp/run"))
    assert env["NO_COLOR"] == ""
    row2 = row_by("A", "A-C1", "M1", "S0_original")
    assert "NO_COLOR" not in adapter.build_environment(row2, pathlib.Path("/tmp/run"))
    row3 = row_by("A", "A-C2", "M1", "S0_original")
    assert adapter.build_environment(row3, pathlib.Path("/tmp/run"))["NO_COLOR"] == "1"
    assert "PYTHONPATH" not in env
    assert env["TERM"] == "xterm-256color"
    assert "LC_ALL" not in env
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert "PYHOST_NETLOG" in env


def test_clearance_rejects_unsigned(tmp_path):
    assert adapter.clearance_problems(None) == ["clearance_missing"]
    path = tmp_path / "clearance.json"
    path.write_text(json.dumps({"batch_id": "next-S3-20261019", "authorized": False}),
                    encoding="utf-8")
    problems = adapter.clearance_problems(path)
    assert "clearance_not_authorized" in problems
    assert "commitment_sha_mismatch" in problems


def test_clearance_accepts_unchanged_and_rejects_drift(tmp_path):
    clearance = tmp_path / "clearance.json"
    clearance.write_text(json.dumps({
        "batch_id": adapter.BATCH_ID, "authorized": True, "execution_authorized": True,
        "signed_by": "synthetic-control", "commitment_sha256": adapter.sha(adapter.COMMITMENT_PATH),
        "rows_sha256": adapter.rows_hash(ROWS), "counts": ROWS["counts"]}), encoding="utf-8")
    assert adapter.clearance_problems(clearance, mode="run") == []
    tampered = json.loads(json.dumps(ROWS))
    tampered["suite_rows"][0]["context_environment"] = {"state": "set", "value": "unexpected"}
    altered = tmp_path / "altered.json"
    altered.write_text(json.dumps(tampered), encoding="utf-8")
    original = adapter.ROWS_PATH
    try:
        adapter.ROWS_PATH = altered
        assert "rows_drift" in adapter.clearance_problems(clearance)
    finally:
        adapter.ROWS_PATH = original


def fake_runner_factory(row, nodes, report_lines=None):
    def runner(argv, cwd, env, timeout):
        if "-c" in argv:
            payload = {"python": "3.11.9", "implementation": "CPython",
                       "flags": {"utf8_mode": 0, "ignore_environment": 0, "isolated": 0},
                       "env": dict(env)}
            return {"returncode": 0, "stdout": json.dumps(payload), "stderr": "",
                    "timed_out": False, "duration_seconds": 0.01,
                    "process_group_cleanup": True}
        pathlib.Path(env["PYHOST_NETLOG"]).write_text(
            json.dumps({"kind": "observer_init"}) + "\n", encoding="utf-8")
        rc = 1 if any(value == "failed" for value in nodes.values()) else 0
        lines = report_lines
        if lines is None:
            lines = [{"$report_type": "CollectionItem", "nodeid": node} for node in nodes]
            lines.append({"$report_type": "CollectionFinished",
                          "collected": list(nodes), "collected_count": len(nodes)})
            for node, outcome in nodes.items():
                lines.append({"$report_type": "TestReport", "nodeid": node,
                              "when": "call", "outcome": outcome,
                              "longrepr": "AssertionError" if outcome == "failed" else None})
            lines.append({"$report_type": "SessionFinished", "exitstatus": rc})
            lines.insert(0, {"$report_type": "RuntimeSnapshot",
                             "environment": dict(env),
                             "round_meta": {"row_key": row["row_key"]}})
        pathlib.Path(env["PYHOST_JSONL_PATH"]).write_text(
            "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
        pathlib.Path(env["PYHOST_NETLOG"]).write_text(
            json.dumps({"kind": "observer_init", "pid": 1001}) + "\n"
            + json.dumps({"kind": "observer_end", "pid": 1001}) + "\n",
            encoding="utf-8")
        return {"returncode": rc, "stdout": "", "stderr": "", "timed_out": False,
                "duration_seconds": 0.1, "process_group_cleanup": True,
                "child_pid": 1001}
    return runner


def test_execute_suite_with_fake_runner(tmp_path):
    row = row_by("A", "A-C1", "M1", "S0_original")
    target = row["expected"]["target_nodes"][0]
    run_dir = tmp_path / "run"
    for name in ("home", "tmp", "bin"):
        (run_dir / name).mkdir(parents=True, exist_ok=True)
    record = adapter.execute_suite(
        row, run_dir, pathlib.Path("/fake/python"),
        runner=fake_runner_factory(row, {target: "passed"}))
    assert record["status"] == "completed"
    assert record["tree_after_ok"] is True
    assert record["boot_views"]["snapshot_env_matches_launch"] is True
    assert record["collection_finished"] is True
    assert adjudicate.row_is_usable(record)


def test_execute_suite_rejects_missing_session(tmp_path):
    row = row_by("A", "A-C1", "M1", "S0_original")
    run_dir = tmp_path / "run"
    for name in ("home", "tmp", "bin"):
        (run_dir / name).mkdir(parents=True, exist_ok=True)
    lines = [{"$report_type": "CollectionFinished", "collected": [], "collected_count": 0}]
    record = adapter.execute_suite(
        row, run_dir, pathlib.Path("/fake/python"),
        runner=fake_runner_factory(row, {}, report_lines=lines))
    assert record["status"] == "failed"
    assert "missing_session" in record["problems"]
    assert not adjudicate.row_is_usable(record)


def test_execute_witness_validates_identity(tmp_path):
    row = [item for item in ROWS["witness_rows"]
           if (item["case"], item["context"], item["product_variant"]) == ("A", "A-C1", "M1")][0]
    run_dir = tmp_path / "run"
    for name in ("home", "tmp", "bin"):
        (run_dir / name).mkdir(parents=True, exist_ok=True)

    def runner(argv, cwd, env, timeout):
        out = pathlib.Path(argv[argv.index("--json-out") + 1])
        out.write_text(json.dumps({"case": "WRONG", "product_root": "/wrong",
                                   "errors": {}, "observations": {}}), encoding="utf-8")
        return {"returncode": 1, "stdout": "", "stderr": "", "timed_out": False,
                "duration_seconds": 0.1, "process_group_cleanup": True}

    record = adapter.execute_witness(row, run_dir, pathlib.Path("/fake/python"), runner)
    assert record["status"] == "failed"
    assert "witness_case_mismatch" in record["problems"]
    assert "witness_product_root_mismatch" in record["problems"]
    assert "witness_nonzero_exit" in record["problems"]


def test_commitment_digest_recomputes():
    import build_s3_inputs as build
    commitment = json.loads(
        (HERE / "S3_PRE_RUN_COMMITMENT_20261019.json").read_text(encoding="utf-8"))
    assert commitment["package_digest"] == build.package_digest(
        {HERE / "S3_PRE_RUN_COMMITMENT_20261019.json"})
    assert commitment["execution_authorized"] is False


def test_status_reports_unauthorized():
    document = json.loads((HERE / "S3_ROWS_20261019.json").read_text(encoding="utf-8"))
    status = adapter.status(document)
    assert status["execution_authorized"] is False
    assert status["cleared"] is False


def test_full_adjudication_synthetic():
    results = {}
    for row in ROWS["suite_rows"]:
        results[row["row_key"]] = make_result({})
    for row in ROWS["witness_rows"]:
        results[row["row_key"]] = {
            "status": "completed", "returncode": 0, "timed_out": False,
            "tree_after_ok": True,
            "witness": valid_payload(row["case"], row["context"]),
        }
    document = adjudicate.adjudicate(ROWS, results, SPEC)
    assert len(document["cells"]) == 108
    assert len(document["controls_original_product"]) == 36
    assert len(document["s0_reference_cells"]) == 36
    for control in document["controls_original_product"]:
        assert control["verdict"] == "CONTROL_ORIGINAL_PRODUCT"
