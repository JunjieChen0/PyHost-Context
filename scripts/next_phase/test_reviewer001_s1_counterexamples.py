"""ClawsGO Science Agent：Reviewer 001 S1 反例的**反向回归**。

原审核的 17 条反例逐条对应一个“修正后必须不成立”的断言；另含两条走
`run → execute_attempt → 假 admit_one` 的成功/失败接线测试。不执行候选、不安装、不联网。
"""
import contextlib
import io
import json
import pathlib
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE
sys.path.insert(0, str(NEXT))
sys.path.insert(0, str(NEXT / "admission"))

import run_next_admission as entry  # noqa: E402
from admission import scheduler  # noqa: E402


class StaticWiringTests(unittest.TestCase):
    def test_entry_can_import_instrument(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec("run_extension"))
        self.assertIsNotNone(importlib.util.find_spec("run_matrix"))
        self.assertIsNotNone(importlib.util.find_spec("matrix_runner.source_tree"))

    def test_metadata_uses_phase_governance_not_old_records(self):
        import run_extension as rx
        with entry._patch_instrument():
            self.assertEqual(rx.EFFECTIVE, entry.EFFECTIVE)
            self.assertEqual(rx.CLEARANCE, entry.CLEARANCE)
            self.assertFalse(str(rx.EFFECTIVE).endswith("v1.1_Reviewer001.json"))
            self.assertFalse(str(rx.CLEARANCE).endswith("REVIEWER001_X5_CLEARANCE.json"))

    def test_a6_timeout_capped_at_240(self):
        import run_extension as rx
        seen = []

        def fake_run(argv, *args, **kwargs):
            seen.append(kwargs.get("timeout"))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        original = rx.subprocess.run
        rx.subprocess.run = fake_run
        try:
            with entry._patch_instrument():
                rx.subprocess.run(["-m", "pytest", "--collect-only"], timeout=600)
                rx.subprocess.run(["pip", "download"], timeout=900)
        finally:
            rx.subprocess.run = original
        self.assertEqual(seen, [entry.A6_TIMEOUT_SECONDS, 900])

    def test_observer_resources_and_instrument_protocol_bound(self):
        inputs = entry.static_inputs()
        for path in entry.OBSERVER_RESOURCES:
            self.assertIn(str(path.relative_to(entry.ROOT)), inputs)
        self.assertIn(str(entry.INSTRUMENT_PROTOCOL.relative_to(entry.ROOT)), inputs)
        roles = entry.input_roles()
        self.assertEqual(roles[str(entry.INSTRUMENT_PROTOCOL.relative_to(entry.ROOT))],
                         "instrument_protocol")
        self.assertEqual(roles[str(entry.EFFECTIVE.relative_to(entry.ROOT))],
                         "phase_governance")


class ClearanceTests(unittest.TestCase):
    def _gate(self, bound_files):
        fixture = {"signed_by": "Reviewer 001", "decision": entry.REQUIRED_DECISION,
                   "batch_id": entry.BATCH_ID,
                   "scope": {"S1a_admission_candidate_execution": True,
                             "S1b_matrix_and_independent_b0": False,
                             "S2_recovery": False, "S3_repair_and_witness": False,
                             "tool_repro": False},
                   "bound_files": bound_files,
                   "static_inputs_commitment": "whatever"}
        with patch.object(entry, "CLEARANCE",
                          SimpleNamespace(is_file=lambda: True)), \
                patch.object(entry, "load_json", return_value=fixture), \
                patch.object(entry, "sha256_of", return_value="synthetic"), \
                patch.object(entry, "static_inputs", return_value={"a": "b"}):
            return entry.clearance_gate()

    def test_empty_bindings_rejected(self):
        gate = self._gate({})
        self.assertFalse(gate["ok"])
        self.assertIn("clearance_bound_files_empty", gate["problems"])

    def test_incomplete_bindings_rejected(self):
        gate = self._gate({"different": "synthetic"})
        self.assertFalse(gate["ok"])
        self.assertTrue(any("clearance_bound_files_incomplete" in p for p in gate["problems"]))

    def test_missing_clearance_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = entry.CLEARANCE
            entry.CLEARANCE = pathlib.Path(tmp) / "missing.json"
            try:
                with self.assertRaises(SystemExit):
                    entry.require_clearance()
            finally:
                entry.CLEARANCE = original


class IdentityTests(unittest.TestCase):
    def _make_sdist(self, root, name="other-99", pkg="entirely-other-library",
                    version="99"):
        path = root / "synthetic-1.tar.gz"
        raw = f"Metadata-Version: 2.1\nName: {pkg}\nVersion: {version}\n".encode()
        with tarfile.open(path, "w:gz") as tar:
            member = tarfile.TarInfo(f"{name}/PKG-INFO")
            member.size = len(raw)
            tar.addfile(member, io.BytesIO(raw))
        return path

    def test_wrong_name_version_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._make_sdist(pathlib.Path(tmp))
            with self.assertRaises(entry.PreparationFailure) as caught:
                entry.verify_artifact_identity(path, "synthetic", "1")
            self.assertEqual(caught.exception.code, "pkg_info_project_mismatch")

    def test_correct_identity_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._make_sdist(pathlib.Path(tmp), name="synthetic-1",
                                    pkg="synthetic", version="1")
            receipt = entry.verify_artifact_identity(path, "synthetic", "1")
            self.assertTrue(receipt["identity_verified"])
            self.assertEqual(receipt["metadata_version"], "1")


class ResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan, _ = entry.load_plan()

    def test_missing_bindings_rejected(self):
        state = scheduler.empty_state()
        scheduler.settle(state, self.plan[0], {
            "admitted": True, "status": "x", "calls": 3,
            "batch_id": None, "commitment": None})
        result = scheduler.validate_resume(self.plan, state, entry.BATCH_ID, "c")
        self.assertFalse(result["ok"])
        self.assertTrue(any("foreign_batch" in p for p in result["problems"]))
        self.assertTrue(any("foreign_commitment" in p for p in result["problems"]))

    def test_changed_version_and_family_rejected(self):
        state = scheduler.empty_state()
        scheduler.settle(state, self.plan[0], {
            "admitted": True, "status": "x", "calls": 3,
            "batch_id": entry.BATCH_ID, "commitment": "c"})
        state["records"][0]["version"] = "wrong"
        state["records"][0]["canonical_family"] = "wrong"
        result = scheduler.validate_resume(self.plan, state, entry.BATCH_ID, "c")
        self.assertFalse(result["ok"])
        self.assertTrue(any("record_mismatch" in p and "version" in p
                            for p in result["problems"]))
        self.assertTrue(any("record_mismatch" in p and "canonical_family" in p
                            for p in result["problems"]))

    def test_duplicate_skip_rejected(self):
        state = scheduler.empty_state()
        scheduler.skip(state, self.plan[0], "group_full")
        scheduler.skip(state, self.plan[0], "group_full")
        result = scheduler.validate_resume(self.plan, state, entry.BATCH_ID, "c")
        self.assertFalse(result["ok"])
        self.assertIn("duplicate_position:1", result["problems"])

    def test_illegal_skip_reason_rejected(self):
        state = scheduler.empty_state()
        with self.assertRaises(ValueError):
            scheduler.skip(state, self.plan[0], "synthetic_wrong_reason")


class DependencyFieldTests(unittest.TestCase):
    def test_resolved_version_field(self):
        plan, _ = entry.load_plan()
        row = next(r for r in plan if r["project"] == "rich-toolkit")
        agreement = entry.dependency_agreement(
            row, {"resolved_distributions": {"rich": "15.0.0"}})
        self.assertEqual(agreement["static_version"], "15.0.0")
        self.assertTrue(agreement["match"])


class CaseTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.table = json.loads((NEXT / "CASE_TABLES_20261018_REV1.json").read_text())
        cls.cases = {case["case_id"]: case for case in cls.table["cases"]}

    def test_am3_only_inverts_empty_branch(self):
        after = next(h["after"] for h in
                     next(m for m in self.cases["A"]["mutations"]
                          if m["id"] == "A-M3")["patch"]["hunks"] if "Console" in h["after"])
        truth = {}
        for value in (None, "", "1", "0"):
            original = value not in (None, "")
            mutant = ("NO_COLOR" in ({} if value is None else {"NO_COLOR": value}))
            truth[value] = (original, mutant)
        self.assertEqual(truth[None], (False, False))
        self.assertEqual(truth[""], (False, True))
        self.assertEqual(truth["1"], (True, True))
        self.assertEqual(truth["0"], (True, True))
        self.assertIn('"NO_COLOR" in os.environ', after)

    def test_c_denominator_not_result_filtered(self):
        denominator = self.table["evaluation_rule"]["C_denominator"]
        self.assertIn("target_executed", denominator)
        self.assertIn("mutation_killed", denominator)
        self.assertNotIn("目标断言在该格命中", denominator)

    def test_bm2_marked_inactive(self):
        bm2 = next(m for m in self.cases["B"]["mutations"] if m["id"] == "B-M2")
        self.assertFalse(bm2["suite_detectable"])
        self.assertEqual(bm2["target_nodes"], [])
        self.assertTrue(all(value.startswith("not_activated")
                            for value in bm2["applicable_contexts"].values()
                            if value != "background_failing"))

    def test_products_are_case_local(self):
        for case in self.table["cases"]:
            products = case["cell_matrix"]["products"]
            self.assertEqual(products[0], "original")
            self.assertEqual(products[1:], [m["id"] for m in case["mutations"]])


