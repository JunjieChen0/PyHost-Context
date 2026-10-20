"""PyHost-Context 准入阶段：``run_b0`` 接入 ``admission_gate`` 的合成测试。

覆盖四类要求：

1. 未授权时 ``run_b0`` **不启动任何进程**（用 ``subprocess.Popen`` 哨兵验证）；
2. 即使规定了 producer 插件，未授权仍一律拒绝、不启动进程；
3. 授权 + 合成套件时 ``run_b0`` 走真实链路（producer→pytest→native JSONL→gate），
   运行结果含 ``admission_gate`` 判定；
4. gate 通过（全通过套件）与不通过（全跳过、稳定失败）两类。

只对临时目录中的合成套件运行真实 pytest，不下载、不安装、不运行任何候选项目。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_ADMISSION_DIR = _HERE.parent
for _path in (str(_HERE), str(_ADMISSION_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from b0_runner import DEFAULT_PRODUCER_PLUGIN, resolve_producer, run_b0  # noqa: E402
from integration import synthetic_suite  # noqa: E402

ALLOW_NONE = {"allow_skips": False, "allow_xfails": False, "allow_xpasses": False}
ALLOW_ALL = {"allow_skips": True, "allow_xfails": True, "allow_xpasses": True}

WHITELIST = [
    "PATH", "HOME", "TMPDIR", "PYTHONPATH", "PYTHONIOENCODING",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "TZ", "LC_ALL", "LC_CTYPE", "LANG",
]
BASE_VALUES = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "HOME": os.environ.get("HOME", tempfile.gettempdir()),
    "TMPDIR": tempfile.gettempdir(),
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
}
BASELINE_CONDITION = {
    "TZ": {"state": "unset"},
    "LC_ALL": {"state": "unset"},
    "LC_CTYPE": {"state": "unset"},
    "LANG": {"state": "unset"},
}


class UnauthorizedNoProcessTests(unittest.TestCase):
    def test_refused_without_authorization_and_no_process_started(self):
        config = {"admission_phase_authorized": False, "admission_rules_frozen": False}
        with mock.patch("subprocess.Popen",
                        side_effect=AssertionError("unauthorized run started a process")):
            result = run_b0(config)
        self.assertFalse(result["executed"])
        self.assertEqual(result["status"], "refused_not_authorized")
        self.assertEqual(result["missing_authorization"],
                         ["admission_phase_authorized", "admission_rules_frozen"])

    def test_refused_with_producer_specified_no_process_started(self):
        config = {
            "admission_phase_authorized": False,
            "admission_rules_frozen": False,
            "producer_plugin": DEFAULT_PRODUCER_PLUGIN,
            "producer_pytest_args": ["-p", "jsonl_producer"],
            "test_entry_argv": [sys.executable, "-m", "pytest", "."],
        }
        with mock.patch("subprocess.Popen",
                        side_effect=AssertionError("unauthorized run started a process")):
            result = run_b0(config)
        self.assertFalse(result["executed"])
        self.assertEqual(result["status"], "refused_not_authorized")

    def test_refused_with_partial_authorization_and_no_process_started(self):
        config = {"admission_phase_authorized": True, "admission_rules_frozen": False}
        with mock.patch("subprocess.Popen",
                        side_effect=AssertionError("partially authorized run started a process")):
            result = run_b0(config)
        self.assertFalse(result["executed"])
        self.assertEqual(result["missing_authorization"], ["admission_rules_frozen"])


class AuthorizedChainCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pyhost-b0gate-")
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _suite(self, name, parts):
        return synthetic_suite.materialize(self.root / name, parts=parts)

    def _config(self, suite_dir, *, policy=None, producer_plugin=None, repeats=5):
        config = {
            "admission_phase_authorized": True,
            "admission_rules_frozen": True,
            "source": str(suite_dir),
            "cwd": str(suite_dir),
            "log_dir": str(self.root / "logs"),
            "launch_whitelist": list(WHITELIST),
            "base_values": dict(BASE_VALUES),
            "baseline_condition": dict(BASELINE_CONDITION),
            "probe_entry_argv": [sys.executable, "-c", "print('probe')"],
            "test_entry_argv": [
                sys.executable, "-m", "pytest", ".", "-q", "--no-header",
                "-p", "no:cacheprovider", "-o", "addopts=",
            ],
            "admission_baseline_repeats": repeats,
            "policy": dict(ALLOW_NONE if policy is None else policy),
            "outer_timeout_seconds": 120,
            "test_timeout_seconds": 120,
        }
        if producer_plugin is not None:
            config["producer_plugin"] = producer_plugin
        return config


class GateAdmittedTests(AuthorizedChainCase):
    def test_run_b0_real_chain_gate_admitted(self):
        suite = self._suite("all_pass", ("all_pass",))
        result = run_b0(self._config(suite))
        self.assertTrue(result["executed"])
        self.assertEqual(result["status"], "b0_completed")
        gate = result["admission_gate"]
        self.assertTrue(gate["admitted"], gate["reasons"])
        self.assertEqual(len(gate["rounds"]), 5)
        self.assertEqual(len(result["gate_rounds"]), 5)
        for artifact in result["gate_rounds"]:
            self.assertEqual(artifact["returncode"], 0)
            self.assertFalse(artifact["timed_out"])
            self.assertTrue(artifact["jsonl"].strip())
        # 真实链路：probe r1..r5 与 full_suite r1..r5 各一条进程账本。
        stages = [entry["stage"] for entry in result["ledger"]]
        self.assertIn("full_suite_r5", stages)
        self.assertIn("probe_r1", stages)

    def test_producer_plugin_and_dash_p_are_configurable(self):
        suite = self._suite("all_pass_cfg", ("all_pass",))
        config = self._config(suite, producer_plugin=DEFAULT_PRODUCER_PLUGIN)
        config["producer_pytest_args"] = ["-p", "jsonl_producer"]
        plugin_path, plugin_args = resolve_producer(config)
        self.assertTrue(str(plugin_path).endswith("integration/jsonl_producer.py"))
        self.assertEqual(plugin_args, ["-p", "jsonl_producer"])
        result = run_b0(config)
        self.assertTrue(result["admission_gate"]["admitted"],
                        result["admission_gate"]["reasons"])

    def test_default_producer_points_to_integration_producer(self):
        plugin_path, plugin_args = resolve_producer({})
        self.assertEqual(plugin_path,
                         (_ADMISSION_DIR / DEFAULT_PRODUCER_PLUGIN).resolve())
        self.assertTrue(str(plugin_path).endswith("integration/jsonl_producer.py"))
        self.assertEqual(plugin_args, ["-p", "jsonl_producer"])


class GateRejectedTests(AuthorizedChainCase):
    def test_run_b0_gate_rejected_when_no_call_executed(self):
        suite = self._suite("all_skip", ("all_skip",))
        result = run_b0(self._config(suite))
        self.assertTrue(result["executed"])
        gate = result["admission_gate"]
        self.assertFalse(gate["admitted"])
        self.assertTrue(any(":no_call_executed" in reason for reason in gate["reasons"]),
                        gate["reasons"])

    def test_run_b0_gate_rejected_when_stable_failure(self):
        suite = self._suite("all_fail", ("all_fail",))
        result = run_b0(self._config(suite, policy=ALLOW_ALL))
        self.assertTrue(result["executed"])
        gate = result["admission_gate"]
        self.assertFalse(gate["admitted"])
        joined = " ".join(gate["reasons"])
        self.assertIn(":failure:", joined)
        self.assertIn(":returncode_not_int_zero", joined)


if __name__ == "__main__":
    unittest.main()
