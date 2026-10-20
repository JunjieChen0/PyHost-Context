"""PyHost-Context 准入阶段：合成套件的 producer→observer→JSONL→parser→summary 集成测试。

每个用例都在**临时目录**生成合成 fixture，用 **subprocess 启动真实 pytest**（挂载 producer 插件），
把真实产出的 native JSONL 交给 ``pytest_jsonl.py`` 解析并汇总，再断言端到端结果。

不下载、不安装、不运行任何候选项目。
"""
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ADMISSION_DIR = _HERE.parent
for _path in (str(_HERE), str(_ADMISSION_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from integration import pipeline, synthetic_suite  # noqa: E402

PASS = "test_core.py::test_pass"
FAIL = "test_core.py::test_fail"
SKIP = "test_core.py::test_skip"
SETUP_ERROR = "test_core.py::test_setup_error"
XFAIL = "test_core.py::test_xfail"
XPASS = "test_core.py::test_xpass"
XPASS_STRICT = "test_core.py::test_xpass_strict"
COND_FALSE = "test_xfail_variants.py::test_xfail_condition_false"
RUN_FALSE = "test_xfail_variants.py::test_xfail_run_false"
DUAL_FAIL = "test_dual_failure.py::test_call_and_teardown_fail"
TEARDOWN_ONLY = "test_dual_failure.py::test_teardown_only_fail"
SEMANTIC = "test_semantic.py::test_semantic_failure"


class PipelineIntegrationCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pyhost-integration-")
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _suite(self, name, parts=("core",)):
        return synthetic_suite.materialize(self.root / name, parts=parts)

    def _run(self, suite_dir, *, jsonl_name="report.jsonl", target=".",
             extra_args=(), baseline_nodeids=None, run_dir_prefixes=()):
        jsonl_path = self.root / jsonl_name
        summary = pipeline.produce_and_summarize(
            suite_dir, jsonl_path, target=target, extra_args=extra_args,
            baseline_nodeids=baseline_nodeids,
            run_dir_prefixes=run_dir_prefixes or (str(suite_dir),))
        return summary, jsonl_path

    def _assert_observed_artifact(self, summary, jsonl_path, allowed_codes=(0, 1, 2, 5)):
        self.assertIsNotNone(summary.get("process"))
        self.assertIn(summary["process"]["returncode"], allowed_codes)
        self.assertTrue(jsonl_path.exists())
        self.assertTrue(pipeline.read_jsonl(jsonl_path).strip())
        self.assertEqual(summary["malformed"], [])
        self.assertIsNotNone(summary["runtime_snapshot"])
        self.assertIsNotNone(summary["session_finished"])


class RealProducerTests(PipelineIntegrationCase):
    def test_real_pytest_produces_parseable_native_jsonl(self):
        suite = self._suite("baseline")
        summary, jsonl_path = self._run(suite)
        self._assert_observed_artifact(summary, jsonl_path)
        self.assertEqual(
            summary["collection"]["collected"],
            [PASS, FAIL, SKIP, SETUP_ERROR, XFAIL, XPASS, XPASS_STRICT])
        self.assertEqual(summary["counts"], {"passed": 2, "failed": 3, "skipped": 2})

    def test_native_collect_reports_and_collection_finished(self):
        suite = self._suite("native")
        summary, _ = self._run(suite)
        collection = summary["collection"]
        self.assertTrue(collection["uses_collection_items"])
        finished = collection["collection_finished"]
        self.assertIsNotNone(finished)
        self.assertEqual(finished["collected"], collection["collected"])
        self.assertEqual(finished["identity_fingerprint"],
                         collection["identity_fingerprint"])
        # native CollectReport 原样保留（含 root "" 与模块级），未把 item 伪造成 CollectReport。
        native_nodes = [r["nodeid"] for r in collection["native_collect_reports"]]
        self.assertIn("", native_nodes)
        self.assertIn("test_core.py", native_nodes)
        self.assertNotIn(PASS, native_nodes)

    def test_session_and_runtime_metadata(self):
        suite = self._suite("meta")
        summary, _ = self._run(suite)
        snapshot = summary["runtime_snapshot"]
        self.assertEqual(snapshot["$report_type"], "RuntimeSnapshot")
        for key in ("python_version", "pytest_version", "cwd", "sys_flags",
                    "open_fds", "environment", "locale", "timezone", "snapshot_hash"):
            self.assertIn(key, snapshot)
        self.assertIn("TZ", snapshot["timezone"])
        session = summary["session_finished"]
        self.assertIsInstance(session["exitstatus"], int)
        self.assertEqual(session["exitstatus"], summary["process"]["returncode"])
        self.assertEqual(session["collected_count"], len(summary["collection"]["collected"]))

    def test_jsonl_exclusive_create_does_not_overwrite(self):
        suite = self._suite("exclusive")
        jsonl_path = self.root / "exclusive.jsonl"
        first = pipeline.run_pytest_suite(suite, jsonl_path)
        self.assertIn(first.returncode, (0, 1))
        original = jsonl_path.read_text(encoding="utf-8")
        self.assertTrue(original.strip())
        # 同一路径再次运行：producer 独占创建失败，既有工件不被覆盖。
        second = pipeline.run_pytest_suite(suite, jsonl_path)
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(jsonl_path.read_text(encoding="utf-8"), original)


class SetupFailureTests(PipelineIntegrationCase):
    def test_setup_failure_marks_call_not_run_and_error(self):
        suite = self._suite("setup_error")
        summary, jsonl_path = self._run(suite)
        self._assert_observed_artifact(summary, jsonl_path)
        record = summary["per_test"][SETUP_ERROR]
        self.assertEqual(record["raw_outcome"], "failed")
        self.assertEqual(record["phases"],
                         {"setup": "reported", "call": "not_run", "teardown": "reported"})
        self.assertIn("error", record["derived_labels"])
        self.assertTrue(record["raw_complete"])
        self.assertEqual(record["phase_details"]["setup"]["outcome"], "failed")


class SkipXfailXpassTests(PipelineIntegrationCase):
    def setUp(self):
        super().setUp()
        suite = self._suite("labels", parts=("core", "xfail_variants"))
        self.summary, self.jsonl_path = self._run(suite)

    def test_plain_skip_has_no_xfail_label(self):
        record = self.summary["per_test"][SKIP]
        self.assertEqual(record["raw_outcome"], "skipped")
        self.assertFalse(record["wasxfail"])
        self.assertEqual(record["derived_labels"], [])
        self.assertIsNone(record["phase_details"]["call"]["wasxfail"])

    def test_xfail_is_xfailed_from_native_wasxfail(self):
        record = self.summary["per_test"][XFAIL]
        self.assertEqual(record["raw_outcome"], "skipped")
        self.assertIn("xfailed", record["derived_labels"])
        self.assertEqual(record["xfail_reason"], "known bug")
        self.assertEqual(record["phase_details"]["call"]["wasxfail"], "known bug")

    def test_xpass_is_xpassed_from_native_wasxfail(self):
        record = self.summary["per_test"][XPASS]
        self.assertEqual(record["raw_outcome"], "passed")
        self.assertIn("xpassed", record["derived_labels"])
        self.assertTrue(record["wasxfail"])

    def test_strict_xpass_keeps_native_failure_and_longrepr(self):
        record = self.summary["per_test"][XPASS_STRICT]
        # native call outcome 保留为 failed，原 longrepr 保留，绝不伪造 wasxfail。
        self.assertEqual(record["raw_outcome"], "failed")
        self.assertEqual(record["phase_details"]["call"]["outcome"], "failed")
        self.assertIn("[XPASS(strict)]", record["longrepr"])
        self.assertIn("xpassed_strict", record["derived_labels"])
        self.assertNotIn("xpassed", record["derived_labels"])
        self.assertFalse(record["wasxfail"])
        self.assertIsNone(record["phase_details"]["call"]["wasxfail"])
        self.assertTrue(record["xfail_strict"])

    def test_xfail_condition_false_runs_normally(self):
        record = self.summary["per_test"][COND_FALSE]
        self.assertEqual(record["raw_outcome"], "passed")
        self.assertEqual(record["derived_labels"], [])
        self.assertFalse(record["wasxfail"])

    def test_xfail_run_false_is_setup_phase_xfailed(self):
        record = self.summary["per_test"][RUN_FALSE]
        self.assertEqual(record["phases"]["call"], "not_run")
        self.assertIn("xfailed", record["derived_labels"])
        self.assertEqual(record["phase_details"]["setup"]["outcome"], "skipped")
        self.assertNotIn("call", record["phase_details"])
        self.assertTrue(record["raw_complete"])


class XfailPolicyVariantTests(PipelineIntegrationCase):
    def test_config_xfail_strict_promotes_xpass_to_strict(self):
        suite = self._suite("strict_cfg")
        summary, _ = self._run(suite, extra_args=("-o", "xfail_strict=true"))
        record = summary["per_test"][XPASS]
        self.assertEqual(record["raw_outcome"], "failed")
        self.assertIn("xpassed_strict", record["derived_labels"])
        self.assertIn("[XPASS(strict)]", record["longrepr"])

    def test_runxfail_disables_xfail_markers(self):
        suite = self._suite("runxfail", parts=("core", "xfail_variants"))
        summary, _ = self._run(suite, extra_args=("--runxfail",))
        # 在 --runxfail 下，xfail 测试按真实结果计：test_xfail 变成真实失败。
        xfail_record = summary["per_test"][XFAIL]
        self.assertEqual(xfail_record["raw_outcome"], "failed")
        self.assertNotIn("xfailed", xfail_record["derived_labels"])
        xpass_record = summary["per_test"][XPASS]
        self.assertEqual(xpass_record["raw_outcome"], "passed")
        self.assertEqual(xpass_record["derived_labels"], [])
        run_false = summary["per_test"][RUN_FALSE]
        self.assertEqual(run_false["raw_outcome"], "passed")


class DualFailureTests(PipelineIntegrationCase):
    def setUp(self):
        super().setUp()
        suite = self._suite("dual", parts=("dual",))
        self.summary, _ = self._run(suite)

    def test_call_and_teardown_failure_coexist(self):
        record = self.summary["per_test"][DUAL_FAIL]
        self.assertEqual(record["raw_outcome"], "failed")
        self.assertIn("error", record["derived_labels"])
        self.assertEqual(record["phase_details"]["call"]["outcome"], "failed")
        self.assertEqual(record["phase_details"]["teardown"]["outcome"], "failed")

    def test_teardown_only_failure_is_error_with_passed_call(self):
        record = self.summary["per_test"][TEARDOWN_ONLY]
        self.assertEqual(record["phase_details"]["call"]["outcome"], "passed")
        self.assertEqual(record["phase_details"]["teardown"]["outcome"], "failed")
        self.assertIn("error", record["derived_labels"])


class CollectionTests(PipelineIntegrationCase):
    def test_collection_error_recorded_while_run_continues(self):
        suite = self._suite("broken", parts=("core", "broken"))
        summary, jsonl_path = self._run(
            suite, extra_args=("--continue-on-collection-errors",))
        self._assert_observed_artifact(summary, jsonl_path)
        self.assertEqual([e["nodeid"] for e in summary["collection"]["errors"]],
                         ["test_broken_collect.py"])
        self.assertTrue(summary["collection"]["errors"][0]["longrepr"])
        self.assertIn(PASS, summary["collection"]["collected"])
        self.assertIn(PASS, summary["per_test"])
        self.assertEqual(summary["collection_errors"], summary["collection"]["errors"])

    def test_deselection_excluded_from_collected(self):
        suite = self._suite("deselect")
        summary, jsonl_path = self._run(suite, extra_args=("-k", "test_pass"))
        self._assert_observed_artifact(summary, jsonl_path)
        self.assertEqual(summary["collection"]["collected"], [PASS])
        self.assertIn(FAIL, summary["collection"]["deselected"])
        self.assertEqual(set(summary["per_test"]), {PASS})


class IdentityTests(PipelineIntegrationCase):
    def test_identity_change_detected_against_baseline(self):
        baseline = self._suite("identity_baseline")
        altered = self._suite("identity_altered", parts=("core", "added"))
        base_summary, _ = self._run(baseline, jsonl_name="base.jsonl")
        baseline_nodeids = base_summary["collection"]["collected"]
        summary, jsonl_path = self._run(
            altered, jsonl_name="altered.jsonl", baseline_nodeids=baseline_nodeids)
        self._assert_observed_artifact(summary, jsonl_path)
        added = "test_added.py::test_added"
        self.assertEqual(summary["collection"]["added"], [added])
        self.assertEqual(summary["collection"]["missing"], [])
        self.assertNotEqual(base_summary["collection"]["identity_fingerprint"],
                            summary["collection"]["identity_fingerprint"])
        self.assertTrue(summary["per_test"][added]["identity_changed"])
        self.assertFalse(summary["per_test"][PASS]["identity_changed"])


class FailureSignatureTests(PipelineIntegrationCase):
    def test_signature_is_conservative(self):
        suite = self._suite("semantic", parts=("semantic",))
        summary, _ = self._run(suite)
        signature = summary["per_test"][SEMANTIC]["failure_signature"]
        self.assertIsNotNone(signature)
        # 语义值一律保留，包括看似可疑的 0x 值与时长样式值。
        self.assertIn("2026-10-14T12:34:56+08:00", signature)
        self.assertIn("Asia/Shanghai", signature)
        self.assertIn("expected=3 actual=4", signature)
        self.assertIn("color=red", signature)
        self.assertIn("token=0xdeadbeef", signature)
        self.assertIn("elapsed=0.123s", signature)
        # 仅对象 repr 地址与已登记运行目录前缀被替换。
        self.assertIn("<object_address>", signature)
        self.assertIn("<run_dir>", signature)
        self.assertNotIn(str(suite), signature)

    def test_simple_failure_signature_keeps_assertion_values(self):
        suite = self._suite("sig_simple")
        summary, _ = self._run(suite)
        self.assertIn("assert 1 == 2", summary["per_test"][FAIL]["failure_signature"])
        self.assertIsNone(summary["per_test"][PASS]["failure_signature"])


if __name__ == "__main__":
    unittest.main()
