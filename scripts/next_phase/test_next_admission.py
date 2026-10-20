"""ClawsGO Science Agent：S1a 入口与静态见证的回归测试（不执行候选、不安装、不联网）。"""
import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE
sys.path.insert(0, str(NEXT))
sys.path.insert(0, str(NEXT / "admission"))

import run_next_admission as entry  # noqa: E402
import closure  # noqa: E402
from admission import scheduler  # noqa: E402


class AdmissionPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan, cls.pools = entry.load_plan()

    def test_sequence_is_29_positions_mmr(self):
        self.assertEqual(len(self.plan), 29)
        groups = [row["group"] for row in self.plan]
        # 位置 1—19：M、M、R 交错；机制池在第 13 个机制位置用尽后，剩余位置按参照池继续。
        for index in range(19):
            expected = "reference" if index % 3 == 2 else "mechanism"
            self.assertEqual(groups[index], expected, f"position {index + 1}")
        for index in range(19, 29):
            self.assertEqual(groups[index], "reference", f"position {index + 1}")

    def test_targets_and_call_ceiling(self):
        self.assertEqual(scheduler.TARGET_PASSES, {"mechanism": 8, "reference": 4})
        self.assertEqual(len(self.plan) * scheduler.CALLS_PER_ATTEMPT, 87)

    def test_family_merge_across_channels(self):
        by_project = {row["project"]: row for row in self.plan}
        self.assertEqual(by_project["pylint"]["canonical_family"],
                         by_project["pylint-plugin-utils"]["canonical_family"])
        self.assertEqual(by_project["structlog"]["canonical_family"],
                         by_project["structlog-sentry"]["canonical_family"])

    def test_static_ineligible_are_marked(self):
        marks = {row["project"]: row.get("eligible") for row in self.plan}
        self.assertFalse(marks["structlog-sentry"])
        self.assertFalse(marks["django-structlog"])
        self.assertFalse(marks["pylint-plugin-utils"])
        self.assertFalse(marks["schemathesis"])
        self.assertTrue(marks["ipython"])
        self.assertTrue(marks["twine"])

    def test_plan_document_binds_inputs(self):
        document = entry.plan_document(self.plan)
        self.assertGreater(len(document["static_inputs"]), 20)
        self.assertIn("extension/tooling/run_extension.py", document["static_inputs"])
        self.assertIn("tooling/restore_source_tree.py", document["static_inputs"])
        self.assertIn("matrix/tooling/matrix_runner/source_tree.py", document["static_inputs"])
        self.assertFalse(document["authorized"])

    def test_closure_includes_transitive_helpers(self):
        files = closure.local_import_closure(NEXT / "run_next_admission.py")
        for required in ("extension/tooling/run_extension.py",
                         "matrix/tooling/run_matrix.py",
                         "matrix/tooling/matrix_runner/source_tree.py",
                         "tooling/restore_source_tree.py",
                         "tooling/artifact_identity.py",
                         "tooling/admission_runner/timeout_ledger.py",
                         "extension/next_phase/admission/scheduler.py"):
            self.assertIn(required, files)


class ResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan, _ = entry.load_plan()

    def test_foreign_batch_rejected(self):
        state = scheduler.empty_state()
        row = self.plan[0]
        scheduler.settle(state, row, {
            "admitted": True, "status": "x", "calls": 3,
            "batch_id": "other-batch", "commitment": "c"})
        result = scheduler.validate_resume(self.plan, state, "next-admission-20261018", "c")
        self.assertFalse(result["ok"])
        self.assertIn("foreign_batch:1", result["problems"])

    def test_duplicate_position_rejected(self):
        state = scheduler.empty_state()
        row = self.plan[0]
        for _ in range(2):
            scheduler.settle(state, row, {
                "admitted": True, "status": "x", "calls": 3,
                "batch_id": "next-admission-20261018", "commitment": "c"})
        result = scheduler.validate_resume(self.plan, state, "next-admission-20261018", "c")
        self.assertIn("duplicate_position:1", result["problems"])

    def test_resume_does_not_redispatch(self):
        state = scheduler.empty_state()
        executed = []

        def execute(row):
            executed.append(row["position"])
            return {"admitted": False, "status": "x", "calls": 3,
                    "batch_id": "next-admission-20261018", "commitment": "c"}

        scheduler.run_to_completion(self.plan, state, execute, max_calls=6)
        first = list(executed)
        scheduler.run_to_completion(self.plan, state, execute)
        self.assertEqual(first, [1, 2])
        self.assertEqual(len(executed), len(set(executed)))


class ClearanceTests(unittest.TestCase):
    def test_missing_clearance_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = entry.CLEARANCE
            entry.CLEARANCE = pathlib.Path(tmp) / "missing.json"
            try:
                with self.assertRaises(SystemExit):
                    entry.require_clearance()
            finally:
                entry.CLEARANCE = original

    def test_wrong_decision_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "clearance.json"
            path.write_text(json.dumps({"signed_by": "Reviewer 001",
                                        "decision": "SOMETHING_ELSE",
                                        "batch_id": entry.BATCH_ID}))
            original = entry.CLEARANCE
            entry.CLEARANCE = path
            try:
                gate = entry.clearance_gate()
                self.assertFalse(gate["ok"])
                self.assertTrue(any("clearance_decision" in p for p in gate["problems"]))
            finally:
                entry.CLEARANCE = original


class StaticWitnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads((NEXT / "STATIC_WITNESSES_20261018.json").read_text())
        cls.items = {item["project"]: item for item in cls.doc["items"]}

    def test_thirteen_items(self):
        self.assertEqual(len(self.items), 13)

    def test_each_read_has_source_line_and_timing(self):
        for item in self.items.values():
            self.assertTrue(item["reads"], item["project"])
            for read in item["reads"]:
                self.assertTrue(read["source_line"])
                self.assertIn(read["corrected_timing"],
                              ("import_or_collection", "construction", "call_time"))
                self.assertEqual(read["read_or_write"], "read")

    def test_ineligible_have_reasons(self):
        ineligible = [i for i in self.items.values()
                      if not i["qualification"]["static_eligible"]]
        self.assertEqual(len(ineligible), 4)
        for item in ineligible:
            self.assertTrue(item["qualification"]["ineligible_reason"])

    def test_eligible_have_test_path(self):
        for item in self.items.values():
            if item["qualification"]["static_eligible"]:
                self.assertTrue(item["test_path"], item["project"])

    def test_dependency_identity_present(self):
        for item in self.items.values():
            if item.get("dependency"):
                dep = item["dependency"]
                self.assertRegex(dep["sha256"], r"^[0-9a-f]{64}$")
                self.assertGreater(dep["bytes"], 0)
                self.assertTrue(dep["declared_constraint"])

    def test_artifact_index_covers_screen(self):
        self.assertEqual(self.doc["artifact_index_count"], len(self.doc["artifact_index"]))
        self.assertGreaterEqual(self.doc["artifact_index_count"], 358)


if __name__ == "__main__":
    unittest.main(verbosity=2)
