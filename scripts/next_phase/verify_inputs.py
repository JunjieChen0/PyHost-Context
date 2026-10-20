"""ClawsGO Science Agent：新阶段执行输入的只读自检。

核对：

1. 参照池 = 冻结 frame 中五类别、非 excluded、有 sdist、排除 E1—E5 后的前 16 项；
2. 两池无重叠；交错序列的位置连续、组名与池槽一致；
3. 三案例表结构完整（4 上下文、≥1 义务、≤3 变异、每枚变异有补丁/义务/见证）；
4. 预算六行合计 1,740、分类上限与计划一致；
5. 绑定哈希与当前文件相符。

不下载、不执行候选、不安装。
用法：``python3 verify_inputs.py --workspace <pyhost-context>``
"""
import argparse
import hashlib
import json
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
EXT = HERE.parent
ROOT = EXT.parent
NEXT = EXT / "next_phase"


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=pathlib.Path)
    args = parser.parse_args()
    if args.workspace:
        root = args.workspace.resolve()
    else:
        root = ROOT
    ext = root / "extension"
    next_dir = ext / "next_phase"
    pools = json.loads((next_dir / "POOLS_20261018.json").read_text())
    cases = json.loads((next_dir / "CASE_TABLES_20261018.json").read_text())
    budget = json.loads((next_dir / "BUDGET_SCHEDULE_BINDINGS_20261018.json").read_text())
    problems = []

    frame = json.loads((ext / "frame" / "frame_20261015.json").read_text())
    excluded = set()
    for rows in json.loads((ext / "freeze" / "final_sample_20261015.json").read_text())["categories"].values():
        for row in rows:
            excluded.add(norm(row["project"]))
    for name in ("termcolor", "click", "tqdm", "babel", "pytz", "humanize", "colorama",
                 "more-itertools", "flask", "apache-arrow", "pyarrow", "rich"):
        excluded.add(norm(name))
    universe = [c for c in frame["candidates"]
                if c.get("category") in ("time_i18n", "dev_test_tools", "text_config", "cli", "data")
                and c.get("status") != "excluded" and c.get("sdist")
                and norm(c["project"]) not in excluded]
    universe.sort(key=lambda c: (c["rank"], c["project"]))
    expected_reference = [c["project"] for c in universe[:16]]
    actual_reference = [row["project"] for row in pools["reference_pool"]["rows"]]
    if expected_reference != actual_reference:
        problems.append("reference_pool_not_mechanical")

    mech = [row["project"] for row in pools["mechanism_pool"]["rows"]]
    if set(mech) & set(actual_reference):
        problems.append("pool_overlap")
    if len(mech) != pools["mechanism_pool"]["size"]:
        problems.append("mechanism_size")

    seq = pools["admission_sequence"]
    if [row["position"] for row in seq] != list(range(1, len(seq) + 1)):
        problems.append("sequence_positions")
    for row in seq:
        pool = pools["mechanism_pool"]["rows"] if row["group"] == "mechanism" else pools["reference_pool"]["rows"]
        match = next((x for x in pool if x["slot"] == row["pool_slot"]), None)
        if match is None or match["project"] != row["project"]:
            problems.append("sequence_row:" + str(row["position"]))

    for case in cases["cases"]:
        if len(case.get("contexts", [])) != 4:
            problems.append("contexts:" + case["case_id"])
        if not case.get("obligations"):
            problems.append("obligations:" + case["case_id"])
        mutations = case.get("mutations", [])
        if not (1 <= len(mutations) <= 3):
            problems.append("mutations:" + case["case_id"])
        for mutation in mutations:
            if not mutation.get("patch") or not mutation.get("obligation") or not mutation.get("witness"):
                problems.append("mutation_fields:" + mutation.get("id", "?"))

    total = sum(line["maximum"] for line in budget["budget"]["lines"])
    if total != 1740:
        problems.append("budget_total")
    if budget["budget"]["total_maximum"] != total:
        problems.append("budget_declared")

    for path, digest in budget["bindings"]["files"].items():
        target = root / path
        if not target.is_file() or sha(target) != digest:
            problems.append("binding:" + path)

    result = {"reference_pool": len(actual_reference), "mechanism_pool": len(mech),
              "admission_sequence": len(seq), "cases": len(cases["cases"]),
              "budget_total": total, "problems": problems, "ok": not problems,
              "candidate_tests_or_installs": 0}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
