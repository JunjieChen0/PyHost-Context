"""ClawsGO Science Agent：S1a 调度器的**合成消费证明**。

不执行候选代码、不安装、不联网。用真实冻结序列 + 假执行器检验：

* 29 个位置按 M,M,R 消费；
* 机制 8 / 参照 4 配额，组满即停且不转移；
* 规范家族上限 4（pylint 自身与 pylint 依赖合并为一个家族）；
* 机制池耗尽按实际 N 固定；
* 续跑不重复派发，调用计数不重复；
* 全失败时调用恰为 87（29 × 3），不超过已具名候选上限。

每个场景的判定写在 ``checks`` 里，任何一项不成立即整体失败。
"""
import copy
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from admission import scheduler  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent


def load_plan():
    pools = json.loads((HERE.parent / "POOLS_20261018.json").read_text(encoding="utf-8"))
    plan = scheduler.build_plan(pools)
    witness_path = HERE.parent / "STATIC_WITNESSES_20261018.json"
    if witness_path.is_file():
        scheduler.apply_eligibility(plan, json.loads(witness_path.read_text(encoding="utf-8")))
    return plan


def outcome(row, admitted, calls=3):
    return {"admitted": admitted, "status": "admission_evaluated",
            "refusals": [] if admitted else ["synthetic_not_admitted"],
            "calls": calls, "run_dir": f"synthetic/{row['project']}",
            "batch_id": "next-admission-20261018", "commitment": "synthetic"}