class WitnessTests(unittest.TestCase):
    def test_witness_uses_stdlib_width_attribute(self):
        source = (NEXT / "witnesses" / "repair_witness.py").read_text()
        self.assertIn("_get_formatter(), \"_width\"", source)
        self.assertNotIn("_get_formatter().width", source)

    def test_witness_creates_input_file(self):
        source = (NEXT / "witnesses" / "repair_witness.py").read_text()
        self.assertIn("TemporaryDirectory", source)
        self.assertIn('write_text("Some RST."', source)

    def test_sgr_extended_colors(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "witness_module", NEXT / "witnesses" / "repair_witness.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rgb = module.color_profile("\x1b[38;2;1;2;3mRGB\x1b[0m")
        self.assertTrue(rgb["has_color_components"])
        self.assertEqual(rgb["basic_color_codes"], [])
        indexed = module.color_profile("\x1b[38;5;208morange\x1b[0m")
        self.assertTrue(indexed["has_color_components"])
        basic = module.color_profile("\x1b[1m\x1b[35mmagenta\x1b[0m")
        self.assertEqual(basic["basic_color_codes"], [35])
        self.assertEqual(basic["non_color_sgr"], [1])


class DispatchWiringTests(unittest.TestCase):
    """走 run → execute_attempt → 假 admit_one 的成功与失败支路。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=str(NEXT))
        self.root = pathlib.Path(self.tmp.name)
        self.plan, _ = entry.load_plan()

    def tearDown(self):
        self.tmp.cleanup()

    def _patch_env(self, stack):
        stack.enter_context(patch.object(entry, "RUNS", self.root / "runs"))
        stack.enter_context(patch.object(entry, "ARTIFACTS", self.root / "artifacts"))
        stack.enter_context(patch.object(entry, "WHEELHOUSE", self.root / "wheelhouse"))
        stack.enter_context(patch.object(entry, "CANDIDATES_DOC", self.root / "candidates.json"))
        stack.enter_context(patch.object(entry, "require_clearance",
                                         return_value={"clearance_sha256": "synthetic"}))
        stack.enter_context(patch.object(entry, "clearance_gate",
                                         return_value={"ok": True,
                                                        "clearance_sha256": "synthetic"}))

    def test_success_passes_candidate_commitment(self):
        seen = {}

        def fake_prepare(row, attempt_dir=None):
            return ({"source_dir": f"{row['project']}-{row['version']}"},
                    {"wheels": {}, "resolved_distributions": {}}, {})

        def fake_commitment(row, record, lock, batch):
            return {"pre_run_commitment_hash": "candidate-hash", "files": {},
                    "project": row["project"]}

        def fake_admit(project, protocol, b0, commitment, phase_root, detail,
                       admission_id=None):
            seen["commitment"] = commitment["pre_run_commitment_hash"]
            detail["A6_collect_attempts"] = [{}]
            return {"run_id": "synthetic-a7"}

        import run_extension as rx
        with contextlib.ExitStack() as stack:
            self._patch_env(stack)
            stack.enter_context(patch.object(entry, "prepare_candidate", side_effect=fake_prepare))
            stack.enter_context(patch.object(entry, "candidate_commitment", side_effect=fake_commitment))
            stack.enter_context(patch.object(entry, "_patch_instrument",
                                             return_value=contextlib.nullcontext()))
            stack.enter_context(patch.object(rx, "admit_one", side_effect=fake_admit))
            stack.enter_context(patch.object(rx, "admission_decision", return_value=(True, [])))
            outcome = entry.execute_attempt(self.plan[0], "batch-hash")
        self.assertEqual(seen["commitment"], "candidate-hash")
        self.assertEqual(outcome["candidate_commitment"], "candidate-hash")
        attempt = self.root / "runs" / self.plan[0]["project"] / "attempt-001"
        self.assertTrue((attempt / "commitment.json").is_file())
        self.assertTrue((attempt / "result.json").is_file())

    def test_expected_preparation_failure_is_ledgered(self):
        files = {"synthetic-input": "a" * 64}
        batch = self.root / "batches"
        batch.mkdir()
        (batch / f"{entry.BATCH_ID}.json").write_text(
            json.dumps({"pre_run_commitment_hash": entry.commitment_of(files)}))
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(entry, "BATCHES", batch))
            stack.enter_context(patch.object(entry, "LEDGER", self.root / "ledger.json"))
            stack.enter_context(patch.object(entry, "RUNS", self.root / "runs"))
            stack.enter_context(patch.object(entry, "static_inputs", return_value=files))
            stack.enter_context(patch.object(entry, "require_clearance",
                                             return_value={"clearance_sha256": "synthetic"}))
            stack.enter_context(patch.object(entry, "clearance_gate",
                                             return_value={"ok": True,
                                                            "clearance_sha256": "synthetic"}))
            stack.enter_context(patch.object(
                entry, "execute_attempt",
                side_effect=entry.PreparationFailure(
                    "dependency_resolution_failed", "synthetic",
                    attempt_dir=self.root / "runs" / self.plan[0]["project"] / "attempt-001")))
            code = entry.run(limit=1)
        self.assertEqual(code, 0)
        ledger = json.loads((self.root / "ledger.json").read_text())
        self.assertEqual(ledger["records"][0]["status"], "technical_failure")
        self.assertEqual(ledger["records"][0]["calls"], 0)
        self.assertEqual(ledger["status"], "partial")


if __name__ == "__main__":
    unittest.main(verbosity=2)
