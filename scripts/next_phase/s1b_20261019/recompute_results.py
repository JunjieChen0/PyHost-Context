#!/usr/bin/env python3
"""ClawsGO Science Agent：S1b 450 轮结果包离线复算。

只读包内文件；不重跑、不联网、不安装、不执行候选。
校验：包清单、账本与承诺、逐轮结果字节与哈希、冻结判据重算的确认单位、
B0 噪声底、预测对照，以及 `S1B_ANALYSIS_20261019.json` 的对应字段。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import analyze_s1b  # noqa: E402

STATE = ROOT / "state"
EXPECTED_CONFIRMED = [
    ("rich-toolkit", "K07_COLUMNS", 1),
    ("twine", "K06_TERM", 3),
    ("typer", "K05_NO_COLOR", 2),
    ("typer", "K06_TERM", 2),
    ("typer", "K07_COLUMNS", 152),
]
EXPECTED_NOISE_FLOOR = 0


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def main():
    problems = []
    manifest = read(ROOT / "PACKAGE_MANIFEST.json")
    for item in manifest["members"]:
        path = ROOT / item["path"]
        if not path.is_file():
            problems.append(f"member_missing:{item['path']}")
        elif path.stat().st_size != item["bytes"] or sha(path) != item["sha256"]:
            problems.append(f"member_drift:{item['path']}")

    ledger = read(STATE / "ledger.json")
    clearance = read(ROOT / "REVIEWER001_S1B_CLEARANCE_20261019.json")
    if ledger.get("status") != "completed":
        problems.append("ledger_status")
    if len(ledger.get("records") or []) != 450:
        problems.append("ledger_records")
    if ledger.get("commitment") != clearance.get("pre_run_commitment_hash"):
        problems.append("commitment_vs_clearance")
    if ledger.get("schedule_sha256") != clearance.get("scope", {}).get("schedule_sha256"):
        problems.append("schedule_vs_clearance")
    shipped = 0
    for record in ledger["records"]:
        for key in ("result", "assessment"):
            entry = record.get(key) or {}
            path = STATE / entry["path"]
            if not path.is_file():
                problems.append(f"missing:{entry['path']}")
            elif path.stat().st_size != entry["bytes"] or sha(path) != entry["sha256"]:
                problems.append(f"drift:{entry['path']}")
            else:
                shipped += 1

    _ledger, runs = analyze_s1b.load_runs(STATE)
    units = {}
    for project in sorted({r["ledger"]["project"] for r in runs.values()}):
        for condition in analyze_s1b.CONDITIONS:
            units[(project, condition)] = analyze_s1b.analyse_unit(project, condition, runs)
    confirmed = sorted((u["project"], u["condition"], u["flip_node_count"])
                       for u in units.values() if u.get("confirmed"))
    if confirmed != sorted(EXPECTED_CONFIRMED):
        problems.append(f"confirmed_mismatch:{confirmed}")
    evaluated = sum(1 for u in units.values() if u["status"] == "evaluated")
    if evaluated != 40:
        problems.append(f"units_evaluated:{evaluated}")
    unstable = set()
    for u in units.values():
        unstable |= set(u.get("control_unstable_nodes") or {})
        unstable |= set(u.get("b0_unstable_nodes") or {})
    noise = len(unstable)
    if noise != EXPECTED_NOISE_FLOOR:
        problems.append(f"noise_floor:{noise}")
    directions = {}
    for u in units.values():
        for node in u.get("flip_nodes") or []:
            directions.setdefault((u["project"], u["condition"]), []).append(node)
    analysis = read(STATE / "S1B_ANALYSIS_20261019.json")
    if sorted(analysis.get("confirmed_units") or []) != sorted(
            f"{p} × {c}" for p, c, _ in EXPECTED_CONFIRMED):
        problems.append("analysis_confirmed")
    comparison = analysis.get("prediction_comparison") or {}
    if comparison.get("hits") != 34 or comparison.get("scored_cells") != 38:
        problems.append("analysis_prediction")
    timed_out = sum(1 for r in runs.values() if r["detail"].get("timed_out"))
    if timed_out:
        problems.append(f"timed_out:{timed_out}")
    invalid = sum(1 for r in runs.values()
                  if r["ledger"].get("perturbation_valid") is not True)
    if invalid:
        problems.append(f"invalid_perturbation:{invalid}")

    print(json.dumps({
        "ok": not problems,
        "problems": problems,
        "slots": len(ledger["records"]),
        "files_verified": shipped,
        "units_evaluated": evaluated,
        "confirmed": [{"project": p, "condition": c, "flip_nodes": n}
                      for p, c, n in sorted(EXPECTED_CONFIRMED)],
        "confirmed_units": len(EXPECTED_CONFIRMED),
        "mechanisms": 2,
        "b0_noise_floor_unstable_nodes": noise,
        "prediction": {k: comparison.get(k) for k in
                       ("scored_cells", "hits", "unknown_cells", "actual_counts")},
        "timed_out": timed_out,
        "invalid_perturbation": invalid,
        "candidate_reruns": 0,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
