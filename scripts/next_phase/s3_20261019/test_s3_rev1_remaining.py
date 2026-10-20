"""ClawsGO Science Agent：REV1 残留三组的拒绝回归（R1 / R2 / R4）。

对应 `PyHost_S3_REV1_Reviewer001_20261019.zip` 的 12 项残留观察与 3 项正控制。
全部合成：不导入、不执行真实候选；prepare 使用假安装器与合成 wheel。
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from unittest.mock import patch

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import s3_adapter as adapter  # noqa: E402
import s3_adjudicate as adjudicate  # noqa: E402

ROWS = json.loads((HERE / "S3_ROWS_20261019.json").read_text(encoding="utf-8"))
SPEC = json.loads((HERE / "inputs/witness_spec.json").read_text(encoding="utf-8"))["spec"]


def row(case="A", context="A-C1", product="M1",
        suite="R3_explicit_parameterization"):
    return next(item for item in ROWS["suite_rows"]
                if (item["case"], item["context"], item["product_variant"],
                    item["suite_variant"]) == (case, context, product, suite))


def tree_source(item, relative):
    with tarfile.open(HERE / item["tree"]["path"]) as archive:
        member = next(member for member in archive.getmembers()
                      if member.name.endswith("/" + relative))
        return archive.extractfile(member).read().decode()


def actual_assertions(item, relative, function, needle):
    source = tree_source(item, relative)
    function_node = next(node for node in ast.walk(ast.parse(source))
                         if isinstance(node, ast.FunctionDef) and node.name == function)
    return [node for node in ast.walk(function_node)
            if isinstance(node, ast.Assert) and needle in ast.unparse(node)]


# ------------------------------------------------------------------ R1

@pytest.mark.parametrize("case,relative,function,needle,stale_line", [
    ("A", "tests/test_argparse.py", "test_actions_spans_in_usage", "format_help", 390),
    ("B", "tests/test_optparse.py", "test_generated_usage", "format_help", 439),
    ("C", "tests/test_main.py", "test_cli_parse_error_handling",
     "Error parsing reStructuredText", 198),
])
def test_actual_assertion_location_accepted(case, relative, function, needle, stale_line):
    item = row(case=case, context=f"{case}-C1")
    selected = actual_assertions(item, relative, function, needle)[0]
    signature = item["expected"]["failure_signature"]
    actual = {"longrepr": f"{relative}:{selected.lineno}: AssertionError: registered"}
    stale = {"longrepr": f"{relative}:{stale_line}: AssertionError: registered"}
    assert adjudicate.signature_match(actual, signature) is True
    if selected.lineno == stale_line:
        pytest.skip("stale line equals actual on this suite")
    assert adjudicate.signature_match(stale, signature) is False


@pytest.mark.parametrize("node,case", [
    ("tests/test_argparse.py::test_user_usage[no_color_absent-default]", "A"),
    ("tests/test_optparse.py::test_generated_usage[columns_40-indented]", "B"),
    ("tests/test_main.py::test_cli_parse_error_handling[columns_unset]", "C"),
])
def test_native_parameter_order_matched(node, case):
    item = row(case=case, context=f"{case}-C1")
    identity = item["expected"]["identity"]
    assert adjudicate.match_target(node, identity) is not None
    assert adjudicate.match_target(node.replace("columns_unset", "columns_999")
                                   .replace("no_color_absent", "no_color_999")
                                   .replace("columns_40", "columns_999"), identity) is None


def test_registered_signature_gate_exists():
    item = row()
    location = item["expected"]["failure_signature"]["assertion_locations"][0]
    text = f"{location['file']}:{location['lines'][0]}: AssertionError: registered"
    assert adjudicate.signature_match({"longrepr": text},
                                      item["expected"]["failure_signature"]) is True


def test_declared_assertion_locations_resolved_per_tree():
    """M2 无目标节点，断言只以声明登记；行号仍按每棵组合树解析。"""
    readings = {"A": ("tests/test_argparse.py", 393),
                "B": ("tests/test_optparse.py", 444),
                "C": ("tests/test_main.py", 207)}
    for case, (file, actual) in readings.items():
        count = 0
        for suite in ("S0_original", "R1_global_fix", "R2_local_isolation",
                      "R3_explicit_parameterization"):
            item = row(case=case, context=f"{case}-C1", product="M2", suite=suite)
            signature = item["expected"]["failure_signature"]
            assert signature["assertion_locations"], (case, suite)
            for entry in signature["assertion_locations"]:
                assert entry["lines"], (case, suite, entry)
                count += 1
        item = row(case=case, context=f"{case}-C1", product="M2",
                   suite="R3_explicit_parameterization")
        signature = item["expected"]["failure_signature"]
        assert adjudicate.signature_match(
            {"longrepr": f"{file}:{actual}: AssertionError: registered"}, signature)
        assert count >= 4


# ------------------------------------------------------------------ R2

def test_zero_observations_no_equal_pass():
    document = adjudicate.adjudicate(ROWS, {}, SPEC)
    assert document["equal_pass"] == []
    assert document["equal_pass_exclusions"]


def test_control_failure_excluded_from_comparison():
    cells = [{"case": "A", "context": "A-C1", "mutation_id": "A-M1",
              "suite_variant": suite, "applicability": "full",
              "covers_requested_branch": True, "detection": suite == "S0_original",
              "verdict": "RETAINED" if suite != "S0_original" else "S0_REFERENCE",
              "health_reference_usable": True, "witness_detection": True}
             for suite in ("S0_original", "R2_local_isolation")]
    control = [{"case": "A", "context": "A-C1",
                "suite_variant": "R2_local_isolation", "verdict": "CONTROL_FAILURE"}]
    comparison = adjudicate.equal_pass_comparison(cells, control)
    assert comparison == []
    assert any(item.get("reason") == "control_failure" for item in comparison.exclusions)


def test_no_common_mutation_no_comparison():
    cells = [
        {"case": "A", "context": "A-C1", "mutation_id": "A-M1",
         "suite_variant": "S0_original", "applicability": "full", "detection": True,
         "health_reference_usable": True, "witness_detection": True},
        {"case": "A", "context": "A-C1", "mutation_id": "A-M2",
         "suite_variant": "R2_local_isolation", "applicability": "full",
         "covers_requested_branch": True, "detection": False,
         "health_reference_usable": True, "witness_detection": True},
    ]
    comparison = adjudicate.equal_pass_comparison(cells)
    assert comparison == []
    assert any(item.get("reason") == "no_common_mutations"
               for item in comparison.exclusions)


def test_equal_pass_different_kills_positive_control():
    cells = [
        {"case": "A", "context": "A-C1", "mutation_id": "A-M1",
         "suite_variant": "S0_original", "applicability": "full", "detection": True,
         "health_reference_usable": True, "witness_detection": True},
        {"case": "A", "context": "A-C1", "mutation_id": "A-M1",
         "suite_variant": "R2_local_isolation", "applicability": "full",
         "covers_requested_branch": True, "detection": False,
         "health_reference_usable": True, "witness_detection": True},
    ]
    comparison = adjudicate.equal_pass_comparison(cells)
    assert comparison and comparison[0]["identical"] is False
    assert comparison[0]["mutation_set"] == ["A-M1"]


# ------------------------------------------------------------------ R4

def fake_suite_runner(kind, netlog=True):
    def run(argv, cwd, env, timeout):
        if "-c" in argv:
            return {"returncode": 0, "stdout": json.dumps({
                "python": "3.11.9", "implementation": "CPython",
                "flags": {"utf8_mode": 0, "ignore_environment": 0, "isolated": 0},
                "env": dict(env)}), "stderr": "", "timed_out": False,
                "duration_seconds": 0.01, "process_group_cleanup": True}
        meta = json.loads(pathlib.Path(env["PYHOST_ROUND_META"]).read_text())
        rc = 2 if kind == "collection_error" else 0
        records = [{"$report_type": "RuntimeSnapshot", "environment": dict(env),
                    "round_meta": meta}]
        if kind == "collection_error":
            records.append({"$report_type": "CollectReport",
                            "nodeid": "tests/test_broken.py", "outcome": "failed",
                            "longrepr": "ModuleNotFoundError: synthetic"})
        collected = [] if kind == "collection_error" else ["tests/synthetic.py::test_ok"]
        records.append({"$report_type": "CollectionFinished", "collected": collected,
                        "collected_count": len(collected)})
        if kind != "collection_error":
            records.extend({"$report_type": "TestReport",
                            "nodeid": "tests/synthetic.py::test_ok",
                            "when": phase, "outcome": "passed"}
                           for phase in ("setup", "call", "teardown"))
        records.append({"$report_type": "SessionFinished", "exitstatus": rc})
        pathlib.Path(env["PYHOST_JSONL_PATH"]).write_text(
            "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        if netlog:
            pathlib.Path(env["PYHOST_NETLOG"]).write_text(
                json.dumps({"kind": "observer_init", "pid": 1001}) + "\n"
                + json.dumps({"kind": "observer_end", "pid": 1001}) + "\n",
                encoding="utf-8")
        return {"returncode": rc, "stdout": "", "stderr": "", "timed_out": False,
                "duration_seconds": 0.01, "process_group_cleanup": True,
                "child_pid": 1001}
    return run


def run_suite(tmp_path, kind, netlog=True):
    item = row(suite="S0_original")
    run_dir = tmp_path / "run"
    for name in ("home", "tmp", "bin"):
        (run_dir / name).mkdir(parents=True, exist_ok=True)
    return adapter.execute_suite(item, run_dir, pathlib.Path("/synthetic/python"),
                                 fake_suite_runner(kind, netlog))


def test_collection_error_not_usable(tmp_path):
    record = run_suite(tmp_path, "collection_error")
    assert record["status"] == "failed"
    assert "collection_error" in record["problems"]
    assert not adjudicate.row_is_usable(record)


def test_missing_netlog_not_usable(tmp_path):
    record = run_suite(tmp_path, "ordinary", netlog=False)
    assert record["status"] == "failed"
    assert "network_evidence_missing" in record["problems"]
    assert not adjudicate.row_is_usable(record)


def test_ordinary_session_still_usable(tmp_path):
    record = run_suite(tmp_path, "ordinary", netlog=True)
    assert record["status"] == "completed"
    assert record["problems"] == []
    assert adjudicate.row_is_usable(record)


# ------------------------------------------------------------------ prepare

def make_wheel(directory, name, version):
    path = directory / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{name}-{version}.dist-info/METADATA",
                         f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
    return path


def prepare_fixture(tmp_path, installed):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    wheels = [make_wheel(workspace, "pytest", "7.2.0"),
              make_wheel(workspace, "pluggy", "1.0.0")]
    inputs = tmp_path / "inputs"
    inputs.mkdir(exist_ok=True)
    (inputs / "toolchains.json").write_text(json.dumps({"toolchains": {
        "synthetic-project": {
            "pytest": "7.2.0",
            "wheels": [{"path": wheel.name, "filename": wheel.name,
                        "sha256": adapter.sha(wheel),
                        "bytes": wheel.stat().st_size} for wheel in wheels],
            "observer": {}}}}), encoding="utf-8")
    state = tmp_path / "state"
    purelib = tmp_path / "purelib"
    purelib.mkdir(exist_ok=True)

    def mock_subprocess(argv, **kwargs):
        argv = [str(item) for item in argv]
        if "venv" in argv:
            venv_dir = pathlib.Path(argv[-1])
            (venv_dir / "bin").mkdir(parents=True, exist_ok=True)
            (venv_dir / "bin/python").touch()
            return subprocess.CompletedProcess(argv, 0, "", "")
        if len(argv) > 2 and argv[1] == "-c":
            code = argv[2]
            if "sysconfig" in code:
                return subprocess.CompletedProcess(argv, 0, str(purelib), "")
            if "m.distributions()" in code:
                return subprocess.CompletedProcess(argv, 0, json.dumps(installed), "")
            if "pytest11" in code:
                return subprocess.CompletedProcess(argv, 0, "[]", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    return workspace, inputs, state, mock_subprocess


def run_prepare(tmp_path, installed, calls=None):
    workspace, inputs, state, mock = prepare_fixture(tmp_path, installed)

    def counted(argv, **kwargs):
        if calls is not None:
            calls.append([str(item) for item in argv])
        return mock(argv, **kwargs)

    with (patch.object(adapter, "WS", workspace),
          patch.object(adapter, "INPUTS", inputs),
          patch.object(adapter, "FROZEN_INTERPRETER", pathlib.Path("/synthetic/python")),
          patch.object(adapter, "interpreter_record",
                       return_value={"path": "/synthetic/python", "version": "3.11.9",
                                     "sha256": "synthetic-interpreter"}),
          patch.object(adapter.subprocess, "run", side_effect=counted)):
        return adapter.prepare(state)


def test_prepare_accepts_exact_set_and_reuses(tmp_path):
    installed = {"pytest": "7.2.0", "pluggy": "1.0.0", "pyhost-observer": "0.2.0"}
    first = run_prepare(tmp_path, installed)
    project = first["projects"]["synthetic-project"]
    assert project["reused"] is False
    assert project["installed"]["pytest"] == "7.2.0"
    second = run_prepare(tmp_path, installed)
    assert second["projects"]["synthetic-project"]["reused"] is True


def test_prepare_rejects_extra_missing_and_wrong_version(tmp_path):
    for installed in (
        {"pytest": "7.2.0", "pluggy": "1.0.0", "pyhost-observer": "0.2.0",
         "unexplained-extra": "99.0"},
        {"pytest": "7.2.0", "pyhost-observer": "0.2.0"},
        {"pytest": "7.2.0", "pluggy": "9.9.9", "pyhost-observer": "0.2.0"},
    ):
        case = tmp_path / ("case-" + "-".join(sorted(installed)))
        with pytest.raises(RuntimeError) as error:
            run_prepare(case, installed)
        assert ("install_set_mismatch" in str(error.value)
                or "missing" in str(error.value)
                or "extra" in str(error.value))


# ------------------------------------------------------ full 240 consumer

def test_full_240_dispatch_and_resume(tmp_path):
    state = tmp_path / "state"
    tools = json.loads((HERE / "inputs/toolchains.json").read_text(encoding="utf-8"))["toolchains"]
    for project in tools:
        python = state / "venvs" / project / "venv/bin/python"
        python.parent.mkdir(parents=True, exist_ok=True)
        python.touch()
    clearance = tmp_path / "clearance.json"
    clearance.write_text(json.dumps({
        "batch_id": adapter.BATCH_ID, "authorized": True,
        "execution_authorized": True, "signed_by": "synthetic-control",
        "commitment_sha256": adapter.sha(adapter.COMMITMENT_PATH),
        "rows_sha256": adapter.rows_hash(ROWS), "counts": ROWS["counts"]}),
        encoding="utf-8")
    calls = []

    def consume(argv, cwd, env, timeout):
        argv = [str(item) for item in argv]
        calls.append(argv)
        common = {"returncode": 0, "stdout": "", "stderr": "", "timed_out": False,
                  "duration_seconds": 0.01, "process_group_cleanup": True,
                  "child_pid": 1001}
        if "-c" in argv:
            return dict(common, stdout=json.dumps({
                "python": "3.11.9", "implementation": "CPython",
                "flags": {"utf8_mode": 0, "ignore_environment": 0, "isolated": 0},
                "env": dict(env)}))
        if "--json-out" in argv:
            out = pathlib.Path(argv[argv.index("--json-out") + 1])
            case = argv[argv.index("--case") + 1]
            out.write_text(json.dumps({"case": case, "product_root": str(cwd),
                                       "errors": {}, "observations": {}}),
                           encoding="utf-8")
            return common
        meta = json.loads(pathlib.Path(env["PYHOST_ROUND_META"]).read_text())
        records = [{"$report_type": "RuntimeSnapshot", "environment": dict(env),
                    "round_meta": meta},
                   {"$report_type": "CollectionFinished",
                    "collected": ["tests/synthetic.py::test_ok"],
                    "collected_count": 1},
                   {"$report_type": "SessionFinished", "exitstatus": 0}]
        records.extend({"$report_type": "TestReport",
                        "nodeid": "tests/synthetic.py::test_ok",
                        "when": phase, "outcome": "passed"}
                       for phase in ("setup", "call", "teardown"))
        pathlib.Path(env["PYHOST_JSONL_PATH"]).write_text(
            "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        pathlib.Path(env["PYHOST_NETLOG"]).write_text(
            json.dumps({"kind": "observer_init", "pid": 1001}) + "\n"
            + json.dumps({"kind": "observer_end", "pid": 1001}) + "\n",
            encoding="utf-8")
        return common

    def invoke():
        with (patch.object(adapter, "_popen", side_effect=consume),
              patch.object(sys, "argv", ["s3_adapter.py", "--run",
                                         "--state-root", str(state),
                                         "--clearance", str(clearance)]),
              contextlib.redirect_stdout(io.StringIO())):
            return adapter.main()

    first = invoke()
    count = len(calls)
    second = invoke()
    ledger = json.loads((state / "ledger.json").read_text())
    summary = json.loads((state / "summary.json").read_text())
    assert first == 0 and second == 0
    assert len(ledger["records"]) == 240
    assert len(calls) == count
    assert summary["complete"] is True
    assert summary["not_executed"] == 0
