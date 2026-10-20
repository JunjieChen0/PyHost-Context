"""PyHost-Context 准入阶段：``admission_gate`` 的合成套件端到端测试。

用真实 pytest 子进程跑合成套件产生 5 轮 native JSONL，喂给 ``admission_gate``；
含正例与反例（稳定失败、全跳过、returncode 非 0、缺 SessionFinished、身份漂移、轮数不符）。
不启动任何候选项目。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ADMISSION_DIR = _HERE.parent
for _path in (str(_HERE), str(_ADMISSION_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from admission_gate import POLICY_KEYS, admission_gate  # noqa: E402
from integration import pipeline, synthetic_suite  # noqa: E402

ALLOW_ALL = {"allow_skips": True, "allow_xfails": True, "allow_xpasses": True}
ALLOW_NONE = {"allow_skips": False, "allow_xfails": False, "allow_xpasses": False}


class AdmissionGateCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pyhost-gate-")
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _rounds(self, name, parts, count=5, extra_args=()):
        suite = synthetic_suite.materialize(self.root / name, parts=parts)
        rounds = []
        for index in range(1, count + 1):
            path = self.root / f"{name}-r{index}.jsonl"
            rounds.append(pipeline.run_round(suite, path, extra_args=extra_args))
        return rounds

    @staticmethod
    def _drop_report(round_artifact, report_type):
        lines = [ln for ln in round_artifact["jsonl"].splitlines()
                 if json.loads(ln).get("$report_type") != report_type]
        mutated = dict(round_artifact)
        mutated["jsonl"] = "\n".join(lines) + "\n"
        return mutated


class PositiveGateTests(AdmissionGateCase):
    def test_all_pass_five_rounds_admitted(self):
        rounds = self._rounds("all_pass", ("all_pass",))
        decision = admission_gate(rounds, policy=ALLOW_NONE)
        self.assertEqual(decision["reasons"], [])
        self.assertTrue(decision["admitted"])
        self.assertEqual(len(decision["rounds"]), 5)
        self.assertEqual(len({d["identity_fingerprint"] for d in decision["rounds"]}), 1)

    def test_mixed_allowed_by_policy_admitted(self):
        rounds = self._rounds("mixed", ("gate_mixed",))
        decision = admission_gate(rounds, policy=ALLOW_ALL)
        self.assertTrue(decision["admitted"], decision["reasons"])


class NegativeGateTests(AdmissionGateCase):
    def test_stable_failure_not_admitted(self):
        rounds = self._rounds("all_fail", ("all_fail",))
        decision = admission_gate(rounds, policy=ALLOW_ALL)
        self.assertFalse(decision["admitted"])
        self.assertTrue(any(":failure:" in reason for reason in decision["reasons"]))

    def test_all_skip_not_admitted(self):
        rounds = self._rounds("all_skip", ("all_skip",))
        decision = admission_gate(rounds, policy=ALLOW_ALL)
        self.assertFalse(decision["admitted"])
        self.assertTrue(any(":no_call_executed" in r for r in decision["reasons"]))

    def test_skip_xfail_xpass_rejected_without_policy(self):
        rounds = self._rounds("mixed_reject", ("gate_mixed",))
        decision = admission_gate(rounds, policy=ALLOW_NONE)
        self.assertFalse(decision["admitted"])
        joined = " ".join(decision["reasons"])
        self.assertIn(":skip:", joined)
        self.assertIn(":xfail:", joined)
        self.assertIn(":xpass:", joined)

    def test_returncode_one_rejected(self):
        rounds = self._rounds("rc", ("all_pass",))
        mutated = list(rounds)
        first = dict(mutated[0])
        first["returncode"] = 1
        mutated[0] = first
        decision = admission_gate(mutated, policy=ALLOW_NONE)
        self.assertFalse(decision["admitted"])
        self.assertIn("round1:returncode_not_int_zero", decision["reasons"])

    def test_bool_returncode_rejected(self):
        rounds = self._rounds("rcbool", ("all_pass",))
        mutated = list(rounds)
        first = dict(mutated[0])
        first["returncode"] = True  # bool 不是严格整数 0
        mutated[0] = first
        decision = admission_gate(mutated, policy=ALLOW_NONE)
        self.assertFalse(decision["admitted"])
        self.assertIn("round1:returncode_not_int_zero", decision["reasons"])

    def test_missing_session_finished_rejected(self):
        rounds = self._rounds("nosess", ("all_pass",))
        mutated = list(rounds)
        mutated[2] = self._drop_report(mutated[2], "SessionFinished")
        decision = admission_gate(mutated, policy=ALLOW_NONE)
        self.assertFalse(decision["admitted"])
        self.assertIn("round3:session_finished_missing", decision["reasons"])

    def test_wrong_round_count_rejected(self):
        rounds = self._rounds("short", ("all_pass",), count=4)
        decision = admission_gate(rounds, policy=ALLOW_NONE)
        self.assertFalse(decision["admitted"])
        self.assertIn("round_count:4!=5", decision["reasons"])

    def test_identity_drift_rejected(self):
        base = self._rounds("drift_base", ("all_pass",), count=4)
        other = self._rounds("drift_other", ("all_pass", "added"), count=1)
        decision = admission_gate(base + other, policy=ALLOW_NONE)
        self.assertFalse(decision["admitted"])
        self.assertIn("identity_drift", decision["reasons"])
        self.assertIn("result_vector_drift", decision["reasons"])

    def test_incomplete_policy_rejected(self):
        rounds = self._rounds("policy", ("all_pass",))
        decision = admission_gate(rounds, policy={"allow_skips": True})
        self.assertFalse(decision["admitted"])
        self.assertTrue(any(r.startswith("policy_incomplete:") for r in decision["reasons"]))

    def test_timeout_round_rejected(self):
        rounds = self._rounds("timeout", ("all_pass",))
        mutated = list(rounds)
        first = dict(mutated[0])
        first["timed_out"] = True
        mutated[0] = first
        decision = admission_gate(mutated, policy=ALLOW_NONE)
        self.assertFalse(decision["admitted"])
        self.assertIn("round1:timed_out", decision["reasons"])

    def test_malformed_jsonl_rejected(self):
        rounds = self._rounds("malformed", ("all_pass",))
        mutated = list(rounds)
        first = dict(mutated[0])
        first["jsonl"] = first["jsonl"] + "this is not json\n"
        mutated[0] = first
        decision = admission_gate(mutated, policy=ALLOW_NONE)
        self.assertFalse(decision["admitted"])
        self.assertIn("round1:malformed_jsonl", decision["reasons"])

    def test_policy_constants_exposed(self):
        self.assertEqual(set(POLICY_KEYS), {"allow_skips", "allow_xfails", "allow_xpasses"})


if __name__ == "__main__":
    unittest.main()
