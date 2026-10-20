"""ClawsGO Science Agent：从原始 JSONL 重建准入记账（逐测试 / 收集节点分开）。

独立审计 F06 指出：**收集节点跳过**不是"一个已收集测试"，必须与逐测试分母分开统计；
且跳过原因要按 pytest 原生的 ``(path, line, reason)`` 元组解析，缺少 reason 时如实记为
"未解析"，不得归入某个猜测的原因。

本模块只读原始 JSONL，不执行任何项目代码。用法：

    python3 reconstruct_accounting.py <run_root> [<run_root> ...]

``run_root`` 形如 ``admission_runs``；对每个候选的每轮 JSONL 重建记录，输出 JSON。
"""
import ast
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pytest_jsonl import build_per_test, collection_info, parse_jsonl  # noqa: E402

TUPLE_RE = re.compile(r"^\(.*\)$", re.S)


def parse_skip_reason(longrepr):
    """把原生 longrepr 解析成 (path, line, reason)；解析不出就返回 None，不猜。"""
    if not longrepr:
        return None
    text = longrepr.strip()
    if not TUPLE_RE.match(text):
        return None
    try:
        value = ast.literal_eval(text)
    except Exception:
        return None
    if isinstance(value, tuple) and len(value) == 3:
        return {"path": str(value[0]), "line": value[1], "reason": str(value[2])}
    return None


def reconstruct(jsonl_path):
    """逐测试结局取 ``build_per_test``（与准入门同一实现，避免两套口径）。

    注意：不能直接数 ``TestReport`` 条数——每个已执行测试会产生 setup/call/teardown
    三条 ``passed`` 报告，按记录计数会把分母放大近三倍。``build_per_test`` 已按 nodeid
    归并并给出 native ``raw_outcome`` 与 ``derived_labels``。
    """
    text = Path(jsonl_path).read_text(encoding="utf-8")
    records, malformed = parse_jsonl(text)
    collection = collection_info(records)
    per_test = build_per_test(records, collection["collected"])

    outcomes = collections.Counter()
    labels = collections.Counter()
    for record in per_test.values():
        outcomes[record["raw_outcome"]] += 1
        for label in record["derived_labels"]:
            labels[label] += 1

    skip_reasons = collections.Counter()
    skip_reason_unparsed = []
    for nodeid, record in sorted(per_test.items()):
        if record["raw_outcome"] != "skipped":
            continue
        longrepr = None
        for phase in ("call", "setup", "teardown"):
            detail = record["phase_details"].get(phase) or {}
            if detail.get("outcome") == "skipped" and detail.get("longrepr"):
                longrepr = detail["longrepr"]
                break
        parsed = parse_skip_reason(longrepr)
        if parsed:
            skip_reasons[parsed["reason"]] += 1
        elif labels.get("xfailed") or record.get("derived_labels"):
            skip_reasons["<xfail 标记用例：longrepr 为回溯，非 (path,line,reason) 元组>"] += 1
        else:
            skip_reasons["<未解析为 (path,line,reason) 元组>"] += 1
            skip_reason_unparsed.append({"nodeid": nodeid, "longrepr": longrepr})

    collection_skips = []
    for record in records:
        if record.get("$report_type") == "CollectReport" and record.get("outcome") == "skipped":
            parsed = parse_skip_reason(record.get("longrepr"))
            collection_skips.append({
                "nodeid": record["nodeid"],
                "reason": parsed["reason"] if parsed else None,
                "parsed": parsed is not None,
                "longrepr": record.get("longrepr"),
            })

    snapshot_env = next((r.get("environment") for r in records
                         if r.get("$report_type") == "RuntimeSnapshot"), None)
    session = next((r for r in records if r.get("$report_type") == "SessionFinished"), None)
    return {
        "jsonl": str(jsonl_path),
        "malformed_lines": len(malformed),
        "collected": collection["collected_count"],
        "per_test_outcomes": dict(outcomes),
        "derived_labels": dict(labels),
        "skip_reason_groups": dict(skip_reasons),
        "unparsed_skip_reasons": skip_reason_unparsed,
        "collection_node_skips": collection_skips,
        "collection_errors": collection["errors"],
        "session_finished": session,
        "has_snapshot_environment": snapshot_env is not None,
    }


def main(argv):
    out = {}
    for root in argv:
        root = Path(root)
        out[root.name] = {}
        for round_file in sorted(root.glob("logs/round*.jsonl"),
                                 key=lambda p: int(re.search(r"round(\d+)", p.name).group(1))):
            out[root.name][round_file.stem] = reconstruct(round_file)
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
