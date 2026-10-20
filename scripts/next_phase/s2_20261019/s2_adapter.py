"""ClawsGO Science Agent：S2 恢复复核适配器（准备件；执行须另行签署）。

职责边界：
- 只消费本目录的五份输入与预跑承诺；
- 授权门只接受 Reviewer 001 签署、绑定本承诺哈希的 S2 放行件；
- 每单位两块 A1→B→A2，块间按冻结调度（块一哈希序、块二严格反序）；
- 逐轮记录完整原始结果、三视图与账本，不修改候选源码与测试；
- 判定只按预注册的 C0—C7 规则，等级按实际结果机械登记。

本文件本身不执行任何候选；``--status`` 可离线核验输入与承诺。
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import pathlib
import sys
import s2_review_guards as reviewed

HERE = pathlib.Path(__file__).resolve().parent
PROJECT_ROOT = pathlib.Path(os.environ.get("PYHOST_PROJECT")
                             or HERE.parents[3]).resolve()
PROJECT = PROJECT_ROOT / "pyhost-context"
NEXT = PROJECT / "extension/next_phase"
WS = PROJECT_ROOT
S1B = NEXT / "s1b_20261019"
INPUTS = {
    "units": HERE / "S2_UNITS_20261019.json",
    "roster": HERE / "S2_ROSTER_20261019.json",
    "schedule": HERE / "S2_SCHEDULE_20261019.json",
    "toolchains": HERE / "S2_TOOLCHAINS_20261019.json",
    "rules": HERE / "S2_RULES_20261019.json",
    "read_chains": HERE / "S2_READ_CHAINS_20261019.json",
    "commitment": HERE / "S2_PRE_RUN_COMMITMENT_20261019.json",
    "anchors": HERE / "S2_REVIEW_ANCHORS_20261020.json",
}
BATCH = "next-S2-20261019"
DECISION = "APPROVED_FOR_S2_EXECUTION"
CLEARANCE_SCHEMA = "pyhost-context/reviewer001-S2-clearance/1.0"
STATE_ROOT_REL = "pyhost-context/extension/next_phase/state/s2_20261019"
TOTAL_RUNS = 30
BLOCKS = 2


class Refused(RuntimeError):
    pass


class TechnicalFailure(RuntimeError):
    pass


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8")
    temp.replace(path)


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canon(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def aggregate(files):
    return sha_text("".join(f"{path}:{digest}\n" for path, digest in sorted(files.items())))


def load_inputs():
    return {name: read(path) for name, path in INPUTS.items()}


# ---------------------------------------------------------------- 输入校验

def validate_units(units):
    problems = []
    docs = units.get("units") or []
    if units.get("batch_id") != BATCH:
        problems.append("units_batch")
    if len(docs) != 5:
        problems.append("units_count")
    for doc in docs:
        reg = doc.get("registered") or {}
        if doc.get("unit_id") != f"{doc.get('project')}/{doc.get('condition')}":
            problems.append("unit_id:" + str(doc.get("unit_id")))
        if not reg.get("flip_nodes"):
            problems.append("flip_empty:" + str(doc.get("unit_id")))
        if reg.get("flip_node_count") != len(reg.get("flip_nodes") or []):
            problems.append("flip_count:" + str(doc.get("unit_id")))
        if reg.get("flip_nodes_sha256") != sha_text("\n".join(sorted(reg.get("flip_nodes") or []))):
            problems.append("flip_hash:" + str(doc.get("unit_id")))
        sigs = reg.get("failure_signatures") or {}
        hashes = reg.get("failure_signature_sha256") or {}
        if sorted(sigs) != sorted(reg.get("flip_nodes") or []):
            problems.append("signature_set:" + str(doc.get("unit_id")))
        for node, text in sigs.items():
            if hashes.get(node) != sha_text(text):
                problems.append(f"signature_hash:{doc.get('unit_id')}:{node}")
        phases = reg.get("phase_outcomes") or {}
        if sorted(phases) != sorted(reg.get("flip_nodes") or []):
            problems.append("phase_set:" + str(doc.get("unit_id")))
        identity = reg.get("identity_nodes") or []
        if reg.get("identity_nodes_sha256") != sha_text("\n".join(identity)):
            problems.append("identity_hash:" + str(doc.get("unit_id")))
        if reg.get("identity_nodes_count") != len(identity):
            problems.append("identity_count:" + str(doc.get("unit_id")))
    return problems


def validate_roster(roster, units):
    problems = []
    if roster.get("batch_id") != BATCH:
        problems.append("roster_batch")
    if roster.get("total_units") != 5 or roster.get("total_runs") != TOTAL_RUNS:
        problems.append("roster_totals")
    unit_docs = {u["unit_id"]: u for u in units["units"]}
    rows = roster.get("units") or []
    if [row.get("unit_id") for row in rows] != [u["unit_id"] for u in units["units"]]:
        problems.append("roster_units")
    for row in rows:
        doc = unit_docs.get(row["unit_id"])
        if doc is None:
            problems.append("roster_unknown:" + str(row.get("unit_id")))
            continue
        for key in ("project", "position", "profile", "condition"):
            if row.get(key) != doc.get(key):
                problems.append(f"roster_field:{row['unit_id']}:{key}")
        if row.get("flip_node_count") != doc["registered"]["flip_node_count"]:
            problems.append("roster_flip_count:" + row["unit_id"])
    return problems


def validate_schedule(schedule, units, roster):
    problems = []
    if schedule.get("batch_id") != BATCH or schedule.get("blocks") != BLOCKS:
        problems.append("schedule_batch_or_blocks")
    entries = schedule.get("entries") or []
    if len(entries) != TOTAL_RUNS or schedule.get("total_runs") != TOTAL_RUNS:
        problems.append("schedule_size")
    recomputed = sha_text(json.dumps({k: v for k, v in schedule.items()
                                      if k != "schedule_sha256"},
                                     sort_keys=True, ensure_ascii=False))
    if recomputed != schedule.get("schedule_sha256"):
        problems.append("schedule_sha256")
    ids = [e.get("run_id") for e in entries]
    if len(ids) != len(set(ids)):
        problems.append("schedule_run_id_duplicate")
    if sorted(e.get("order") for e in entries) != list(range(1, TOTAL_RUNS + 1)):
        problems.append("schedule_order")
    seed = schedule.get("seed")
    unit_docs = {u["unit_id"]: u for u in units["units"]}
    if [u["unit_id"] for u in units["units"]] != [r["unit_id"] for r in roster["units"]]:
        problems.append("units_roster_order")
    block_units = {1: [], 2: []}
    for entry in entries:
        unit = unit_docs.get(entry.get("unit_id"))
        if unit is None:
            problems.append("schedule_unknown_unit:" + str(entry.get("run_id")))
            continue
        if entry.get("project") != unit["project"] or entry.get("position") != unit["position"]:
            problems.append("schedule_identity:" + entry["run_id"])
        want_condition = unit["condition"] if entry["phase"] == "B" else "B0"
        if entry.get("condition") != want_condition:
            problems.append("schedule_condition:" + entry["run_id"])
        if entry.get("target_condition") != unit["condition"]:
            problems.append("schedule_target:" + entry["run_id"])
        if entry.get("label_sha256") != sha_text(f"{seed}|{entry.get('label')}"):
            problems.append("schedule_label:" + entry["run_id"])
        block_units.setdefault(entry["block"], []).append(entry)
    hash_order = sorted(unit_docs, key=lambda uid: sha_text(f"{seed}|unit|{uid}"))
    for block in (1, 2):
        rows = block_units.get(block) or []
        if len(rows) != 15:
            problems.append(f"block_size:{block}")
            continue
        seen = []
        for index in range(0, len(rows), 3):
            chunk = rows[index:index + 3]
            if [c["phase"] for c in chunk] != ["A1", "B", "A2"]:
                problems.append(f"block_phase_order:{block}:{index // 3}")
            if len({c["unit_id"] for c in chunk}) != 1:
                problems.append(f"block_unit_mix:{block}:{index // 3}")
            seen.append(chunk[0]["unit_id"])
        expected = hash_order if block == 1 else list(reversed(hash_order))
        if seen != expected:
            problems.append(f"block_unit_order:{block}")
        if [c["order"] for c in rows] != sorted(c["order"] for c in rows):
            problems.append(f"block_order_field:{block}")
    if block_units.get(1) and block_units.get(2):
        if block_units[1][0]["order"] >= block_units[2][0]["order"]:
            problems.append("block_sequence")
    per_unit = {uid: sum(1 for e in entries if e.get("unit_id") == uid) for uid in unit_docs}
    if any(count != 6 for count in per_unit.values()):
        problems.append("per_unit_runs")
    return problems


def validate_toolchains(toolchains, units):
    problems = []
    if toolchains.get("batch_id") != BATCH:
        problems.append("toolchains_batch")
    projects = sorted({u["project"] for u in units["units"]})
    if sorted(toolchains.get("projects") or {}) != projects:
        problems.append("toolchains_projects")
    for project, tool in (toolchains.get("projects") or {}).items():
        if tool.get("project") != project:
            problems.append("toolchain_name:" + project)
        if not tool.get("install_files"):
            problems.append("toolchain_install_empty:" + project)
        expected = tool.get("expected_distributions") or {}
        if "pyhost-observer" not in {k.lower() for k in expected}:
            problems.append("toolchain_observer:" + project)
        doc = next(u for u in units["units"] if u["project"] == project)
        if tool.get("profile") != doc["profile"]:
            problems.append("toolchain_profile:" + project)
    return problems


def validate_rules(rules):
    problems = []
    if rules.get("batch_id") != BATCH:
        problems.append("rules_batch")
    if rules.get("design", {}).get("total_runs") != TOTAL_RUNS:
        problems.append("rules_total")
    ids = [c["id"] for c in rules.get("block_criteria") or []]
    for want in ("C0_baseline_reproduction", "C1_validity", "C2_identity",
                 "C3_baseline_stability", "C4_flip_set", "C5_signature",
                 "C6_stage", "C7_restoration"):
        if want not in ids:
            problems.append("rules_criterion:" + want)
    if rules.get("authorization", {}).get("S2_execution") is not False:
        problems.append("rules_authorization_must_be_false")
    return problems


def validate_inputs(inputs):
    problems = []
    problems += validate_units(inputs["units"])
    problems += validate_roster(inputs["roster"], inputs["units"])
    problems += validate_schedule(inputs["schedule"], inputs["units"], inputs["roster"])
    problems += validate_toolchains(inputs["toolchains"], inputs["units"])
    problems += validate_rules(inputs["rules"])
    problems += reviewed.validate_consumed_schedule(sys.modules[__name__], inputs)
    return problems


def verify_commitment(commitment):
    return reviewed.verify_commitment(sys.modules[__name__], commitment)


# ---------------------------------------------------------------- 授权门

def validate_clearance(document, commitment, actual_bindings):
    problems = []
    if document.get("decision") != DECISION:
        problems.append("decision")
    if document.get("schema_version") != CLEARANCE_SCHEMA:
        problems.append("schema_version")
    if document.get("signed_by") != "Reviewer 001":
        problems.append("signed_by")
    if not document.get("signed_at_utc"):
        problems.append("signed_at_utc")
    auth = document.get("authorization") or {}
    if auth.get("S2_execution") is not True:
        problems.append("S2_not_authorized")
    for key in ("S3", "repro", "S1b_replay", "new_samples", "new_contexts",
                "semantic_mutations"):
        if auth.get(key) is not False:
            problems.append("scope_not_bounded:" + key)
    scope = document.get("scope") or {}
    if scope.get("batch_id") != BATCH:
        problems.append("scope_batch")
    if scope.get("total_runs") != TOTAL_RUNS or scope.get("blocks") != BLOCKS:
        problems.append("scope_runs")
    if scope.get("schedule_sha256") != commitment.get("schedule_sha256"):
        problems.append("scope_schedule")
    if scope.get("state_root") != STATE_ROOT_REL:
        problems.append("scope_state_root")
    if document.get("pre_run_commitment_hash") != commitment.get("pre_run_commitment_hash"):
        problems.append("commitment_hash")
    if document.get("bindings") != actual_bindings:
        problems.append("binding_drift")
    if scope.get("unit_ids") != reviewed.UNIT_IDS:
        problems.append("scope_unit_ids")
    if document.get("authorization", {}).get("registered_S2_analysis") is not True:
        problems.append("registered_S2_analysis_not_authorized")
    return problems


def gate(clearance_path, commitment, actual_bindings):
    path = pathlib.Path(clearance_path)
    if not path.is_file():
        raise Refused("S2_clearance_missing")
    document = read(path)
    problems = validate_clearance(document, commitment, actual_bindings)
    if document.get("commitment_document_sha256") != sha(INPUTS["commitment"]):
        problems.append("commitment_document_drift")
    if problems:
        raise Refused("S2_clearance_invalid:" + ",".join(problems))
    return {"ok": True, "clearance_sha256": sha(path),
            "commitment": commitment["pre_run_commitment_hash"],
            "state_root": str((WS / document["scope"]["state_root"]).resolve())}


# ---------------------------------------------------------------- 判定（纯函数）

def block_judgement(unit, runs, validity):
    return reviewed.block_judgement(sys.modules[__name__], unit, runs, validity)


def summary_counts(vector):
    counts = {}
    for value in vector.values():
        counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items()))


def unit_judgement(unit, blocks, gates=None):
    return reviewed.unit_judgement(unit, blocks, gates)


def adjudicate(units, records, gate_values=None):
    return reviewed.adjudicate(sys.modules[__name__], units, records, gate_values)


# ---------------------------------------------------------------- 执行

def run_schedule(inputs, *, state_root, clearance, runner=None, max_runs=None):
    return reviewed.run_schedule(sys.modules[__name__], inputs, state_root, clearance, runner, max_runs)


def load_ledger_records(state_root, inputs=None):
    return reviewed.load_ledger_records(sys.modules[__name__], state_root, inputs or load_inputs())


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--adjudicate", action="store_true")
    parser.add_argument("--state-root", type=pathlib.Path)
    parser.add_argument("--clearance", type=pathlib.Path)
    parser.add_argument("--max-runs", type=int)
    args = parser.parse_args(argv)
    inputs = load_inputs()
    if args.status:
        problems = validate_inputs(inputs) + verify_commitment(inputs["commitment"])
        print(json.dumps({"ok": not problems, "problems": problems,
                          "commitment": inputs["commitment"]["pre_run_commitment_hash"],
                          "schedule": inputs["schedule"]["schedule_sha256"],
                          "runs": len(inputs["schedule"]["entries"])},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if not problems else 1
    if args.adjudicate:
        if args.state_root is None:
            parser.error("--adjudicate 需要 --state-root")
        if args.clearance is None:
            parser.error("--adjudicate 需要同一批次 --clearance")
        reviewed.preflight(sys.modules[__name__], inputs, args.clearance, args.state_root)
        ledger, records = load_ledger_records(args.state_root, inputs)
        gate_values = {u["unit_id"]: {"schedule_gate": True, "scope_identity": True,
            "reading_chain": inputs["anchors"]["units"][u["unit_id"]].get("reading_chain_review_completed") is True}
            for u in inputs["units"]["units"]}
        out = adjudicate(inputs["units"], records, gate_values)
        write(pathlib.Path(args.state_root) / "adjudication.json", out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if not args.run:
        parser.error("需要 --status、--run 或 --adjudicate")
    if args.state_root is None or args.clearance is None:
        parser.error("--run 需要 --state-root 与 --clearance")
    result = run_schedule(inputs, state_root=args.state_root,
                          clearance=args.clearance, max_runs=args.max_runs)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.path[:0] = [str(PROJECT / "matrix/tooling"), str(PROJECT / "tooling"),
                    str(PROJECT / "tooling/admission_runner"),
                    str(PROJECT / "matrix/tooling/matrix_runner")]
    try:
        code = main()
    except Refused as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}, ensure_ascii=False))
        code = 2
    except Exception as exc:
        print(json.dumps({"status": "instrument_error",
                          "reason": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        code = 3
    raise SystemExit(code)
