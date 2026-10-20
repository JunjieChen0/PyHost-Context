"""ClawsGO Science Agent：S1b 薄适配器的正反例测试。

不安装、不导入、不执行任何候选；环境与运行器一律用假对象。
"""
from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import s1b_adapter as adapter  # noqa: E402

adapter._ensure_paths()


def temp_file(directory, name, data):
    path = pathlib.Path(directory) / name
    path.write_bytes(data)
    return path


def toolchain_for(files, expected, profile="D-R1", pytest_version="9.1.1", extra=()):
    return {"project": "demo", "version": "1.0", "profile": profile,
            "pytest_version": pytest_version, "runner_profile": "s1b/test",
            "install_files": files, "expected_distributions": expected,
            "plugin_loads": ["asyncio"] if profile.startswith("D-") else [],
            "extra_pytest_args": list(extra)}


class InputsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roster, cls.toolchains, cls.schedule, cls.commitment = adapter.load_inputs()

    def test_real_inputs_validate(self):
        self.assertEqual([], adapter.validate_inputs(self.roster, self.toolchains,
                                                     self.schedule))

    def test_real_commitment_verifies(self):
        self.assertEqual([], adapter.verify_commitment(self.commitment))

    def test_roster_order_is_frozen(self):
        bad = copy.deepcopy(self.roster)
        bad["projects"] = list(reversed(bad["projects"]))
        self.assertIn("roster_order", adapter.validate_inputs(bad, self.toolchains,
                                                              self.schedule))

    def test_schedule_counts_and_ids(self):
        entries = self.schedule["entries"]
        self.assertEqual(450, len(entries))
        self.assertEqual(225, sum(e["arm"] == "main" for e in entries))
        self.assertEqual(225, sum(e["arm"] == "control" for e in entries))
        self.assertEqual(450, len({e["run_id"] for e in entries}))
        self.assertFalse(any(e["run_id"].startswith("s1b-main") and e["arm"] != "main"
                             for e in entries))
        self.assertEqual(list(range(1, 451)), [e["order"] for e in entries])

    def test_schedule_hash_and_labels(self):
        self.assertEqual([], adapter.validate_inputs(self.roster, self.toolchains,
                                                     self.schedule))
        schedule = copy.deepcopy(self.schedule)
        schedule["entries"][0]["label"] = "tampered"
        self.assertIn("schedule_sha256", adapter.validate_inputs(
            self.roster, self.toolchains, schedule))

    def test_commitment_tamper(self):
        commitment = copy.deepcopy(self.commitment)
        key = sorted(commitment["files"])[0]
        commitment["files"][key] = "0" * 64
        problems = adapter.verify_commitment(commitment)
        self.assertTrue(any(p.startswith("drift:") for p in problems))
        self.assertIn("commitment_aggregate", problems)

    def test_reference_profiles_keep_s1a_toolchain(self):
        for project in ("typer", "pytest-xdist"):
            tool = self.toolchains["projects"][project]
            self.assertEqual("S1a", tool["profile"])
            self.assertEqual("7.2.0", tool["pytest_version"])
            self.assertEqual([], tool["plugin_loads"])
            self.assertEqual([], tool["extra_pytest_args"])

    def test_mechanism_profiles(self):
        expected = {"structlog": ["asyncio", "randomly", "time_machine"],
                    "rich-toolkit": ["anyio", "inline_snapshot"],
                    "twine": ["socket"]}
        for project, loads in expected.items():
            self.assertEqual(loads, self.toolchains["projects"][project]["plugin_loads"])