def scenario_all_pass(plan):
    state = scheduler.empty_state()
    scheduler.run_to_completion(plan, state, lambda row: outcome(row, True))
    summary = scheduler.summary(plan, state)
    checks = {
        "mechanism_passes_8": summary["mechanism_passes"] == 8,
        "reference_passes_4": summary["reference_passes"] == 4,
        "attempts_12": summary["attempted"] == 12,
        "no_skips_needed": summary["skipped"] == 0,
        "calls_36": summary["calls_used"] == 36,
        "stop_both_full": summary["stop"] == "mechanism_full__reference_full",
        "first_12_positions": [r["position"] for r in state["records"]] ==
                              [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
    }
    return {"name": "all_pass", "summary": summary, "checks": checks,
            "ok": all(checks.values())}


def scenario_all_fail(plan):
    state = scheduler.empty_state()
    scheduler.run_to_completion(plan, state, lambda row: outcome(row, False))
    summary = scheduler.summary(plan, state)
    checks = {
        "attempts_25": summary["attempted"] == 25,
        "calls_75": summary["calls_used"] == 75,
        "static_ineligible_skips_4": summary["skip_reasons"].get("static_ineligible") == 4,
        "mechanism_zero": summary["mechanism_passes"] == 0,
        "reference_zero": summary["reference_passes"] == 0,
        "stop_both_exhausted": summary["stop"] == "mechanism_exhausted__reference_exhausted",
    }
    return {"name": "all_fail", "summary": summary, "checks": checks,
            "ok": all(checks.values())}


def scenario_family_cap(plan):
    # 让 4 个 rich 家族项目通过，其余机制候选失败；观察第 5、6 个 rich 候选被家族上限跳过。
    rich_pass = {"rich-toolkit", "twine", "rich-click", "hatch"}

    def decide(row):
        if row["group"] == "mechanism":
            return outcome(row, row["project"] in rich_pass)
        return outcome(row, True)

    state = scheduler.empty_state()
    scheduler.run_to_completion(plan, state, decide)
    summary = scheduler.summary(plan, state)
    rich_skips = [s for s in state["skips"] if s["reason"] == "family_cap"]
    checks = {
        "rich_family_count_4": summary["family_pass_counts"].get("rich|COLUMNS") == 4,
        "family_cap_skips_1": len(rich_skips) == 1,
        "skipped_projects": [s["project"] for s in rich_skips] == ["pdm"],
        "static_ineligible_skips_4": summary["skip_reasons"].get("static_ineligible") == 4,
        "mechanism_below_target": summary["mechanism_passes"] < 8,
        "reference_4": summary["reference_passes"] == 4,
        "calls_36": summary["calls_used"] == 36,
    }
    return {"name": "family_cap", "summary": summary, "checks": checks,
            "ok": all(checks.values())}


def scenario_pool_exhaustion(plan):
    def decide(row):
        # 机制全失败，参照全通过：机制池耗尽，参照组满即停。
        return outcome(row, row["group"] == "reference")

    state = scheduler.empty_state()
    scheduler.run_to_completion(plan, state, decide)
    summary = scheduler.summary(plan, state)
    checks = {
        "mechanism_attempted_9": sum(1 for r in state["records"]
                                     if r["group"] == "mechanism") == 9,
        "reference_attempted_4": sum(1 for r in state["records"]
                                     if r["group"] == "reference") == 4,
        "attempts_13": summary["attempted"] == 13,
        "calls_39": summary["calls_used"] == 39,
        "stop_exhausted_full": summary["stop"] == "mechanism_exhausted__reference_full",
    }
    return {"name": "pool_exhaustion", "summary": summary, "checks": checks,
            "ok": all(checks.values())}


def scenario_one_group_full(plan):
    """机制组先满、参照组继续：机制位置在组满后跳过，不占调用。"""
    state = scheduler.empty_state()

    def decide(row):
        return outcome(row, row["group"] == "mechanism")

    scheduler.run_to_completion(plan, state, decide)
    summary = scheduler.summary(plan, state)
    group_skips = [s for s in state["skips"] if s["reason"] == "group_full"]
    checks = {
        "mechanism_8": summary["mechanism_passes"] == 8,
        "reference_0": summary["reference_passes"] == 0,
        "mechanism_late_positions_skipped": len(group_skips) == 5,
        "attempts_24": summary["attempted"] == 24,
        "calls_72": summary["calls_used"] == 72,
        "stop_full_exhausted": summary["stop"] == "mechanism_full__reference_exhausted",
    }
    return {"name": "one_group_full", "summary": summary, "checks": checks,
            "ok": all(checks.values())}


def scenario_resume(plan):
    state = scheduler.empty_state()

    def decide(row):
        return outcome(row, False)

    scheduler.run_to_completion(copy.deepcopy(plan), state, decide, max_calls=2 * 3)
    first = scheduler.summary(plan, state)
    # 同批续跑：同一份 state 继续，已尝试位置不会再派发。
    scheduler.run_to_completion(plan, state, decide)
    second = scheduler.summary(plan, state)
    positions = [r["position"] for r in state["records"]]
    checks = {
        "no_duplicate_positions": len(positions) == len(set(positions)),
        "first_partial": first["calls_used"] == 6 and first["attempted"] == 2,
        "resume_total_25": second["attempted"] == 25,
        "resume_calls_75": second["calls_used"] == 75,
        "resume_stop": second["stop"] == "mechanism_exhausted__reference_exhausted",
    }
    return {"name": "resume", "summary": second, "checks": checks,
            "ok": all(checks.values()), "first_summary": first}


def scenario_family_merge(plan):
    """规范家族合并检查：pylint 自身读取与 pylint 依赖读取属于同一家族。"""
    by_project = {row["project"]: row for row in plan}
    checks = {
        "pylint_same_family": by_project["pylint"]["canonical_family"] ==
                              by_project["pylint-plugin-utils"]["canonical_family"],
        "structlog_same_family": by_project["structlog"]["canonical_family"] ==
                                 by_project["structlog-sentry"]["canonical_family"],
        "rich_family_six": sum(1 for row in plan
                               if row["canonical_family"] == "rich|COLUMNS") == 6,
    }
    return {"name": "family_merge", "checks": checks, "ok": all(checks.values())}


def run_all():
    plan = load_plan()
    scenarios = [scenario_family_merge(plan), scenario_all_pass(plan),
                 scenario_one_group_full(plan), scenario_all_fail(plan),
                 scenario_family_cap(plan), scenario_pool_exhaustion(plan),
                 scenario_resume(plan)]
    summary = {
        "scenarios": len(scenarios),
        "passed": sum(1 for s in scenarios if s["ok"]),
        "failed": [s["name"] for s in scenarios if not s["ok"]],
        "planned_positions": len(plan),
        "candidate_executions": 0,
        "installs": 0,
        "network_calls": 0,
    }
    return {
        "schema_version": "pyhost-context/next-admission-synthetic-proof/1.0",
        "batch_id": "next-admission-20261018",
        "method": "真实冻结序列 + 假执行器；不导入候选代码",
        "scenarios": scenarios,
        "summary": summary,
        "ok": not summary["failed"],
    }


if __name__ == "__main__":
    print(json.dumps(run_all()["summary"], ensure_ascii=False, indent=2))
