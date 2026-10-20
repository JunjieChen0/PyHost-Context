"""ClawsGO Science Agent：用合成 fixture 检验 B0 运行器逻辑，不运行任何候选项目。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from b0_runner import AdmissionNotAuthorized, authorization_errors, build_b0_snapshot, check_authorized, run_b0
from env_state import (build_launch_env, env_key_record, file_state, file_state_hash,
                       launch_env_diff, three_state)
from pytest_jsonl import (build_per_test, collection_info, failure_signature,
                          identity_fingerprint, parse_jsonl, phase_states, with_root_cause)
from timeout_ledger import TimeoutLedger


def report(node, when, outcome, **extra):
    record = {"$report_type": "TestReport", "nodeid": node, "when": when, "outcome": outcome}
    record.update(extra)
    return record


class PhaseStateTests(unittest.TestCase):
    def test_setup_failure_makes_call_not_run(self):
        records = [
            report("tests/sample.py::test_a", "setup", "failed", longrepr="boom"),
            report("tests/sample.py::test_a", "teardown", "passed"),
        ]
        states = phase_states(records, ["tests/sample.py::test_a"])["tests/sample.py::test_a"]
        self.assertEqual(states["setup"], "reported")
        self.assertEqual(states["call"], "not_run")
        self.assertEqual(states["teardown"], "reported")

    def test_setup_skip_makes_call_not_run(self):
        records = [report("t::a", "setup", "skipped", longrepr="skip")]
        self.assertEqual(phase_states(records, ["t::a"])["t::a"]["call"], "not_run")

    def test_report_missing_when_process_interrupted(self):
        self.assertEqual(
            phase_states([], ["t::a"])["t::a"],
            {"setup": "report_missing", "call": "report_missing", "teardown": "report_missing"})

    def test_passed_setup_without_call_is_report_missing(self):
        records = [report("t::a", "setup", "passed")]
        self.assertEqual(phase_states(records, ["t::a"])["t::a"]["call"], "report_missing")


class PerTestTests(unittest.TestCase):
    def test_phase_states_and_derived_labels(self):
        records = [
            report("t::xf", "setup", "passed"),
            report("t::xf", "call", "skipped", wasxfail="known bug"),
            report("t::xf", "teardown", "passed"),
            report("t::xp", "setup", "passed"),
            report("t::xp", "call", "passed", wasxfail="should fail"),
            report("t::xp", "teardown", "passed"),
        ]
        per = build_per_test(records, ["t::xf", "t::xp"])
        self.assertIn("xfailed", per["t::xf"]["derived_labels"])
        self.assertIn("xpassed", per["t::xp"]["derived_labels"])
        self.assertNotIn("incomplete", per["t::xf"]["derived_labels"])
        self.assertEqual(per["t::xf"]["phases"]["call"], "reported")

    def test_strict_xpass_keeps_native_failure_and_longrepr(self):
        # native 严格 XPASS：call outcome=failed + 原 longrepr，且无 wasxfail。
        records = [
            report("t::s", "setup", "passed"),
            report("t::s", "call", "failed", longrepr="[XPASS(strict)] strict reason"),
            report("t::s", "teardown", "passed"),
        ]
        per = build_per_test(records, ["t::s"])
        record = per["t::s"]
        self.assertEqual(record["raw_outcome"], "failed")
        self.assertIn("xpassed_strict", record["derived_labels"])
        self.assertNotIn("xpassed", record["derived_labels"])
        self.assertIn("[XPASS(strict)]", record["longrepr"])
        self.assertFalse(record["wasxfail"])

    def test_setup_error_labeled_error_not_incomplete(self):
        records = [
            report("t::e", "setup", "failed", longrepr="boom"),
            report("t::e", "teardown", "passed"),
        ]
        per = build_per_test(records, ["t::e"])
        self.assertIn("error", per["t::e"]["derived_labels"])
        self.assertNotIn("incomplete", per["t::e"]["derived_labels"])
        self.assertEqual(per["t::e"]["phases"]["call"], "not_run")
        self.assertEqual(per["t::e"]["root_cause"], "unresolved")

    def test_missing_teardown_labeled_incomplete(self):
        records = [report("t::i", "setup", "passed"), report("t::i", "call", "passed")]
        per = build_per_test(records, ["t::i"])
        self.assertIn("incomplete", per["t::i"]["derived_labels"])
        self.assertEqual(per["t::i"]["phases"]["teardown"], "report_missing")

    def test_identity_changed_coexists_with_failure(self):
        records = [
            report("t::new", "setup", "passed"),
            report("t::new", "call", "failed", longrepr="assert 1 == 2"),
            report("t::new", "teardown", "passed"),
        ]
        per = build_per_test(records, ["t::new"], baseline_nodeids=["t::old"])
        record = per["t::new"]
        self.assertTrue(record["identity_changed"])
        self.assertEqual(record["raw_outcome"], "failed")
        self.assertEqual(record["phases"]["call"], "reported")

    def test_root_cause_defaults_to_unresolved_and_validates(self):
        per = build_per_test([report("t::a", "setup", "passed")], ["t::a"])
        self.assertEqual(per["t::a"]["root_cause"], "unresolved")
        self.assertEqual(with_root_cause(per["t::a"], "nonsense")["root_cause"], "unresolved")
        self.assertEqual(with_root_cause(per["t::a"], "state_pollution_supported")["root_cause"],
                         "state_pollution_supported")


class StageIntegrityTests(unittest.TestCase):
    def test_duplicate_stage_not_complete(self):
        records = [
            report("t::a", "setup", "passed"),
            report("t::a", "call", "passed"),
            report("t::a", "call", "passed"),
            report("t::a", "teardown", "passed"),
        ]
        record = build_per_test(records, ["t::a"])["t::a"]
        self.assertIn("duplicate_stage", record["stage_integrity"])
        self.assertFalse(record["raw_complete"])

    def test_illegal_stage_not_complete(self):
        records = [
            report("t::a", "setup", "passed"),
            report("t::a", "call", "passed"),
            report("t::a", "teardown", "passed"),
            report("t::a", "teardown_x", "passed"),
        ]
        record = build_per_test(records, ["t::a"])["t::a"]
        self.assertIn("illegal_stage", record["stage_integrity"])
        self.assertFalse(record["raw_complete"])

    def test_missing_call_after_passed_setup_not_complete(self):
        records = [report("t::a", "setup", "passed"), report("t::a", "teardown", "passed")]
        record = build_per_test(records, ["t::a"])["t::a"]
        self.assertIn("missing_call", record["stage_integrity"])
        self.assertFalse(record["raw_complete"])

    def test_complete_when_all_phases_present(self):
        records = [
            report("t::a", "setup", "passed"),
            report("t::a", "call", "passed"),
            report("t::a", "teardown", "passed"),
        ]
        record = build_per_test(records, ["t::a"])["t::a"]
        self.assertEqual(record["stage_integrity"], [])
        self.assertTrue(record["raw_complete"])

    def test_phase_details_keep_raw_fields(self):
        records = [report("t::a", "call", "failed", longrepr="boom", wasxfail="why")]
        record = build_per_test(records, ["t::a"])["t::a"]
        self.assertEqual(record["phase_details"]["call"]["outcome"], "failed")
        self.assertEqual(record["phase_details"]["call"]["longrepr"], "boom")
        self.assertEqual(record["phase_details"]["call"]["wasxfail"], "why")


class SignatureTests(unittest.TestCase):
    def test_failure_signature_is_conservative(self):
        raw = (
            "E  AssertionError: assert 4 == 3\n"
            "E    when=2026-10-14T12:00:00+08:00 tz=Asia/Shanghai\n"
            "E    expected 3 actual 4 color=red token=0xdeadbeef elapsed=0.003s\n"
            "E    object <Foo object at 0x7f9c2ab1c0d0>\n"
            "E    file /tmp/pytest-of-user/pytest-12/test_t0/f.txt")
        signature = failure_signature(raw, run_dir_prefixes=["/tmp/pytest-of-user/pytest-12"])
        # 语义值一律保留，包括非 repr 的 0x 值与时长样式值。
        self.assertIn("2026-10-14T12:00:00+08:00", signature)
        self.assertIn("Asia/Shanghai", signature)
        self.assertIn("expected 3 actual 4", signature)
        self.assertIn("color=red", signature)
        self.assertIn("token=0xdeadbeef", signature)
        self.assertIn("0.003s", signature)
        # 仅显式对象 repr 地址与已登记运行目录前缀被替换。
        self.assertNotIn("0x7f9c2ab1c0d0", signature)
        self.assertNotIn("/tmp/pytest-of-user/pytest-12/test_t0/f.txt", signature)
        self.assertIn("<object_address>", signature)
        self.assertIn("<run_dir>", signature)
        self.assertIsNone(failure_signature(None))

    def test_failure_signature_without_registered_prefix_keeps_path(self):
        raw = "E    file /tmp/pytest-of-user/pytest-12/test_t0/f.txt"
        self.assertIn("/tmp/pytest-of-user", failure_signature(raw))


class CollectionTests(unittest.TestCase):
    def test_collection_info_order_and_identity(self):
        records = [
            {"$report_type": "CollectReport", "nodeid": "t::a", "outcome": "passed"},
            {"$report_type": "CollectReport", "nodeid": "t::b", "outcome": "passed"},
            {"$report_type": "CollectReport", "nodeid": "t::broken", "outcome": "failed", "longrepr": "syntax"},
        ]
        info = collection_info(records, baseline_nodeids=["t::a"])
        self.assertEqual(info["collected"], ["t::a", "t::b"])
        self.assertEqual(info["errors"][0]["nodeid"], "t::broken")
        self.assertIn("t::b", info["added"])
        self.assertNotEqual(info["identity_fingerprint"], identity_fingerprint(["t::a"]))

    def test_parse_jsonl_reports_malformed_lines(self):
        records, malformed = parse_jsonl('{"a": 1}\nnot json\n\n')
        self.assertEqual(len(records), 1)
        self.assertEqual(malformed[0]["line"], 2)


class EnvStateTests(unittest.TestCase):
    def test_three_state_distinguishes_unset_empty_set(self):
        env = {"A": "", "B": "x"}
        self.assertEqual(three_state(env, "A"), {"state": "empty"})
        self.assertEqual(three_state(env, "B"), {"state": "set", "value": "x"})
        self.assertEqual(three_state(env, "C"), {"state": "unset"})

    def test_launch_env_diff_exactly_one_key(self):
        spec = {
            "TZ": {"state": "set", "value": "UTC"},
            "LANG": {"state": "set", "value": "C.UTF-8"},
            "NO_COLOR": {"state": "unset"},
        }
        actual = {"TZ": "UTC", "LANG": "C.UTF-8", "NO_COLOR": "1"}
        diff = launch_env_diff(spec, actual, ["TZ", "LANG", "NO_COLOR"])
        self.assertEqual([item["key"] for item in diff], ["NO_COLOR"])
        self.assertEqual(diff[0]["expected"], {"state": "unset"})
        self.assertEqual(diff[0]["actual"], {"state": "set", "value": "1"})

    def test_direct_environment_path_runtime_effect_not_applicable(self):
        self.assertEqual(env_key_record("PATH")["runtime_effect"], "not_applicable")
        self.assertEqual(env_key_record("PYTHONPATH")["runtime_effect"], "not_applicable")
        self.assertEqual(env_key_record("TZ")["runtime_effect"], "applicable")

    def test_build_launch_env_rejects_whitelist_overflow(self):
        with self.assertRaises(ValueError):
            build_launch_env(["TZ"], {"PATH": {"state": "set", "value": "/bin"}}, {})

    def test_build_launch_env_applies_three_states(self):
        env = build_launch_env(
            ["PATH", "TZ", "NO_COLOR"],
            {"TZ": {"state": "set", "value": "UTC"}, "NO_COLOR": {"state": "unset"}},
            {"PATH": "/usr/bin"})
        self.assertEqual(env, {"PATH": "/usr/bin", "TZ": "UTC"})

    def test_file_state_separate_from_launch_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            source.mkdir()
            (source / "mod.py").write_text("x = 1\n")
            state = file_state(source)
            self.assertEqual(list(state), ["mod.py"])
            before = file_state_hash(state)
            self.assertEqual(file_state_hash(file_state(source)), before)


class SnapshotTests(unittest.TestCase):
    def _config(self, source):
        return {
            "source": str(source),
            "launch_whitelist": ["PATH", "TZ", "LANG", "NO_COLOR"],
            "baseline_condition": {
                "TZ": {"state": "set", "value": "UTC"},
                "LANG": {"state": "set", "value": "C.UTF-8"},
                "NO_COLOR": {"state": "unset"},
            },
            "base_values": {"PATH": "/usr/bin"},
            "probe_entry_argv": ["python3.11", "-c", "import sys; print(sys.version_info)"],
            "test_entry_argv": ["python3.11", "-m", "pytest", "tests", "-p", "no:cacheprovider"],
        }

    def test_probe_and_test_entry_argv_are_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            source.mkdir()
            (source / "mod.py").write_text("x = 1\n")
            snapshot = build_b0_snapshot(self._config(source))
            self.assertNotEqual(snapshot["probe_entry_argv"], snapshot["test_entry_argv"])
            self.assertEqual(snapshot["probe_entry_argv"][0], "python3.11")
            self.assertIn("pytest", snapshot["test_entry_argv"])

    def test_snapshot_keeps_file_state_and_environment_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            source.mkdir()
            (source / "mod.py").write_text("x = 1\n")
            snapshot = build_b0_snapshot(self._config(source))
            self.assertNotIn("launch_env_diff", snapshot["file_state"])
            self.assertEqual(snapshot["launch_env_diff"], [])
            self.assertEqual(snapshot["launch_env_three_state"]["NO_COLOR"], {"state": "unset"})


class TimeoutLedgerTests(unittest.TestCase):
    def test_timeout_preserves_raw_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = TimeoutLedger(boundary=1, log_dir=tmp)
            record = ledger.run(
                [sys.executable, "-u", "-c", "print('before-timeout'); import time; time.sleep(30)"],
                cwd=tmp, env=dict(os.environ), stage="sleep_case", timeout=1)
            self.assertTrue(record["timed_out"])
            self.assertEqual(record["timeout_seconds"], 1)
            self.assertEqual(record["signal"], 9)
            self.assertLess(record["duration_seconds"], 5)
            self.assertIn("before-timeout", Path(record["stdout"]).read_text())

    def test_normal_completion_is_not_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = TimeoutLedger(boundary=30, log_dir=tmp)
            record = ledger.run([sys.executable, "-c", "print('done')"],
                                cwd=tmp, env=dict(os.environ), stage="ok_case")
            self.assertFalse(record["timed_out"])
            self.assertEqual(record["returncode"], 0)
            self.assertEqual(len(ledger.entries), 1)


class AuthorizationTests(unittest.TestCase):
    def test_authorization_errors_lists_untrue_flags(self):
        self.assertEqual(authorization_errors({}),
                         ["admission_phase_authorized", "admission_rules_frozen"])
        self.assertEqual(authorization_errors({"admission_phase_authorized": 1,
                                               "admission_rules_frozen": True}),
                         ["admission_phase_authorized"])
        self.assertEqual(authorization_errors({"admission_phase_authorized": True,
                                               "admission_rules_frozen": True}), [])

    def test_run_b0_refuses_when_unauthorized(self):
        result = run_b0({"admission_phase_authorized": False, "admission_rules_frozen": False})
        self.assertFalse(result["executed"])
        self.assertEqual(result["status"], "refused_not_authorized")
        self.assertEqual(result["missing_authorization"],
                         ["admission_phase_authorized", "admission_rules_frozen"])

    def test_run_b0_refuses_with_partial_authorization(self):
        result = run_b0({"admission_phase_authorized": True, "admission_rules_frozen": False})
        self.assertFalse(result["executed"])
        self.assertEqual(result["missing_authorization"], ["admission_rules_frozen"])

    def test_check_authorized_raises_without_authorization(self):
        with self.assertRaises(AdmissionNotAuthorized):
            check_authorized({"admission_phase_authorized": False, "admission_rules_frozen": False})

    def test_authorized_dry_run_builds_snapshot_without_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            source.mkdir()
            (source / "mod.py").write_text("x = 1\n")
            config = {
                "admission_phase_authorized": True,
                "admission_rules_frozen": True,
                "source": str(source),
                "launch_whitelist": ["PATH", "TZ"],
                "baseline_condition": {"TZ": {"state": "set", "value": "UTC"}},
                "base_values": {"PATH": "/usr/bin"},
                "probe_entry_argv": ["probe"],
                "test_entry_argv": ["test"],
            }
            result = run_b0(config, dry_run=True)
            self.assertFalse(result["executed"])
            self.assertEqual(result["status"], "dry_run_authorized")
            self.assertIn("snapshot", result)


if __name__ == "__main__":
    unittest.main()