class TransitionTests(unittest.TestCase):
    def evaluate(self, profile="D-R1", pytest_version="9.1.1", launch=None,
                 post=None, snapshot=None):
        tool = {"profile": profile, "pytest_version": pytest_version}
        launch = {} if launch is None else launch
        post = {} if post is None else post
        snapshot = {"PYTEST_VERSION": "9.1.1"} if snapshot is None else snapshot
        before = copy.deepcopy((launch, post, snapshot))
        result = adapter.evaluate_transition(tool, "B0", {}, launch, post, snapshot)
        self.assertEqual(before, (launch, post, snapshot))
        return result

    def test_exact_addition_accepted_for_D_R1(self):
        result = self.evaluate()
        self.assertTrue(result["valid"])
        self.assertTrue(result["runner_metadata_transition_accepted"])
        self.assertFalse(result["raw_assessment"]["valid"])

    def test_raw_delta_retained(self):
        result = self.evaluate()
        self.assertEqual(["PYTEST_VERSION"], result["post_init_vs_pre_collection_delta"])

    def test_wrong_value_refused(self):
        self.assertFalse(self.evaluate(snapshot={"PYTEST_VERSION": "9.2.0"})["valid"])

    def test_wrong_declared_version_refused(self):
        self.assertFalse(self.evaluate(pytest_version="9.2.0")["valid"])

    def test_preseeded_key_refused(self):
        self.assertFalse(self.evaluate(launch={"PYTEST_VERSION": "old"},
                                       post={"PYTEST_VERSION": "old"})["valid"])

    def test_other_drift_still_refused(self):
        result = self.evaluate(snapshot={"PYTEST_VERSION": "9.1.1", "COLUMNS": "40"})
        self.assertFalse(result["valid"])
        self.assertIn("COLUMNS", result["remaining_post_init_vs_pre_collection_delta"])

    def test_missing_snapshot_refused(self):
        result = adapter.evaluate_transition({"profile": "D-R1", "pytest_version": "9.1.1"},
                                             "B0", {}, {}, {}, None)
        self.assertFalse(result["valid"])

    def test_D_R0_never_uses_amendment(self):
        self.assertTrue(self.evaluate(profile="D-R0", pytest_version="7.2.0",
                                      snapshot={})["valid"])
        self.assertFalse(self.evaluate(profile="D-R0", pytest_version="7.2.0",
                                       snapshot={"PYTEST_VERSION": "9.1.1"})["valid"])

    def test_S1a_never_uses_amendment(self):
        self.assertTrue(self.evaluate(profile="S1a", pytest_version="7.2.0",
                                      snapshot={})["valid"])
        self.assertFalse(self.evaluate(profile="S1a", pytest_version="7.2.0",
                                       snapshot={"PYTEST_VERSION": "9.1.1"})["valid"])


class ClearanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roster, cls.toolchains, cls.schedule, cls.commitment = adapter.load_inputs()

    def document(self):
        return {
            "schema_version": "pyhost-context/reviewer001-S1b-clearance/1.0",
            "decision": adapter.DECISION,
            "signed_by": "synthetic-test-fixture",
            "signed_at_utc": None,
            "authorization": {"S1b_execution": True, "S2": False, "S3": False,
                              "repro": False, "new_perturbation_runs": False},
            "scope": {"state_root": adapter.STATE_ROOT_REL, "batch_id": adapter.BATCH, "total_runs": 450,
                      "main_runs": 225, "control_runs": 225,
                      "schedule_sha256": self.commitment["schedule_sha256"]},
            "pre_run_commitment_hash": self.commitment["pre_run_commitment_hash"],
            "bindings": self.commitment["files"],
        }

    def test_wellformed_document_only_lacks_real_signature(self):
        problems = adapter.validate_clearance(self.document(), self.commitment,
                                              self.commitment["files"])
        self.assertEqual(["signed_by", "signed_at_utc"], problems)

    def test_wrong_decision(self):
        doc = self.document(); doc["decision"] = "DRAFT"
        self.assertIn("decision", adapter.validate_clearance(doc, self.commitment,
                                                             self.commitment["files"]))

    def test_unbounded_scope(self):
        doc = self.document(); doc["authorization"]["S2"] = True
        self.assertIn("scope_not_bounded:S2",
                      adapter.validate_clearance(doc, self.commitment,
                                                  self.commitment["files"]))

    def test_wrong_schedule_binding(self):
        doc = self.document(); doc["scope"]["schedule_sha256"] = "0" * 64
        self.assertIn("scope_schedule",
                      adapter.validate_clearance(doc, self.commitment,
                                                  self.commitment["files"]))

    def test_binding_drift(self):
        doc = self.document()
        doc["bindings"] = dict(doc["bindings"])
        doc["bindings"][sorted(doc["bindings"])[0]] = "0" * 64
        self.assertIn("binding_drift",
                      adapter.validate_clearance(doc, self.commitment,
                                                  self.commitment["files"]))

    def test_missing_clearance_file_refused(self):
        with self.assertRaises(adapter.Refused):
            adapter.gate(HERE / "no-such-clearance.json", self.commitment,
                         self.commitment["files"])

    def test_synthetic_file_is_not_a_signature(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "clearance.json"
            path.write_text(json.dumps(self.document()), encoding="utf-8")
            with self.assertRaises(adapter.Refused):
                adapter.gate(path, self.commitment, self.commitment["files"])


class InstallTests(unittest.TestCase):
    def test_file_pinned_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            first = temp_file(directory, "a-1.0-py3-none-any.whl", b"a")
            second = temp_file(directory, "b-2.0-py3-none-any.whl", b"b")
            files = [{"filename": first.name, "path": str(first),
                      "sha256": adapter.sha(first), "bytes": first.stat().st_size},
                     {"filename": second.name, "path": str(second),
                      "sha256": adapter.sha(second), "bytes": second.stat().st_size}]
            tool = toolchain_for(files, {"demo": "1.0", "pyhost-observer": "0.2.0"})
            plan = adapter.install_plan(tool)
            self.assertEqual([str(first), str(second)], plan)
            argv = adapter.install_argv("/v/bin/python", plan)
            for flag in ("--offline", "--no-index", "--no-deps"):
                self.assertIn(flag, argv)
            self.assertEqual("copy", argv[argv.index("--link-mode") + 1])
            self.assertEqual(plan, argv[-2:])
            self.assertFalse(any("==" in item for item in argv if not item.endswith(".whl")))

    def test_missing_file_refused(self):
        tool = toolchain_for([{"filename": "x-1.0.whl", "path": "/nonexistent/x-1.0.whl",
                               "sha256": "0" * 64, "bytes": 1}], {"demo": "1.0"})
        with self.assertRaises(adapter.Refused):
            adapter.install_plan(tool)

    def test_hash_drift_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = temp_file(directory, "x-1.0-py3-none-any.whl", b"x")
            tool = toolchain_for([{"filename": path.name, "path": str(path),
                                   "sha256": "0" * 64, "bytes": path.stat().st_size}],
                                 {"demo": "1.0"})
            with self.assertRaises(adapter.Refused):
                adapter.install_plan(tool)


class VenvTests(unittest.TestCase):
    def test_exact_set_required_and_profile_written(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            wheel = temp_file(root, "demo-1.0-py3-none-any.whl", b"demo")
            files = [{"filename": wheel.name, "path": str(wheel),
                      "sha256": adapter.sha(wheel), "bytes": wheel.stat().st_size}]
            expected = {"demo": "1.0", "pyhost-observer": "0.2.0"}
            tool = toolchain_for(files, expected)
            venv = root / "venvs/demo/venv"
            (venv / "bin").mkdir(parents=True)
            (venv / "bin/python").write_bytes(b"")

            class Runner:
                def __init__(self, inventory_lines):
                    self.calls = []
                    self.inventory_lines = inventory_lines

                def __call__(self, argv, **kwargs):
                    self.calls.append(argv)
                    class Result:
                        returncode = 0
                        stdout = ""
                        stderr = ""
                    result = Result()
                    if argv[0].endswith("bin/python"):
                        code = argv[-1]
                        if "platform.python_version" in code:
                            result.stdout = "3.11.9"
                        elif "importlib.metadata" in code:
                            result.stdout = "\n".join(self.inventory_lines)
                    return result

            config = venv / "lib/python3.11/site-packages/_pytest/config/__init__.py"
            config.parent.mkdir(parents=True)
            config.write_bytes((HERE / "inputs/pytest-9.1.1-config.py").read_bytes())
            runner = Runner(["demo==1.0", "pyhost-observer==0.2.0"])
            record = adapter.ensure_venv("demo", tool, root / "venvs", runner=runner,
                                         base_python="/usr/bin/python3",
                                         observer_install=lambda venvpy: {"ok": True})
            self.assertEqual(expected, record["installed"])
            install = next(argv for argv in runner.calls if argv[0] == "uv"
                           and "install" in argv)
            self.assertIn(str(wheel), install)
            self.assertTrue((root / "venvs/demo/profile.json").is_file())

    def test_mismatched_final_set_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            wheel = temp_file(root, "demo-1.0-py3-none-any.whl", b"demo")
            files = [{"filename": wheel.name, "path": str(wheel),
                      "sha256": adapter.sha(wheel), "bytes": wheel.stat().st_size}]
            tool = toolchain_for(files, {"demo": "1.0", "pyhost-observer": "0.2.0"})
            venv = root / "venvs/demo/venv"
            (venv / "bin").mkdir(parents=True)
            (venv / "bin/python").write_bytes(b"")

            class Runner:
                def __call__(self, argv, **kwargs):
                    class Result:
                        returncode = 0
                        stderr = ""
                    result = Result()
                    if argv[0].endswith("bin/python"):
                        code = argv[-1]
                        if "platform.python_version" in code:
                            result.stdout = "3.11.9"
                        elif "importlib.metadata" in code:
                            result.stdout = "demo==9.9\n"
                        else:
                            result.stdout = ""
                    else:
                        result.stdout = ""
                    return result

            with self.assertRaises(adapter.TechnicalFailure):
                adapter.ensure_venv("demo", tool, root / "venvs", runner=Runner(),
                                    base_python="/usr/bin/python3",
                                    observer_install=lambda venvpy: {"ok": True})


class AssessmentTests(unittest.TestCase):
    def test_run_assessment_reads_three_views(self):
        from matrix_runner import launch, slots as slots_mod
        b0 = json.loads((adapter.PROJECT / "admission_freeze/b0_environment.json")
                        .read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            context = {"run_root": str(pathlib.Path(directory) / "run"),
                       "source_path": str(pathlib.Path(directory) / "src"),
                       "logs": str(pathlib.Path(directory) / "logs")}
            pathlib.Path(context["logs"]).mkdir(parents=True)
            pathlib.Path(context["source_path"]).mkdir(parents=True)
            slots = slots_mod.ensure_slots(context["run_root"])
            spec = launch.build_round_spec(
                b0, None, {"<frozen-run-root>": context["run_root"],
                           "<project-cwd>": context["source_path"]}, slots)
            env = launch.materialize(spec)
            (pathlib.Path(context["logs"]) / "r1_boot.stdout.txt").write_text(
                json.dumps({"post_init_env": env, "flags": {"ignore_environment": 0, "isolated": 0}}) + "\n", encoding="utf-8")
            snapshot = dict(env); snapshot["PYTEST_VERSION"] = "9.1.1"
            pathlib.Path(slots["jsonl"]).write_text(
                json.dumps({"$report_type": "RuntimeSnapshot",
                            "environment": snapshot}) + "\n", encoding="utf-8")
            archived = slots_mod.clear_slots(slots, pathlib.Path(context["logs"]), "r1")
            payload = {"archived_slots": archived, "jsonl_sha256": archived["jsonl"]["sha256"]}
            entry = {"run_id": "r1", "condition": "B0"}
            amended = adapter.run_assessment({"profile": "D-R1", "pytest_version": "9.1.1",
                                              "project": "demo"}, entry, context, b0,
                                             None, payload)
            self.assertTrue(amended["valid"])
            self.assertTrue(amended["runner_metadata_transition_accepted"])
            original = adapter.run_assessment({"profile": "D-R0", "pytest_version": "7.2.0",
                                               "project": "demo"}, entry, context, b0,
                                              None, payload)
            self.assertFalse(original["valid"])


class PluginTests(unittest.TestCase):
    def test_declared_plugins_must_exist(self):
        tool = toolchain_for([], {"demo": "1.0"}, profile="D-R1")
        check = adapter.check_plugin_declarations(tool, {"asyncio", "randomly"})
        self.assertEqual(["asyncio"], check["declared"])

    def test_missing_plugin_refused(self):
        tool = toolchain_for([], {"demo": "1.0"}, profile="D-R1")
        with self.assertRaises(adapter.TechnicalFailure):
            adapter.check_plugin_declarations(tool, {"randomly"})

    def test_reference_profile_has_no_declared_plugins(self):
        tool = toolchain_for([], {"demo": "1.0"}, profile="S1a", pytest_version="7.2.0")
        check = adapter.check_plugin_declarations(tool, {"asyncio"})
        self.assertEqual([], check["declared"])


class ExecutionGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roster, cls.toolchains, cls.schedule, cls.commitment = adapter.load_inputs()

    def test_run_requires_clearance_before_any_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(adapter.Refused):
                adapter.run_schedule(self.roster, self.toolchains, self.schedule,
                                     self.commitment, state_root=pathlib.Path(directory),
                                     clearance=pathlib.Path(directory) / "missing.json",
                                     runner=lambda **kwargs: self.fail("不得派发"),
                                     prepare=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
