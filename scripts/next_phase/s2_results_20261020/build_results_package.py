"""ClawsGO Science Agent：生成 S2 结果报告数据并打包（只读，不重跑）。

从已完成的状态目录读取账本、30 份结果/assessment 与七类原始产物，
生成 cells、同标签原生变化、仪器缺陷记录，并打包为单一 ZIP。
"""
from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import zipfile
from collections import Counter

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
WS = NEXT.parents[2]
STATE = NEXT / "state/s2_20261019"
S2DIR = NEXT / "s2_20261019"
OUT = WS / "deliverables/PyHost_S2_Results_20261020.zip"
SUFFIXES = (".jsonl", ".meta", ".netlog", ".stdout.txt", ".stderr.txt",
            "_boot.stdout.txt", "_boot.stderr.txt")


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def main():
    ledger = read(STATE / "ledger.json")
    adjudication = read(STATE / "adjudication.json")
    units = read(S2DIR / "S2_UNITS_20261019.json")
    records = ledger["records"]
    assert len(records) == 30, len(records)

    cells = []
    for unit in adjudication["units"]:
        for block in unit["blocks"]:
            row = {"unit_id": unit["unit_id"], "block": block["block"],
                   "status": block["status"]}
            row.update({k: v for k, v in block["criteria"].items()})
            row["flip_node_count"] = block["details"]["flip_node_count"]
            row["registered_flip_node_count"] = block["details"]["registered_flip_node_count"]
            row["flip_missing"] = len(block["details"]["flip_missing"])
            row["flip_extra"] = len(block["details"]["flip_extra"])
            row["signature_drift_nodes"] = len(block["details"]["signature_drift_nodes"])
            row["stage_drift_nodes"] = len(block["details"]["stage_drift_nodes"])
            cell_status = {b["block"]: b["status"] for b in unit["blocks"]}
            if block["block"] == 2:
                row.update(unit_status=unit["summary"]["unit_status"],
                           registered_level=unit["summary"]["registered_level"],
                           proposed_level=unit["summary"]["proposed_level"])
            cells.append(row)
    fields = ["unit_id", "block", "status", "C1_validity", "C0_baseline_reproduction",
              "C2_identity", "C3_baseline_stability", "C4_flip_set", "C5_signature",
              "C6_stage", "C7_restoration", "flip_node_count",
              "registered_flip_node_count", "flip_missing", "flip_extra",
              "signature_drift_nodes", "stage_drift_nodes", "unit_status",
              "registered_level", "proposed_level"]
    with (HERE / "results/cells.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(cells)

    # 同标签原生变化：summary 不变但签名或阶段变化；单列，不扩注册集合。
    same_rows = []
    for unit in adjudication["units"]:
        uid = unit["unit_id"]
        project = uid.split("/")[0]
        for block in unit["blocks"]:
            phase_runs = {}
            for phase in ("A1", "B"):
                run_id = f"s2-b{block['block']}-p{unit_position(uid, units):02d}-"\
                         f"{uid.split('/')[1]}-{phase}"
                phase_runs[phase] = read(STATE / "results" / run_id / "attempt-001.json")
            a1, b = phase_runs["A1"], phase_runs["B"]
            for node in a1["vector"]:
                if a1["vector"][node] != b["vector"].get(node):
                    continue
                sig_changed = (a1["per_test"][node]["failure_signature"]
                               != b["per_test"][node]["failure_signature"])
                phase_changed = (a1["summaries"][node]["phase_outcomes"]
                                 != b["summaries"][node]["phase_outcomes"])
                if sig_changed or phase_changed:
                    same_rows.append({"unit_id": uid, "block": block["block"],
                                      "nodeid": node, "summary": a1["vector"][node],
                                      "signature_changed": sig_changed,
                                      "phase_changed": phase_changed})
    with (HERE / "results/same_label_native_changes.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["unit_id", "block", "nodeid",
                                                    "summary", "signature_changed",
                                                    "phase_changed"])
        writer.writeheader()
        writer.writerows(same_rows)

    (HERE / "results/instrument_defects.json").write_text(json.dumps(
        instrument_defects(ledger, adjudication), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    (HERE / "results/adjudication.json").write_text(
        json.dumps(adjudication, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8")
    (HERE / "results/ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8")
    (HERE / "units/S2_UNITS_20261019.json").write_text(
        json.dumps(units, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8")

    build_zip(records)


def unit_position(unit_id, units):
    return next(u["position"] for u in units["units"] if u["unit_id"] == unit_id)


def instrument_defects(ledger, adjudication):
    typer_runs = [r for r in ledger["records"] if r["project"] == "typer"]
    skipped = 0
    for row in typer_runs:
        detail = read(STATE / row["result"]["path"])
        if detail["vector"].get(
                "tests/test_completion/test_completion.py::test_show_completion") == "skipped":
            skipped += 1
    parser_affected = 0
    path_normalisation_nodes = None
    for row in typer_runs:
        assessment = read(STATE / row["assessment"]["path"])
        if "raw_result_parse_mismatch" in (assessment.get("S2_execution_problems") or []):
            parser_affected += 1
    k07 = next(u for u in adjudication["units"] if u["unit_id"] == "typer/K07_COLUMNS")
    path_normalisation_nodes = {b["block"]: len(b["details"]["signature_drift_nodes"])
                                for b in k07["blocks"]}
    return {
        "scope": "作者侧归因诊断；不改写任何判定，不重跑，待 Reviewer 001 复核。",
        "defects": [
            {
                "id": "P-S2-1_shell_detection_context",
                "summary": ("typer 的 test_show_completion 在 S1b 基线中为 failed，"
                            "在全部 18 个 S2 typer 轮中为 skipped（Test requires Bash）。"),
                "mechanism": ("该测试的 skip 标记调用 shellingham.detect_shell()，"
                              "依赖进程树中的 shell 祖先。本次 30 轮以后台方式启动，"
                              "父 shell 退出后进程被重新挂接，探测失败 → shell=None → 跳过。"),
                "evidence": {"typer_runs": len(typer_runs), "skipped": skipped,
                             "nodeid": ("tests/test_completion/test_completion.py::"
                                        "test_show_completion")},
                "effect": ("每个 typer A1 的基线向量与 S1b 注册基线相差该 1 个节点"
                           "（failed→skipped），使 C0 基线复现不成立；"
                           "该节点不在任何注册翻转集合内，C4 不受影响。"),
                "author_action": "保留现场，不重跑；交由复核方裁定。",
            },
            {
                "id": "P-S2-2_reference_parser_prefix",
                "summary": ("复核方新守卫用 run_root 与 source_path 作为归一化前缀重解析 JSONL；"
                            "runner 实际归一化的前缀是源码树的父目录。"),
                "mechanism": ("typer 的 failed/skipped 签名包含测试文件绝对路径；"
                              "重解析后仍为绝对路径，而 runner 返回值为 "
                              "`<run_dir>/typer/tests/...`，故 per_test 不相等。"),
                "evidence": {"typer_runs_flagged": parser_affected,
                             "typer_runs_total": len(typer_runs),
                             "twine_or_rich_toolkit_flagged": 0,
                             "per_run_differing_nodes": 195,
                             "differing_field": "failure_signature only",
                             "summaries_and_vector_equal": True},
                "effect": ("全部 18 个 typer 轮被 C1 判为 NOT_EVALUABLE_VALIDITY；"
                           "twine 与 rich-toolkit 不受影响。"),
                "author_action": "保留现场，不修改签署守卫，不重跑。",
            },
            {
                "id": "P-S2-3_cross_batch_absolute_path",
                "summary": ("typer/K07 的 8 个注册失败签名包含 fixture 模块 repr 的绝对路径，"
                            "其中批次目录 s1b_20261019 与 s2_20261019 不同。"),
                "mechanism": ("C5 要求签名文本完全相同；除批次根目录外两段文本一致。"),
                "evidence": {"registered_flip_nodes": 152,
                             "signature_drift_nodes_per_block": path_normalisation_nodes,
                             "difference": "state 根目录路径片段（s1b_20261019 → s2_20261019）"},
                "effect": "C5 在 typer/K07 两块均不成立；其余 typer 单位 C5 成立。",
                "author_action": "保留现场，不修改注册签名，不重跑。",
            },
        ],
        "verification": {
            "registered_flip_sets_reproduced": {
                u["unit_id"]: [b["criteria"]["C4_flip_set"] for b in u["blocks"]]
                for u in adjudication["units"]},
            "same_label_native_changes": "见 results/same_label_native_changes.csv；"
                                         "typer/K07 每块 10 个（8 failed、2 xfailed），"
                                         "不扩展 E2 集合、不计新增发现。",
        },
    }


def build_zip(records):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    members = []

    def add(archive, arcname, data):
        members.append({"path": arcname, "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest()})
        info = zipfile.ZipInfo(arcname, (2026, 10, 20, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, data)

    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(HERE.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                add(archive, path.relative_to(HERE).as_posix(), path.read_bytes())
        for row in records:
            run_id = row["run_id"]
            for key in ("result", "assessment"):
                path = STATE / row[key]["path"]
                add(archive, f"raw/results/{run_id}/{path.name}", path.read_bytes())
            assessment = read(STATE / row["assessment"]["path"])
            for item in assessment.get("evidence_files") or []:
                path = STATE / item["path"]
                add(archive, f"raw/logs/{path.name}", path.read_bytes())
        manifest = {
            "schema_version": "pyhost-context/S2-results-package/1.0",
            "batch_id": "next-S2-20261019",
            "pre_run_commitment_hash": "5f85571e834db8e62907ab8c28949743eb84e0b496c80c482c4978196c1c9a52",
            "schedule_sha256": "9474b2a4920baeaf75a7e8421a64ef9dfbc862ccd9414c2c9da1b8efdf88df39",
            "clearance_sha256": sha(S2DIR / "REVIEWER001_S2_CLEARANCE_20261020.json"),
            "members": members,
        }
        info = zipfile.ZipInfo("S2_RESULTS_MANIFEST.json", (2026, 10, 20, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, json.dumps(manifest, ensure_ascii=False, indent=2).encode())
    raw = OUT.read_bytes()
    print(json.dumps({"path": str(OUT), "members": len(members) + 1,
                      "files": len(members), "bytes": len(raw),
                      "sha256": hashlib.sha256(raw).hexdigest()},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
