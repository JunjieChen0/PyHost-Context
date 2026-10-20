"""ClawsGO Science Agent：新阶段准入的**纯调度器**（不执行任何候选代码）。

职责只有四件：

1. 按冻结的 29 个位置（M、M、R 交错）决定下一个要派发的候选；
2. 维护机制组 8 项、参照组 4 项的通过配额，组满即停该组，未用配额不转移；
3. 维护机制组的**规范家族上限 4**——家族按键名与定义发行版合并，不因"候选自身"
   与"依赖"通道不同而把同一读取 API 拆成两个家族；
4. 从账本恢复状态：已尝试的位置不再派发；跳过记录不占调用。

本模块只消费 `POOLS_20261018.json` 与账本记录，不读候选工件、不导入候选代码。
"""
import re

TARGET_PASSES = {"mechanism": 8, "reference": 4}
FAMILY_CAP = 4
CALLS_PER_ATTEMPT = 3  # 至多 2 次 A6 收集 + 1 次 A7 基线
ALLOWED_SKIP_REASONS = {"group_full", "family_cap", "static_ineligible"}


def normalize(name):
    return re.sub(r"[-_.]+", "-", str(name)).lower()


def canonical_family(row):
    """规范家族 = 定义发行版 + 读取键。

    依赖通道的发行版取自池内 family 字段（`dependency|<dist>|<key>`）；项目自身通道
    按项目名归一。通道不进入家族名，避免把 pylint 的自身读取与 pylint-plugin-utils
    经 pylint 的读取当成两个家族。
    """
    parts = str(row.get("family") or "").split("|")
    if len(parts) == 3 and parts[0] == "dependency":
        dist = normalize(parts[1])
    else:
        dist = normalize(row["project"])
    return f"{dist}|{row.get('key') or 'reference'}"


def build_plan(pools):
    """把两池与准入序列展开成可派发计划；每个位置一行。"""
    mechanism = {row["slot"]: row for row in pools["mechanism_pool"]["rows"]}
    reference = {row["slot"]: row for row in pools["reference_pool"]["rows"]}
    plan = []
    for entry in pools["admission_sequence"]:
        pool = mechanism if entry["group"] == "mechanism" else reference
        row = pool[entry["pool_slot"]]
        if row["project"] != entry["project"]:
            raise ValueError(f"序列位置 {entry['position']} 与池槽不一致")
        plan.append({
            "position": entry["position"],
            "group": entry["group"],
            "pool_slot": entry["pool_slot"],
            "project": row["project"],
            "version": row["version"],
            "category": row["category"],
            "rank": row["rank"],
            "key": row.get("key"),
            "channel": row.get("channel") or "none_reference",
            "family": row.get("family"),
            "canonical_family": canonical_family(row),
            "sdist": row["sdist"],
        })
    return plan


def apply_eligibility(plan, witness):
    """把静态见证的合格判定写到计划行；机制组不合格者不派发。"""
    items = {item["project"]: item for item in witness.get("items", [])}
    for row in plan:
        if row["group"] != "mechanism":
            row["eligible"], row["ineligible_reason"] = True, None
            continue
        item = items.get(row["project"])
        if item is None:
            row["eligible"], row["ineligible_reason"] = False, "missing_static_witness"
        else:
            qualification = item["qualification"]
            row["eligible"] = bool(qualification["static_eligible"])
            row["ineligible_reason"] = qualification.get("ineligible_reason")
    return plan


def empty_state():
    return {"records": [], "skips": []}


def _attempted_positions(state):
    return {row["position"] for row in state["records"]}


def passed(state, group):
    return [row for row in state["records"] if row["group"] == group and row.get("admitted")]


def calls_used(state):
    return sum(int(row.get("calls") or 0) for row in state["records"])


def _settled_positions(state):
    return _attempted_positions(state) | {row["position"] for row in state["skips"]}


def group_closed(plan, state, group):
    """该组是否已关闭：组满，或组内每个位置都已尝试/跳过。"""
    if len(passed(state, group)) >= TARGET_PASSES[group]:
        return "full"
    rows = [row for row in plan if row["group"] == group]
    if all(row["position"] in _settled_positions(state) for row in rows):
        return "exhausted"
    return None


def stop_reason(plan, state):
    """两组都关闭时给出停止原因；否则 None。"""
    mech = group_closed(plan, state, "mechanism")
    ref = group_closed(plan, state, "reference")
    if mech and ref:
        return f"mechanism_{mech}__reference_{ref}"
    return None


def next_action(plan, state):
    """返回下一个动作。

    `{"action": "dispatch", "row": ...}`
    `{"action": "skip", "row": ..., "reason": ...}`（家族上限、组满）
    `{"action": "stop", "reason": ...}`
    """
    reason = stop_reason(plan, state)
    if reason:
        return {"action": "stop", "reason": reason}
    attempted = _settled_positions(state)
    for row in plan:
        if row["position"] in attempted:
            continue
        group = row["group"]
        if len(passed(state, group)) >= TARGET_PASSES[group]:
            return {"action": "skip", "row": row, "reason": "group_full"}
        if group == "mechanism":
            if row.get("eligible") is False:
                return {"action": "skip", "row": row,
                        "reason": "static_ineligible"}
            family_passes = sum(1 for rec in passed(state, group)
                                if rec["canonical_family"] == row["canonical_family"])
            if family_passes >= FAMILY_CAP:
                return {"action": "skip", "row": row, "reason": "family_cap"}
        return {"action": "dispatch", "row": row}
    return {"action": "stop", "reason": "no_dispatchable_row"}


