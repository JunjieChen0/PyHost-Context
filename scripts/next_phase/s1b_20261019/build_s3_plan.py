"""ClawsGO Science Agent：生成 S3 计划（静态准备，不执行）。

落实 Reviewer 001 的 7 点更正：产品轴 / 套件轴分离、唯一行键、逐格字段、
RETained 参照、共同适用格比较、CONTEXT_RESTRICTED、预算与承诺绑定。
"""
from __future__ import annotations

import hashlib
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
WS = HERE.parents[3]
PROTOCOL = WS / "pyhost-context/matrix/protocol/matrix_protocol_v0.4_reviewed.json"
CASE_TABLES = WS / "pyhost-context/extension/next_phase/CASE_TABLES_20261018_REV1.json"
ARTIFACTS = WS / "pyhost-context/extension/repair_artifacts"
OUT = HERE / "S3_PLAN_20261019.json"
SEED = "pyhost-context-S3-v1-20261019"

CASES = {
    "A": {"project": "rich-argparse", "condition": "K05_NO_COLOR",
          "read_layer": "dependency", "read_object": "rich.console.Console（NO_COLOR 判定）",
          "read_timing": "Console 构造期",
          "repair_application": "必须在 Console 构造之前建立；会话级固定环境会把有效上下文改为 Z"},
    "B": {"project": "rich-argparse", "condition": "K07_COLUMNS",
          "read_layer": "stdlib", "read_object": "stdlib optparse 的 HelpFormatter 宽度来源",
          "read_timing": "收集期（Reviewer 001 核定）",
          "repair_application": "必须在 optparse 读取之前；收集期之后再设无效"},
    "C": {"project": "rich-rst", "condition": "K07_COLUMNS",
          "read_layer": "dependency", "read_object": "rich.console.Console（COLUMNS 宽度）",
          "read_timing": "Console 构造期",
          "repair_application": "必须在 Console 构造之前，或显式控制 Console.width"},
}
CONTEXTS = {
    "A": ["A-C1", "A-C2", "A-C3", "A-C4"],
    "B": ["B-C1", "B-C2", "B-C3", "B-C4"],
    "C": ["C-C1", "C-C2", "C-C3", "C-C4"],
}
PRODUCTS = ["original", "M1", "M2", "M3"]
SUITES = ["S0_original", "R1_global_fix", "R2_local_isolation",
          "R3_explicit_parameterization"]
CELL_FIELDS = [
    "case", "context", "product_variant", "suite_variant", "repeat",
    "effective_context", "s0_passes_in_context", "mutation_activated",
    "target_behaviour_executed", "target_assertion_executed",
    "detected_in_target_phase", "failure_signature_matches",
    "background_failure", "non_target_failures", "unknown_reason",
    "returncode", "collected", "target_node_outcomes",
]


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def artifacts():
    found = {}
    for path in sorted(ARTIFACTS.rglob("*.tar.gz")):
        found[str(path.relative_to(WS))] = {"sha256": sha(path),
                                            "bytes": path.stat().st_size}
    return found


