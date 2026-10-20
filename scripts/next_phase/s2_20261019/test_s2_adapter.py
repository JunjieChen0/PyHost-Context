"""ClawsGO Science Agent：S2 适配器的合成回归（不运行候选、不联网）。"""
from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import unittest

import s2_adapter as adapter

HERE = pathlib.Path(__file__).resolve().parent
NODES = ["t::a", "t::b", "t::c"]
FLIP = ["t::a", "t::b"]
SIGS = {"t::a": "AssertionError: a", "t::b": "AssertionError: b"}
PHASES = {"t::a": {"setup": "passed", "call": "failed", "teardown": "passed"},
          "t::b": {"setup": "passed", "call": "failed", "teardown": "passed"}}
BASELINE = {node: "passed" for node in NODES}


def sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canon(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def unit():
    return {
        "unit_id": "demo/K06_TERM", "project": "demo", "position": 1,
        "profile": "S1a", "condition": "K06_TERM",
        "registered": {
            "flip_nodes": list(FLIP),
            "flip_node_count": len(FLIP),
            "flip_nodes_sha256": sha_text("\n".join(sorted(FLIP))),
            "failure_signatures": dict(SIGS),
            "failure_signature_sha256": {n: sha_text(t) for n, t in SIGS.items()},
            "phase_outcomes": copy.deepcopy(PHASES),
            "identity_nodes": list(NODES),
            "identity_nodes_count": len(NODES),
            "identity_nodes_sha256": sha_text("\n".join(NODES)),
            "baseline_vector_sha256": sha_text(canon(BASELINE)),
            "source_tree_hash": "a" * 64,
            "baseline_summary_flip_nodes": {n: "passed" for n in FLIP},
        },
    }


def detail(vector, sigs=None, phases=None):
    sigs = sigs or {}
    phases = phases or {}
    return {
        "vector": dict(vector),
        "returncode": 1 if any(v == "failed" for v in vector.values()) else 0,
        "session_exitstatus": 1 if any(v == "failed" for v in vector.values()) else 0,
        "timed_out": False, "malformed_jsonl_lines": 0, "collected": len(vector),
        "round_start_state": {"source_tree_hash": "a" * 64},
        "child_pid": 10, "child_pgid": 10,
        "process_group_cleanup": {"performed": False, "target_pgid": 10, "group_alive_at_child_exit": False},
        "network": {"pytest_process_pid": "10", "pytest_process_covered": True,
                    "processes": {"10": {"init_seen": True, "end_seen": True}}},
        "per_test": {node: {"failure_signature": sigs.get(node), "raw_complete": True,
                            "stage_integrity": [], "phases": {}, "phase_details": {}} for node in vector},
        "summaries": {node: {"phase_outcomes": phases.get(node)}
                      for node in vector},
    }


def block(a1=None, b=None, a2=None, valid=None, sigs=None, phases=None):
    a1d = detail(a1 or BASELINE)
    bdefault = {**BASELINE, "t::a": "failed", "t::b": "failed"}
    bd = detail(b if b is not None else bdefault,
                sigs=SIGS if sigs is None else sigs,
                phases=PHASES if phases is None else phases)
    a2d = detail(a2 or BASELINE)
    runs = {"A1": a1d, "B": bd, "A2": a2d}
    validity = valid or {"A1": True, "B": True, "A2": True}
    return adapter.block_judgement(unit(), runs, validity)


class BlockJudgementTests(unittest.TestCase):
    def test_confirmed(self):
        result = block()
        self.assertEqual(result["status"], "BLOCK_CONFIRMED")
        self.assertTrue(all(result["criteria"].values()))

    def test_no_effect(self):
        result = block(b=BASELINE)
        self.assertEqual(result["status"], "NOT_RECOVERED_NO_EFFECT")

    def test_flip_set_drift(self):
        result = block(b={**BASELINE, "t::a": "failed", "t::b": "failed",
                          "t::c": "failed"})
        self.assertEqual(result["status"], "NOT_RECOVERED_FLIP_SET_DRIFT")
        self.assertEqual(result["details"]["flip_extra"], ["t::c"])

    def test_signature_drift(self):
        sigs = dict(SIGS, **{"t::b": "AssertionError: changed"})
        result = block(b={**BASELINE, "t::a": "failed", "t::b": "failed"}, sigs=sigs)
        self.assertEqual(result["status"], "NOT_RECOVERED_SIGNATURE_DRIFT")
        self.assertEqual(result["details"]["signature_drift_nodes"], ["t::b"])

    def test_stage_drift(self):
        phases = copy.deepcopy(PHASES)
        phases["t::a"] = {"setup": "failed", "call": "failed", "teardown": "passed"}
        result = block(b={**BASELINE, "t::a": "failed", "t::b": "failed"},
                       phases=phases)
        self.assertEqual(result["status"], "NOT_RECOVERED_STAGE_DRIFT")

    def test_a2_residual(self):
        result = block(a2={**BASELINE, "t::a": "failed"})
        self.assertEqual(result["status"], "NOT_RECOVERED_A2_RESIDUAL")
        self.assertEqual(result["details"]["a1_a2_differing_nodes"], ["t::a"])

    def test_baseline_instability_outside_flip_set(self):
        result = block(a2={**BASELINE, "t::c": "failed"})
        self.assertEqual(result["status"], "NOT_EVALUABLE_BASELINE_INSTABILITY")
        self.assertEqual(result["details"]["a1_a2_nonflip_differing_nodes"], ["t::c"])

    def test_baseline_drift(self):
        drifted = {**BASELINE, "t::c": "failed"}
        result = block(a1=drifted, a2=drifted)
        self.assertEqual(result["status"], "NOT_EVALUABLE_BASELINE_DRIFT")

    def test_identity_drift(self):
        shorter = {"t::a": "passed", "t::b": "passed"}
        result = block(a1=shorter, a2=shorter)
        self.assertEqual(result["status"], "NOT_EVALUABLE_IDENTITY_DRIFT")

    def test_invalid_run(self):
        result = block(valid={"A1": True, "B": False, "A2": True})
        self.assertEqual(result["status"], "NOT_EVALUABLE_VALIDITY")

    def test_boundary_does_not_claim_negative_on_invalid(self):
        result = block(valid={"A1": True, "B": False, "A2": True}, b=BASELINE)
        self.assertEqual(result["status"], "NOT_EVALUABLE_VALIDITY")
        self.assertNotIn("NOT_RECOVERED", result["status"])


class UnitJudgementTests(unittest.TestCase):
    def statuses(self, first, second):
        return adapter.unit_judgement(unit(), {1: {"status": first},
                                               2: {"status": second}}, {"schedule_gate": True, "scope_identity": True, "reading_chain": True})

    def test_two_blocks(self):
        summary = self.statuses("BLOCK_CONFIRMED", "BLOCK_CONFIRMED")
        self.assertEqual(summary["unit_status"], "RECOVERED_TWO_BLOCKS")
        self.assertEqual(summary["proposed_level"], "E4")

    def test_one_block(self):
        summary = self.statuses("BLOCK_CONFIRMED", "NOT_EVALUABLE_VALIDITY")
        self.assertEqual(summary["unit_status"], "RECOVERED_ONE_BLOCK")
        self.assertEqual(summary["proposed_level"], "E3")

    def test_inconsistent(self):
        summary = self.statuses("BLOCK_CONFIRMED", "NOT_RECOVERED_NO_EFFECT")
        self.assertEqual(summary["unit_status"], "RECOVERY_INCONSISTENT")
        self.assertEqual(summary["proposed_level"], "E2")

    def test_not_recovered(self):
        summary = self.statuses("NOT_RECOVERED_FLIP_SET_DRIFT",
                                "NOT_RECOVERED_NO_EFFECT")
        self.assertEqual(summary["unit_status"], "NOT_RECOVERED")
        self.assertEqual(summary["proposed_level"], "E1")

    def test_not_evaluable(self):
        summary = self.statuses("NOT_EVALUABLE_BASELINE_DRIFT",
                                "NOT_EVALUABLE_VALIDITY")
        self.assertEqual(summary["unit_status"], "NOT_EVALUABLE")
        self.assertEqual(summary["proposed_level"], "E2")


class InputValidationTests(unittest.TestCase):
    def inputs(self):
        return {name: adapter.read(adapter.INPUTS[name])
                for name in ("units", "roster", "schedule", "toolchains", "rules")}

    def test_real_inputs(self):
        self.assertEqual(adapter.validate_inputs(self.inputs()), [])

    def test_schedule_tamper_detected(self):
        inputs = self.inputs()
        inputs["schedule"]["entries"][0]["condition"] = "K01_TZ"
        self.assertIn("schedule_sha256", adapter.validate_inputs(inputs))

    def test_schedule_reorder_detected(self):
        inputs = self.inputs()
        schedule = inputs["schedule"]
        entries = schedule["entries"]
        entries[0:3], entries[3:6] = entries[3:6], entries[0:3]
        for index, entry in enumerate(entries, 1):
            entry["order"] = index
        schedule["schedule_sha256"] = adapter.sha_text(
            json.dumps({k: v for k, v in schedule.items() if k != "schedule_sha256"},
                       sort_keys=True, ensure_ascii=False))
        problems = adapter.validate_inputs(inputs)
        self.assertIn("block_unit_order:1", problems)

    def test_unit_signature_tamper_detected(self):
        inputs = self.inputs()
        unit = inputs["units"]["units"][0]
        node = unit["registered"]["flip_nodes"][0]
        unit["registered"]["failure_signatures"][node] = "tampered"
        self.assertTrue(any(p.startswith("signature_hash:")
                            for p in adapter.validate_inputs(inputs)))

    def test_rules_criteria_required(self):
        inputs = self.inputs()
        inputs["rules"]["block_criteria"] = inputs["rules"]["block_criteria"][:-1]
        self.assertTrue(any(p.startswith("rules_criterion:")
                            for p in adapter.validate_inputs(inputs)))


class ClearanceTests(unittest.TestCase):
    def commitment(self):
        return {"pre_run_commitment_hash": "abc", "schedule_sha256": "def",
                "files": {"x": "y"}}

    def document(self, **overrides):
        doc = {
            "decision": adapter.DECISION,
            "schema_version": adapter.CLEARANCE_SCHEMA,
            "signed_by": "Reviewer 001",
            "signed_at_utc": "2026-10-19T00:00:00Z",
            "authorization": {"registered_S2_analysis": True, "S2_execution": True, "S3": False, "repro": False,
                              "S1b_replay": False, "new_samples": False,
                              "new_contexts": False, "semantic_mutations": False},
            "scope": {"batch_id": adapter.BATCH, "total_runs": 30, "blocks": 2,
                      "schedule_sha256": "def",
                      "state_root": adapter.STATE_ROOT_REL, "unit_ids": adapter.reviewed.UNIT_IDS},
            "pre_run_commitment_hash": "abc",
            "bindings": {"x": "y"},
        }
        doc.update(overrides)
        return doc

    def test_valid(self):
        self.assertEqual(adapter.validate_clearance(
            self.document(), self.commitment(), {"x": "y"}), [])

    def test_requires_s2_true(self):
        doc = self.document(authorization={"S2_execution": False, "S3": False,
                                           "repro": False, "S1b_replay": False,
                                           "new_samples": False, "new_contexts": False,
                                           "semantic_mutations": False})
        self.assertIn("S2_not_authorized",
                      adapter.validate_clearance(doc, self.commitment(), {"x": "y"}))

    def test_rejects_extra_authorization(self):
        doc = self.document(authorization={"S2_execution": True, "S3": True,
                                           "repro": False, "S1b_replay": False,
                                           "new_samples": False, "new_contexts": False,
                                           "semantic_mutations": False})
        self.assertIn("scope_not_bounded:S3",
                      adapter.validate_clearance(doc, self.commitment(), {"x": "y"}))

    def test_rejects_wrong_schedule(self):
        doc = self.document(scope={"batch_id": adapter.BATCH, "total_runs": 30,
                                   "blocks": 2, "schedule_sha256": "other",
                                   "state_root": adapter.STATE_ROOT_REL})
        self.assertIn("scope_schedule",
                      adapter.validate_clearance(doc, self.commitment(), {"x": "y"}))

    def test_rejects_binding_drift(self):
        self.assertIn("binding_drift",
                      adapter.validate_clearance(self.document(), self.commitment(),
                                                 {"x": "z"}))


if __name__ == "__main__":
    unittest.main()
