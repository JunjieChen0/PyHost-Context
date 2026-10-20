"""ClawsGO Science Agent：S1 执行输入的只读自检。

核对范围：29 位置与配额、9/4 静态资格、13 项静态链见证、三案例补丁与派生工件、
预算与绑定、合成消费证明。不下载、不执行候选、不安装。

用法：``python3 extension/next_phase/verify_s1_inputs.py``
"""
import hashlib
import json
import pathlib
import re
import sys
import tarfile

NEXT = pathlib.Path(__file__).resolve().parent
EXT = NEXT.parent
ROOT = EXT.parent
sys.path.insert(0, str(NEXT))
sys.path.insert(0, str(NEXT / "admission"))

from admission import scheduler  # noqa: E402


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def members(sdist):
    out = {}
    with tarfile.open(sdist) as tar:
        for member in tar.getmembers():
            if member.isfile():
                out[member.name] = tar.extractfile(member).read()
    return out


def main():
    problems = []
    plan_doc = load(NEXT / "state" / "plans" / "next_admission_plan_draft.json")
    plan_rows = plan_doc["rows"]
    if len(plan_rows) != 29:
        problems.append("plan_positions")
    if [r["position"] for r in plan_rows] != list(range(1, 30)):
        problems.append("plan_position_order")
    for index, row in enumerate(plan_rows[:19]):
        expected = "reference" if index % 3 == 2 else "mechanism"
        if row["group"] != expected:
            problems.append(f"plan_interleave:{index + 1}")
    if any(r["group"] != "reference" for r in plan_rows[19:]):
        problems.append("plan_tail_reference")
    if plan_doc.get("a6_timeout_seconds") != 240:
        problems.append("a6_timeout_not_240")
    inputs = plan_doc.get("static_inputs") or {}
    for required in ("tooling/admission_runner/integration/jsonl_producer.py",
                     "matrix/tooling/matrix_runner/netwatch_sitecustomize.py",
                     "extension/effective/EFFECTIVE_PROTOCOL_v1.1_Reviewer001.json"):
        if required not in inputs:
            problems.append(f"static_input_missing:{required}")
    request = NEXT / "state" / "plans" / "next_admission_clearance_request.json"
    if request.is_file():
        doc = load(request)
        if doc.get("bound_files") != inputs:
            problems.append("clearance_request_bindings")
        if doc.get("static_inputs_commitment") != plan_doc.get("static_inputs_commitment"):
            problems.append("clearance_request_commitment")

    witness = load(NEXT / "STATIC_WITNESSES_20261018.json")
    items = {item["project"]: item for item in witness["items"]}
    if len(items) != 13:
        problems.append("witness_items")
    eligible = [name for name, item in items.items()
                if item["qualification"]["static_eligible"]]
    if len(eligible) != 9:
        problems.append("eligible_count")
    if witness["artifact_index_count"] < 358:
        problems.append("artifact_index")
    by_project = {r["project"]: r for r in plan_rows}
    for name, item in items.items():
        row = by_project.get(name)
        if row is None:
            problems.append(f"plan_missing:{name}")
        elif bool(row.get("eligible")) != bool(item["qualification"]["static_eligible"]):
            problems.append(f"eligibility_mismatch:{name}")
        if item["qualification"]["static_eligible"] and not item["test_path"]:
            problems.append(f"test_path_missing:{name}")
        for read in item["reads"]:
            if read["read_or_write"] != "read":
                problems.append(f"write_chain:{name}")
            if not read["source_line"]:
                problems.append(f"source_line_missing:{name}")
        if item.get("dependency"):
            dep = item["dependency"]
            path = NEXT / "cache" / "sdists" / dep["filename"]
            if not path.is_file() or sha(path) != dep["sha256"]:
                problems.append(f"dependency_artifact:{name}")

    table = load(NEXT / "CASE_TABLES_20261018_REV1.json")
    if len(table["cases"]) != 3:
        problems.append("case_count")
    sdists = {
        "rich-argparse": ROOT / "admission_artifacts" / "rich-argparse" / "rich_argparse-1.8.0.tar.gz",
        "rich-rst": ROOT / "admission_artifacts" / "rich-rst" / "rich_rst-2.2.0.tar.gz",
    }
    prefixes = {"rich-argparse": "rich_argparse-1.8.0/", "rich-rst": "rich_rst-2.2.0/"}
    for case in table["cases"]:
        project = case["project"]
        archive = members(sdists[project])
        for mutation in case["mutations"]:
            for hunk in mutation["patch"]["hunks"]:
                key = prefixes[project] + hunk["file"]
                text = archive[key].decode("utf-8").splitlines()
                if text[hunk["line"] - 1].rstrip("\n") != hunk["before"]:
                    problems.append(f"patch_anchor:{mutation['id']}:{hunk['line']}")
            for node in mutation["target_nodes"]:
                rel, function = node.split("::", 1)
                function = function.split("[", 1)[0]
                key = prefixes[project] + rel
                body = archive[key].decode("utf-8")
                if not re.search(r"^def %s\(" % re.escape(function), body, re.M):
                    problems.append(f"target_missing:{mutation['id']}:{node}")
    c_case = next(c for c in table["cases"] if c["case_id"] == "C")
    for mutation in c_case["mutations"]:
        for node in mutation["target_nodes"]:
            if not node.startswith("tests/test_main.py::"):
                problems.append(f"C_target_path:{mutation['id']}")
    a_m3 = next(m for c in table["cases"] if c["case_id"] == "A"
                for m in c["mutations"] if m["id"] == "A-M3")
    if any('os.environ.get("NO_COLOR", "x") == ""' in h["after"]
           for h in a_m3["patch"]["hunks"]):
        problems.append("A_M3_old_broad_semantics")
    if not any('("NO_COLOR" in os.environ)' in h["after"]
               for h in a_m3["patch"]["hunks"]):
        problems.append("A_M3_not_single_deviation")
    denominator = table["evaluation_rule"]["C_denominator"]
    if "目标断言在该格命中" in denominator or "target_executed" not in denominator:
        problems.append("C_denominator_result_filtered")
    bm2 = next(m for c in table["cases"] if c["case_id"] == "B"
               for m in c["mutations"] if m["id"] == "B-M2")
    if bm2["suite_detectable"] or any(not value.startswith("not_activated")
                                      for value in bm2["applicable_contexts"].values()
                                      if value != "background_failing"):
        problems.append("B_M2_activation_claim")
    for case in table["cases"]:
        if case["cell_matrix"]["products"][1:] != [m["id"] for m in case["mutations"]]:
            problems.append(f"cell_products:{case['case_id']}")
    witness = (NEXT / "witnesses" / "repair_witness.py").read_text()
    if "_get_formatter(), \"_width\"" not in witness:
        problems.append("witness_stdlib_width")
    if "TemporaryDirectory" not in witness or 'write_text("Some RST."' not in witness:
        problems.append("witness_input_file")
    for name in ("test_next_admission.py", "test_reviewer001_s1_counterexamples.py"):
        if not (NEXT / name).is_file():
            problems.append(f"regression_missing:{name}")

    index = load(NEXT / "DERIVED_INDEX_20261018.json")
    if index["source_table_sha256"] != sha(NEXT / "CASE_TABLES_20261018_REV1.json"):
        problems.append("derived_table_binding")
    tree_rows = []
    for entry in index["derived"]:
        root = ROOT / entry["tree"]
        rows = []
        for path in sorted(root.rglob("*")):
            if path.is_file():
                rows.append(f"{path.relative_to(root).as_posix()}:{sha(path)}")
        digest = hashlib.sha256("\n".join(rows).encode()).hexdigest()
        tree_rows.append(digest == entry["tree_sha256"])
    if not all(tree_rows):
        problems.append("derived_tree_hash")

    budget = load(NEXT / "BUDGET_SCHEDULE_BINDINGS_20261018_REV1.json")
    if budget["budget"]["total_maximum"] != 1740:
        problems.append("budget_total")
    if budget["named_candidate_execution"]["admission_call_ceiling_named"] != 87:
        problems.append("named_call_cap")
    if budget["budget"]["named_cost_upper_bound"]["maximum_at_12_projects_6_cases"] != 1683:
        problems.append("named_cost")
    for path, digest in budget["bindings"]["files"].items():
        if not (ROOT / path).is_file() or sha(ROOT / path) != digest:
            problems.append(f"binding:{path}")

    proof = load(NEXT / "state" / "proofs" / "SYNTHETIC_CONSUMPTION_PROOF.json")
    if not proof["ok"] or proof["summary"]["passed"] != 7:
        problems.append("synthetic_proof")

    result = {
        "plan_positions": len(plan_rows),
        "mechanism_eligible": len(eligible),
        "mechanism_ineligible": len(items) - len(eligible),
        "cases": len(table["cases"]),
        "mutations": sum(len(c["mutations"]) for c in table["cases"]),
        "derived_trees": index["count"],
        "budget_total": budget["budget"]["total_maximum"],
        "named_calls": budget["named_candidate_execution"]["admission_call_ceiling_named"],
        "problems": problems,
        "ok": not problems,
        "candidate_tests_or_installs": 0,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
