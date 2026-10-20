#!/usr/bin/env python3
"""ClawsGO Science Agent：S1b 准备包离线复算。

只读包内文件；不联网、不安装、不导入、不执行任何候选。
校验：包清单、五项目名单、逐项目工具链配置、450 轮调度、承诺哈希与审阅方输入绑定。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
PROJECTS = ["structlog", "rich-toolkit", "twine", "typer", "pytest-xdist"]
POSITIONS = {"structlog": 2, "rich-toolkit": 4, "twine": 7, "typer": 18, "pytest-xdist": 25}
PROFILES = {"structlog": "D-R1", "rich-toolkit": "D-R1", "twine": "D-R0",
            "typer": "S1a", "pytest-xdist": "S1a"}


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def canon(name):
    return re.sub(r"[-_.]+", "-", str(name)).lower()


def aggregate(files):
    return hashlib.sha256("".join(f"{p}:{files[p]}\n" for p in sorted(files)).encode()).hexdigest()


def check_manifest(problems):
    manifest = read(ROOT / "PACKAGE_MANIFEST.json")
    listed = {m["path"]: m for m in manifest["members"]}
    for name, item in listed.items():
        path = ROOT / name
        if not path.is_file():
            problems.append(f"member_missing:{name}")
        elif path.stat().st_size != item["bytes"] or sha(path) != item["sha256"]:
            problems.append(f"member_drift:{name}")
    on_disk = {str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
               if p.is_file() and p.name != "PACKAGE_MANIFEST.json"
               and "__pycache__" not in p.parts}
    for name in sorted(on_disk - set(listed)):
        problems.append(f"unlisted_member:{name}")
    return len(listed)


def check_roster(problems):
    roster = read(ROOT / "S1B_ROSTER_20261019.json")
    rows = roster.get("projects") or []
    if [r["project"] for r in rows] != PROJECTS:
        problems.append("roster_order")
    if [r["position"] for r in rows] != [2, 4, 7, 18, 25]:
        problems.append("roster_positions")
    if {r["project"]: r["profile"] for r in rows} != PROFILES:
        problems.append("roster_profiles")
    if roster.get("main_matrix", {}).get("runs") != 225 or \
            roster.get("independent_b0", {}).get("runs") != 225 or \
            roster.get("total_runs") != 450:
        problems.append("roster_budget")
    return roster


def check_toolchains(problems):
    doc = read(ROOT / "S1B_TOOLCHAINS_20261019.json")
    tools = doc.get("projects") or {}
    if sorted(tools) != sorted(PROJECTS):
        problems.append("toolchain_projects")
    for project, tool in tools.items():
        if tool.get("profile") != PROFILES[project]:
            problems.append(f"profile:{project}")
        files = tool.get("install_files") or []
        if not files or len({f["filename"] for f in files}) != len(files):
            problems.append(f"install_files:{project}")
        expected = {canon(k): str(v) for k, v in (tool.get("expected_distributions") or {}).items()}
        if expected.get("pyhost-observer") != "0.2.0":
            problems.append(f"observer:{project}")
        pytest_versions = [v for k, v in expected.items() if k == "pytest"]
        if pytest_versions != [tool["pytest_version"]]:
            problems.append(f"pytest_expected:{project}")
        loads = tool.get("plugin_loads") or []
        if tool["profile"].startswith("D-") and not loads:
            problems.append(f"plugins_missing:{project}")
        if tool["profile"] == "S1a" and loads:
            problems.append(f"plugins_changed:{project}")
        source = tool.get("source") or {}
        if not source.get("sha256") or not source.get("bytes"):
            problems.append(f"source_missing:{project}")
        if tool["profile"] != "D-R1" and (tool.get("extra_pytest_args") or []):
            problems.append(f"extra_args:{project}")
    rule = doc.get("amended_transition_rule") or {}
    if rule.get("applies_to_profiles") != ["D-R1"] or \
            rule.get("declared_pytest_version") != "9.1.1":
        problems.append("amendment_scope")
    if rule.get("sha256") != sha(ROOT / "inputs/pytest_runner_transition.py"):
        problems.append("amendment_module_sha")
    return doc


def check_schedule(problems):
    schedule = read(ROOT / "S1B_SCHEDULE_20261019.json")
    entries = schedule.get("entries") or []
    if len(entries) != 450:
        problems.append(f"schedule_size:{len(entries)}")
    main = [e for e in entries if e["arm"] == "main"]
    control = [e for e in entries if e["arm"] == "control"]
    if len(main) != 225 or len(control) != 225:
        problems.append("schedule_arms")
    for project in PROJECTS:
        if sum(e["project"] == project for e in main) != 45:
            problems.append(f"main_project:{project}")
        if sum(e["project"] == project for e in control) != 45:
            problems.append(f"control_project:{project}")
        for condition in ["B0"] + [f"K0{i}" for i in range(1, 9)]:
            if sum(e["project"] == project and e["condition"].startswith(condition)
                   for e in main) != 5:
                problems.append(f"main_condition:{project}:{condition}")
    ids = [e["run_id"] for e in entries]
    if len(ids) != len(set(ids)):
        problems.append("run_id_duplicate")
    if sorted(e["order"] for e in entries) != list(range(1, 451)):
        problems.append("schedule_order")
    if [e["label_sha256"] for e in entries] != sorted(e["label_sha256"] for e in entries):
        problems.append("schedule_not_sorted")
    for entry in entries:
        want = hashlib.sha256(f"{schedule['seed']}|{entry['label']}".encode()).hexdigest()
        if entry.get("label_sha256") != want:
            problems.append(f"label:{entry['run_id']}")
    recomputed = hashlib.sha256(
        json.dumps({k: v for k, v in schedule.items() if k != "schedule_sha256"},
                   sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if recomputed != schedule.get("schedule_sha256"):
        problems.append("schedule_sha256")
    return schedule


def check_commitment(problems):
    commitment = read(ROOT / "S1B_PRE_RUN_COMMITMENT_20261019.json")
    files = commitment.get("files") or {}
    if len(files) != 42:
        problems.append(f"commitment_files:{len(files)}")
    index = {path.name: path for path in ROOT.rglob("*") if path.is_file()}
    shipped = 0
    for path, digest in files.items():
        local = index.get(pathlib.PurePosixPath(path).name)
        if local is None:
            continue
        shipped += 1
        if sha(local) != digest:
            problems.append(f"commitment_drift:{path}")
    if aggregate(files) != commitment.get("pre_run_commitment_hash"):
        problems.append("commitment_aggregate")
    if commitment.get("schedule_sha256") != \
            read(ROOT / "S1B_SCHEDULE_20261019.json")["schedule_sha256"]:
        problems.append("commitment_schedule")
    return commitment, shipped


def check_reviewer_inputs(problems):
    closure = read(ROOT / "inputs/REVIEWER001_D_RESULTS_CLOSURE_20261019.json")
    binds = closure.get("binds") or {}
    present = 0
    for path, digest in binds.items():
        local = ROOT / "inputs" / pathlib.PurePosixPath(path).name
        if local.is_file():
            present += 1
            if sha(local) != digest:
                problems.append(f"reviewer_input_drift:{path}")
    return present, len(binds)


def check_sealed(problems, commitment):
    """执行前封存件必须存在、可解析、且已在承诺内。"""
    sealed = commitment.get("sealed_before_execution") or []
    if sorted(sealed) != ["S1B_PREREGISTERED_PREDICTIONS_20261019.json",
                          "S1B_REPAIR_MUTATION_ADJUDICATION_20261019.json"]:
        problems.append("sealed_list")
    for name in sealed:
        path = ROOT / name
        if not path.is_file():
            problems.append(f"sealed_missing:{name}")
            continue
        if not any(path.rsplit("/", 1)[-1] == name for path in commitment.get("files", {})):
            problems.append(f"sealed_not_in_commitment:{name}")
        document = read(path)
        if document.get("status") not in ("sealed_before_execution", "preregistered_not_executed"):
            problems.append(f"sealed_status:{name}")
    predictions = read(ROOT / "S1B_PREREGISTERED_PREDICTIONS_20261019.json")
    rows = predictions.get("predictions") or []
    if len(rows) != 40:
        problems.append(f"prediction_cells:{len(rows)}")
    projects = {row["project"] for row in rows}
    if projects != set(PROJECTS):
        problems.append("prediction_projects")
    keys = {row["key"] for row in rows}
    if len(keys) != 8:
        problems.append(f"prediction_keys:{len(keys)}")
    for row in rows:
        if row["predicted_outcome"] not in ("predicted_flip", "predicted_no_effect",
                                            "predicted_unknown"):
            problems.append(f"prediction_outcome:{row['project']}:{row['key']}")
        if row["predicted_outcome"] == "predicted_flip" and row["confidence"] == "low":
            problems.append(f"prediction_flip_low_confidence:{row['project']}:{row['key']}")
    adjudication = read(ROOT / "S1B_REPAIR_MUTATION_ADJUDICATION_20261019.json")
    if adjudication.get("execution_status", {}).get("repair_suites_run") != 0:
        problems.append("adjudication_claims_runs")
    if [c["case_id"] for c in adjudication.get("cases", [])] != ["A", "B", "C"]:
        problems.append("adjudication_cases")
    return {"prediction_cells": len(rows), "sealed_files": len(sealed)}


def main():
    problems = []
    members = check_manifest(problems)
    roster = check_roster(problems)
    toolchains = check_toolchains(problems)
    schedule = check_schedule(problems)
    commitment, shipped = check_commitment(problems)
    reviewer_present, reviewer_total = check_reviewer_inputs(problems)
    sealed = check_sealed(problems, commitment)
    summary = {
        "sealed": sealed,
        "ok": not problems, "problems": problems,
        "manifest_members": members,
        "projects": len(roster.get("projects") or []),
        "schedule_runs": len(schedule.get("entries") or []),
        "schedule_sha256": schedule.get("schedule_sha256"),
        "pre_run_commitment_hash": commitment.get("pre_run_commitment_hash"),
        "commitment_files": len(commitment.get("files") or {}),
        "commitment_files_shipped_and_verified": shipped,
        "reviewer_inputs_shipped_and_verified": f"{reviewer_present}/{reviewer_total}",
        "toolchain_install_files": {p: len(t["install_files"])
                                    for p, t in toolchains["projects"].items()},
        "candidate_runs_or_installs": 0,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
