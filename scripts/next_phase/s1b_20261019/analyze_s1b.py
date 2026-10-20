"""ClawsGO Science Agent：S1b 450 轮结果的冻结规则汇总（只读）。

只读 state/s1b_20261019 下已登记的账本与逐轮原生结果：
- 不重跑、不补轮、不改写任何记录；
- RQ1 判据按有效协议 v1.2 的四条，逐 unit（项目 × K 条件）判定；
- 同时输出执行前封存的 40 个 unit 预测与实际观测的对照；
- 只做描述性汇总，不做组间因果归因。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
from collections import Counter, defaultdict

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_STATE = HERE.parent / "state" / "s1b_20261019"
SCHEMA = "pyhost-context/S1b-analysis/1.0"
CONDITIONS = ["K01_TZ", "K02_LC_ALL", "K03_LANG", "K04_HASH",
              "K05_NO_COLOR", "K06_TERM", "K07_COLUMNS", "K08_LINES"]


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def load_runs(state):
    ledger = read(state / "ledger.json")
    runs = {}
    for record in ledger["records"]:
        path = state / record["result"]["path"]
        if path.stat().st_size != record["result"]["bytes"]:
            raise SystemExit(f"result_size:{record['run_id']}")
        value = read(path)
        runs[record["run_id"]] = {"ledger": record, "detail": value}
    return ledger, runs


def vector(run):
    return run["detail"].get("vector") or {}


def run_valid(run):
    detail = run["detail"]
    return (run["ledger"].get("perturbation_valid") is True
            and detail.get("timed_out") is False
            and detail.get("session_exitstatus") is not None
            and detail.get("malformed_jsonl_lines") == 0)


def flips(a, b):
    """a 与 b 两个 nodeid→summary 向量的差异集合。"""
    keys = set(a) | set(b)
    return {key for key in keys if a.get(key) != b.get(key)}


def analyse_unit(project, condition, runs):
    main_b0 = sorted(r for r in runs if runs[r]["ledger"]["arm"] == "main"
                     and runs[r]["ledger"]["project"] == project
                     and runs[r]["ledger"]["condition"] == "B0")
    main_k = sorted(r for r in runs if runs[r]["ledger"]["arm"] == "main"
                    and runs[r]["ledger"]["project"] == project
                    and runs[r]["ledger"]["condition"] == condition)
    control = sorted(r for r in runs if runs[r]["ledger"]["arm"] == "control"
                     and runs[r]["ledger"]["project"] == project)
    if len(main_b0) != 5 or len(main_k) != 5 or len(control) != 45:
        return {"project": project, "condition": condition,
                "status": "incomplete_slots",
                "slots": {"main_b0": len(main_b0), "main_k": len(main_k),
                          "control": len(control)}}
    # B0 参照：5 轮主矩阵 B0 的逐节点多数结局；若自身不稳定则单列
    b0_vectors = [vector(runs[r]) for r in main_b0]
    b0_nodes = set().union(*(set(v) for v in b0_vectors))
    b0_outcome = {}
    b0_unstable = {}
    for node in b0_nodes:
        values = [v.get(node) for v in b0_vectors]
        counts = Counter(values)
        mode, mode_count = counts.most_common(1)[0]
        b0_outcome[node] = mode
        if mode_count != len(values):
            b0_unstable[node] = dict(counts)
    k_vectors = [vector(runs[r]) for r in main_k]
    k_identical = all(v == k_vectors[0] for v in k_vectors[1:])
    k_flip_sets = []
    for vector_k in k_vectors:
        keys = set(vector_k) | set(b0_outcome)
        k_flip_sets.append({node for node in keys
                            if vector_k.get(node) != b0_outcome.get(node)})
    identical_flip_sets = all(s == k_flip_sets[0] for s in k_flip_sets[1:])
    flip_set = sorted(k_flip_sets[0])
    # 45 次独立 B0 对照的逐节点稳定性
    control_flips = set()
    control_unstable = {}
    for node in set().union(*(set(vector(runs[r])) for r in control)):
        values = [vector(runs[r]).get(node) for r in control]
        counts = Counter(values)
        if len(counts) > 1:
            control_unstable[node] = dict(counts)
    for node in flip_set:
        if node in control_unstable:
            control_flips.add(node)
    valid_runs = all(run_valid(runs[r]) for r in main_k)
    timed_out = any(runs[r]["detail"].get("timed_out") for r in main_k)
    criteria = {
        "1_k_runs_identical": k_identical,
        "1_differs_from_b0": bool(flip_set),
        "2_flip_set_identical_across_5": identical_flip_sets,
        "3_no_control_instability_in_flip_set": not control_flips,
        "4_all_runs_valid_no_timeout": valid_runs and not timed_out,
    }
    return {
        "project": project, "condition": condition, "status": "evaluated",
        "slots": {"main_b0": 5, "main_k": 5, "control": 45},
        "flip_nodes": flip_set,
        "flip_node_count": len(flip_set),
        "criteria": criteria,
        "confirmed": all(criteria.values()),
        "b0_unstable_nodes": b0_unstable,
        "control_unstable_nodes": control_unstable,
        "control_instability_in_flip_set": sorted(control_flips),
        "k_run_durations_seconds": [round(runs[r]["detail"]["duration_seconds"], 3)
                                    for r in main_k],
        "k_returncodes": [runs[r]["detail"]["returncode"] for r in main_k],
        "k_collected": [runs[r]["detail"]["collected"] for r in main_k],
    }


def prediction_table(predictions, units):
    rows = []
    for row in predictions["predictions"]:
        key = (row["project"], row["key"])
        observed = units.get(key) or {}
        actual = ("confirmed" if observed.get("confirmed") else
                  "flipped_not_confirmed" if observed.get("flip_nodes") else
                  "no_flip" if observed.get("status") == "evaluated" else
                  observed.get("status", "missing"))
        predicted = row["predicted_outcome"]
        if actual not in ("confirmed", "flipped_not_confirmed", "no_flip"):
            hit = None
        elif predicted == "predicted_flip":
            hit = actual in ("confirmed", "flipped_not_confirmed")
        elif predicted == "predicted_no_effect":
            hit = actual == "no_flip"
        else:
            hit = None
        rows.append({
            "project": row["project"], "key": row["key"],
            "predicted_outcome": predicted, "confidence": row["confidence"],
            "actual": actual, "hit": hit,
            "flip_nodes": (observed.get("flip_nodes") or [])[:20],
            "flip_node_count": observed.get("flip_node_count", 0),
            "criteria": observed.get("criteria"),
            "mechanism": row.get("mechanism"),
        })
    summary = Counter(row["actual"] for row in rows)
    scored = [row for row in rows if row["hit"] is not None]
    return {
        "rows": rows,
        "actual_counts": dict(summary),
        "scored_cells": len(scored),
        "hits": sum(1 for row in scored if row["hit"]),
        "unknown_cells": sum(1 for row in rows if row["hit"] is None),
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=pathlib.Path, default=DEFAULT_STATE)
    parser.add_argument("--out", type=pathlib.Path)
    args = parser.parse_args(argv)
    state = args.state
    ledger, runs = load_runs(state)
    projects = sorted({r["ledger"]["project"] for r in runs.values()})
    units = {}
    for project in projects:
        for condition in CONDITIONS:
            result = analyse_unit(project, condition, runs)
            units[(project, condition)] = result
    predictions = read(HERE / "S1B_PREREGISTERED_PREDICTIONS_20261019.json")
    comparison = prediction_table(predictions, units)
    confirmed = [f"{u['project']} × {u['condition']}" for u in units.values()
                 if u.get("confirmed")]
    durations = defaultdict(list)
    for record in ledger["records"]:
        durations[record["project"]].append(record.get("duration_seconds") or 0)
    document = {
        "schema_version": SCHEMA,
        "batch_id": ledger.get("batch_id"),
        "ledger_status": ledger.get("status"),
        "commitment": ledger.get("commitment"),
        "schedule_sha256": ledger.get("schedule_sha256"),
        "slots_recorded": len(ledger["records"]),
        "units_evaluated": sum(1 for u in units.values() if u["status"] == "evaluated"),
        "confirmed_units": confirmed,
        "confirmed_count": len(confirmed),
        "units": [units[(p, c)] for p in projects for c in CONDITIONS],
        "prediction_comparison": comparison,
        "descriptive": {
            "runs_per_project": {p: len(v) for p, v in durations.items()},
            "median_seconds_per_project": {p: round(statistics.median(v), 3)
                                           for p, v in durations.items()},
            "max_seconds_per_project": {p: round(max(v), 3) for p, v in durations.items()},
            "timed_out_runs": sum(1 for r in runs.values() if r["detail"].get("timed_out")),
            "commitment_mismatch": sum(
                1 for r in runs.values()
                if r["ledger"].get("commitment") != ledger.get("commitment")),
        },
        "limitations": [
            "只做描述性汇总；组间工具链不同，不作因果归因。",
            "确认判据是操作性筛查，不是“不存在 flaky”的证明。",
            "预测对照以 unit 为单位；同一依赖链的翻转按机制归并，不按项目累加。",
        ],
    }
    out = args.out or (state / "S1B_ANALYSIS_20261019.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(json.dumps({"out": str(out), "slots": document["slots_recorded"],
                      "units": document["units_evaluated"],
                      "confirmed": confirmed,
                      "prediction": {k: comparison[k] for k in
                                     ("actual_counts", "scored_cells", "hits", "unknown_cells")}},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