def main():
    tables = json.loads(CASE_TABLES.read_text(encoding="utf-8"))
    by_case = {case["case_id"]: case for case in tables["cases"]}
    rows, witnesses = [], []
    for case_id, meta in CASES.items():
        table = by_case[case_id]
        mutations = table["mutations"]
        for context in CONTEXTS[case_id]:
            for product in PRODUCTS:
                for suite in SUITES:
                    label = f"{case_id}|{context}|{product}|{suite}|r1"
                    rows.append({
                        "row_key": label,
                        "case": case_id, "context": context,
                        "product_variant": product, "suite_variant": suite,
                        "repeat": 1,
                        "label_sha256": hashlib.sha256(
                            f"{SEED}|{label}".encode()).hexdigest(),
                        "call_kind": "suite",
                    })
                label = f"{case_id}|{context}|{product}|witness|r1"
                witnesses.append({
                    "row_key": label, "case": case_id, "context": context,
                    "product_variant": product, "call_kind": "witness",
                    "repeat": 1,
                    "label_sha256": hashlib.sha256(
                        f"{SEED}|{label}".encode()).hexdigest(),
                })
    for row in rows + witnesses:
        pass
    rows.sort(key=lambda r: r["label_sha256"])
    witnesses.sort(key=lambda r: r["label_sha256"])
    for index, row in enumerate(rows, 1):
        row["order"] = index
    for index, row in enumerate(witnesses, 1):
        row["order"] = index
    plan = {
        "schema_version": "pyhost-context/S3-plan/1.0",
        "batch_id": "next-S3-20261019",
        "status": "prepared_not_authorized",
        "execution_authorized": False,
        "axes": {
            "product": PRODUCTS,
            "suite": SUITES,
            "contexts_per_case": 4,
            "repeat_per_cell": 1,
            "row_key": "case/context/product_variant/suite_variant/repeat",
            "product_axis_note": "原产品 + 三枚已声明变异；修复不再乘一个产品轴",
            "suite_axis_note": "原套件 + R1/R2/R3；套件与独立见证调用分开记账",
        },
        "cases": [{"case_id": key, **value,
                   "declared_mutations": [m["id"] for m in by_case[key]["mutations"]],
                   "contexts": CONTEXTS[key]} for key, value in CASES.items()],
        "row_fields": CELL_FIELDS,
        "adjudication": {
            "denominator": ["s0_passes_in_context", "mutation_activated",
                            "target_behaviour_executed", "target_assertion_executed"],
            "RETAINED": "同一上下文存在 S0 检出参照，且该修复套件在共同适用格检出",
            "PRE_EXISTING_BLIND_SPOT": "S0 漏检而独立见证检出；即使某修复后来检出也不改判，不要求其余修复继续漏检",
            "DEGRADED": "S0 检出、该修复在同格漏检，且 mutation_activated 与 target_behaviour_executed 为真、effective_context 未改变",
            "CONTEXT_RESTRICTED": "修复把环境固定到 Z，目标行为不再执行；记录 effective_context，不得认证原上下文义务",
            "equal_pass": "只在共同适用格比较杀死集合；context-restricted 格单列",
            "separation": "mutation_activated 与 target_assertion_executed 分开登记，不合并",
            "unknown_and_not_applicable": "一律保留，不当作检出或通过",
        },
        "budget": {
            "suite_runs_planned": len(rows),
            "witness_runs_planned": len(witnesses),
            "total_calls_planned": len(rows) + len(witnesses),
            "caps": {"repair_suites_maximum": 384, "repair_witnesses_maximum": 96},
            "unused": {"repair_suites": 384 - len(rows),
                       "repair_witnesses": 96 - len(witnesses)},
            "no_new_rounds": True,
            "note": "按逐行计划求和；未用配额不转移，不扩到未登记格。",
        },
        "inputs": {
            "case_tables": {"path": str(CASE_TABLES.relative_to(WS)),
                            "sha256": sha(CASE_TABLES)},
            "protocol": {"path": str(PROTOCOL.relative_to(WS)),
                         "sha256": sha(PROTOCOL)},
            "artifacts": artifacts(),
            "missing_before_execution": [
                "A 的 M2、M3 变异工件（当前只有一枚 mutp）",
                "B 的 M2、M3 变异工件（当前只有一枚 mutp）",
                "三案例的见证代码、目标断言位置与预期失败签名文件",
                "每行实际套件补丁与变异补丁的 SHA-256 绑定表",
            ],
            "present": {
                "A": "rp-k05-r{1,2,3}-{orig,mutp} 六件目录（1 枚变异）",
                "B": "rp-k07-r{1,2,3}-{orig,mutp} 六件目录（1 枚变异）",
                "C": "validation_richrst_k07 下 3 枚变异与 3 个修复套件",
            },
        },
        "commitment": {
            "required_bindings": [
                "每案例每套件的实际补丁 tarball SHA-256",
                "每枚变异的补丁 SHA-256 与行为义务描述",
                "见证代码、目标断言位置、预期失败签名",
                "本计划的逐行清单（行键、顺序、标签哈希）",
                "调度种子与重复数",
                "未用配额规则",
            ],
            "seed": SEED,
            "rows_sha256": hashlib.sha256(json.dumps(
                rows + witnesses, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        },
        "not_authorized": ["S3 执行", "S2", "repro", "新键值", "新候选"],
        "calls_check": {
            "suite": len(rows), "witness": len(witnesses),
            "expected_suite": 3 * 4 * 4 * 4, "expected_witness": 3 * 4 * 4,
        },
    }
    if len(rows) != 192 or len(witnesses) != 48:
        raise SystemExit("row_count_mismatch")
    if len({r["row_key"] for r in rows}) != len(rows):
        raise SystemExit("duplicate_row_key")
    OUT.write_text(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(json.dumps({"out": str(OUT), "sha256": sha(OUT),
                      "suite_rows": len(rows), "witness_rows": len(witnesses),
                      "total": len(rows) + len(witnesses),
                      "artifacts": len(plan["inputs"]["artifacts"])},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
