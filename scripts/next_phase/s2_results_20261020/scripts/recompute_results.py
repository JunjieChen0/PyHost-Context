"""ClawsGO Science Agent：S2 结果包只读复算（不重跑候选、不联网）。

- 核对账本与全部结果/assessment/七类原始产物的字节；
- 用已安装的原判定程序（s2_adapter.adjudicate）从包内原始结果重算判定，
  与 results/adjudication.json 逐字节比较；
- 重数同标签原生变化并与 CSV 比较。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def find_workspace():
    for parent in (HERE, *HERE.parents):
        if (parent / "pyhost-context/extension/next_phase/s2_20261019").is_dir():
            return parent
    return None


def load_adapter(workspace):
    path = (workspace / "pyhost-context/extension/next_phase/s2_20261019/"
            "s2_adapter.py")
    os.environ.setdefault("PYHOST_PROJECT", str(workspace))
    sys.path.insert(0, str(path.parent))
    sys.path.insert(0, str(path.parent / "reference_parser"))
    spec = importlib.util.spec_from_file_location("s2_adapter_installed", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["s2_adapter_installed"] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=pathlib.Path)
    args = parser.parse_args()
    workspace = args.workspace or find_workspace()
    checks = []

    def check(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    ledger = read(ROOT / "results/ledger.json")
    adjudication = read(ROOT / "results/adjudication.json")
    units = read(ROOT / "units/S2_UNITS_20261019.json")
    records = ledger["records"]
    check("ledger_records", len(records) == 30, len(records))
    check("ledger_attempts", len(ledger.get("attempts") or []) == 30)
    check("ledger_completed", ledger.get("status") == "completed")
    check("ledger_commitment",
          ledger.get("commitment") ==
          "5f85571e834db8e62907ab8c28949743eb84e0b496c80c482c4978196c1c9a52")
    check("ledger_schedule",
          ledger.get("schedule_sha256") ==
          "9474b2a4920baeaf75a7e8421a64ef9dfbc862ccd9414c2c9da1b8efdf88df39")

    rebuilt = {}
    log_files = 0
    problems = []
    for row in records:
        run_id = row["run_id"]
        path = ROOT / "raw/results" / run_id / pathlib.Path(row["result"]["path"]).name
        apath = ROOT / "raw/results" / run_id / pathlib.Path(row["assessment"]["path"]).name
        if (not path.is_file() or path.stat().st_size != row["result"]["bytes"]
                or sha(path) != row["result"]["sha256"]):
            problems.append("result:" + run_id)
            continue
        if (not apath.is_file() or apath.stat().st_size != row["assessment"]["bytes"]
                or sha(apath) != row["assessment"]["sha256"]):
            problems.append("assessment:" + run_id)
            continue
        detail = read(path)
        assessment = read(apath)
        rebuilt[run_id] = {"detail": detail, "valid": assessment.get("valid") is True,
                           "row": row}
        for item in assessment.get("evidence_files") or []:
            log = ROOT / "raw/logs" / pathlib.Path(item["path"]).name
            log_files += 1
            if (not log.is_file() or log.stat().st_size != item["bytes"]
                    or sha(log) != item["sha256"]):
                problems.append("raw:" + run_id + ":" + log.name)
    check("raw_and_results_bytes", not problems, problems[:20] or None)
    check("raw_log_file_count", log_files == 210, log_files)

    if workspace is None:
        check("workspace", False, "workspace_not_found")
    else:
        adapter = load_adapter(workspace)
        anchors_path = (workspace / "pyhost-context/extension/next_phase/"
                        "s2_20261019/S2_REVIEW_ANCHORS_20261020.json")
        anchors = read(anchors_path) if anchors_path.is_file() else {"units": {}}
        gate_values = {u["unit_id"]: {
            "schedule_gate": True, "scope_identity": True,
            "reading_chain": anchors.get("units", {}).get(u["unit_id"], {})
            .get("reading_chain_review_completed") is True}
            for u in units["units"]}
        recomputed = adapter.adjudicate(units, rebuilt, gate_values)
        check("adjudication_recomputed", recomputed == adjudication)

    rows = list(csv.DictReader((ROOT / "results/same_label_native_changes.csv")
                               .open(encoding="utf-8")))
    counted = 0
    for unit in adjudication["units"]:
        position = next(u["position"] for u in units["units"]
                        if u["unit_id"] == unit["unit_id"])
        for block in unit["blocks"]:
            a1 = rebuilt[f"s2-b{block['block']}-p{position:02d}-"
                         f"{unit['unit_id'].split('/')[1]}-A1"]["detail"]
            b = rebuilt[f"s2-b{block['block']}-p{position:02d}-"
                        f"{unit['unit_id'].split('/')[1]}-B"]["detail"]
            for node in a1["vector"]:
                if a1["vector"][node] == b["vector"].get(node) and (
                        a1["per_test"][node]["failure_signature"]
                        != b["per_test"][node]["failure_signature"]
                        or a1["summaries"][node]["phase_outcomes"]
                        != b["summaries"][node]["phase_outcomes"]):
                    counted += 1
    check("same_label_rows", counted == len(rows), {"recomputed": counted,
                                                    "csv": len(rows)})

    failed = [c for c in checks if not c["ok"]]
    result = {"ok": not failed, "checks": len(checks), "failed": len(failed),
              "failures": failed, "raw_log_files": log_files,
              "candidate_or_tool_calls": 0, "installs": 0, "network_calls": 0}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