def settle(state, row, outcome):
    """登记一次准入尝试。`outcome` 由执行器给出，调度器不推断。"""
    record = {
        "position": row["position"],
        "group": row["group"],
        "pool_slot": row["pool_slot"],
        "project": row["project"],
        "version": row["version"],
        "canonical_family": row["canonical_family"],
        "admitted": bool(outcome.get("admitted")),
        "status": outcome.get("status"),
        "refusals": list(outcome.get("refusals") or []),
        "calls": int(outcome.get("calls") or 0),
        "run_dir": outcome.get("run_dir"),
        "batch_id": outcome.get("batch_id"),
        "commitment": outcome.get("commitment"),
    }
    state["records"].append(record)
    return record


def skip(state, row, reason):
    if reason not in ALLOWED_SKIP_REASONS:
        raise ValueError(f"illegal_skip_reason:{reason}")
    state["skips"].append({
        "position": row["position"], "group": row["group"],
        "pool_slot": row["pool_slot"], "project": row["project"],
        "version": row["version"],
        "canonical_family": row["canonical_family"], "reason": reason, "calls": 0,
    })


def validate_resume(plan, state, batch_id, commitment):
    """续跑前检查：每条记录严格绑定批次、承诺与冻结身份；跳过原因须合法且位置唯一。"""
    problems = []
    by_position = {row["position"]: row for row in plan}
    seen = set()
    for record in state["records"]:
        position = record.get("position")
        if position in seen:
            problems.append(f"duplicate_position:{position}")
        seen.add(position)
        row = by_position.get(position)
        if row is None:
            problems.append(f"position_outside_plan:{position}")
            continue
        if record.get("batch_id") != batch_id:
            problems.append(f"foreign_batch:{position}")
        if record.get("commitment") != commitment:
            problems.append(f"foreign_commitment:{position}")
        for field in ("project", "group", "pool_slot", "version", "canonical_family"):
            if record.get(field) != row[field]:
                problems.append(f"record_mismatch:{position}:{field}")
        if not isinstance(record.get("calls"), int) or record.get("calls") < 0:
            problems.append(f"invalid_calls:{position}")
        if not isinstance(record.get("admitted"), bool):
            problems.append(f"invalid_admitted:{position}")
        if not record.get("status"):
            problems.append(f"missing_status:{position}")
    for entry in state["skips"]:
        position = entry.get("position")
        if position in seen:
            problems.append(f"duplicate_position:{position}")
        seen.add(position)
        row = by_position.get(position)
        if row is None:
            problems.append(f"skip_outside_plan:{position}")
            continue
        if entry.get("reason") not in ALLOWED_SKIP_REASONS:
            problems.append(f"illegal_skip_reason:{position}:{entry.get('reason')}")
        for field in ("project", "group", "pool_slot", "version", "canonical_family"):
            if entry.get(field) != row[field]:
                problems.append(f"skip_mismatch:{position}:{field}")
        if int(entry.get("calls") or 0) != 0:
            problems.append(f"skip_calls_nonzero:{position}")
    return {"ok": not problems, "problems": problems}


def run_to_completion(plan, state, execute, max_calls=None):
    """合成/真实共用的主循环；`execute(row)` 返回 outcome。"""
    while True:
        action = next_action(plan, state)
        if action["action"] == "stop":
            return {"stop": action["reason"], "state": state}
        row = action["row"]
        if action["action"] == "skip":
            skip(state, row, action["reason"])
            continue
        if max_calls is not None and calls_used(state) + CALLS_PER_ATTEMPT > max_calls:
            return {"stop": "call_cap", "state": state}
        outcome = execute(row)
        settle(state, row, outcome)


def summary(plan, state):
    mech = passed(state, "mechanism")
    ref = passed(state, "reference")
    return {
        "planned_positions": len(plan),
        "attempted": len(state["records"]),
        "skipped": len(state["skips"]),
        "mechanism_passes": len(mech),
        "reference_passes": len(ref),
        "targets": dict(TARGET_PASSES),
        "family_cap": FAMILY_CAP,
        "calls_used": calls_used(state),
        "call_ceiling_named_candidates": len(plan) * CALLS_PER_ATTEMPT,
        "stop": stop_reason(plan, state),
        "family_pass_counts": _family_counts(mech),
        "skip_reasons": _skip_counts(state["skips"]),
    }


def _family_counts(records):
    counts = {}
    for record in records:
        counts[record["canonical_family"]] = counts.get(record["canonical_family"], 0) + 1
    return dict(sorted(counts.items()))


def _skip_counts(skips):
    counts = {}
    for entry in skips:
        counts[entry["reason"]] = counts.get(entry["reason"], 0) + 1
    return dict(sorted(counts.items()))
