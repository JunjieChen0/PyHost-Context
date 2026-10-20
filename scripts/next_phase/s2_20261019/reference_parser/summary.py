"""ClawsGO Science Agent：逐测试**汇总标签**（v0.4 校订修正）。

修正的问题：原规则"三阶段齐备且 call passed → passed"会在 teardown 失败时仍然给出
``passed``。校订后的规则是——``passed`` 要求：所需阶段齐备、call 为原生 ``passed``，
并且**其他阶段都没有 failed 或 skipped**。call passed 不得覆盖 teardown 的 error。

实现方式：不修改准入期已被审计的 ``pytest_jsonl``（那份代码与准入证据绑定），而是在矩阵侧
用它的输出（``phases`` 阶段状态 + ``phase_details`` 原生结果 + native ``wasxfail``）重新派生
一个**汇总标签**，并显式给出叠加字段。派生标签不是互斥分区：``incomplete`` / ``error`` /
``xpassed_strict`` 可以作为 overlay 与主标签并存，但都不扩大测试分母。
"""
PHASE_ORDER = ("setup", "call", "teardown")
SUMMARY_LABELS = ("passed", "failed", "error", "skipped", "xfailed", "xpassed",
                  "xpassed_strict", "incomplete", "not_run")


def _outcomes(record):
    details = record.get("phase_details") or {}
    return {when: (details.get(when) or {}).get("outcome") for when in PHASE_ORDER}


def _states(record):
    return {when: (record.get("phases") or {}).get(when, "report_missing")
            for when in PHASE_ORDER}


def _wasxfail(record):
    details = record.get("phase_details") or {}
    return any((details.get(when) or {}).get("wasxfail") is not None for when in PHASE_ORDER)


def summary_of(record):
    """由 ``pytest_jsonl.build_per_test`` 的逐测试记录派生汇总标签。

    返回 ``{"summary": …, "overlays": [...], "call_passed_but_not_overall_passed": bool,
    "phase_states": {...}, "phase_outcomes": {...}}``。
    """
    states = _states(record)
    outcomes = _outcomes(record)
    labels = set(record.get("derived_labels") or [])
    overlays = []

    missing = [when for when in PHASE_ORDER if states[when] == "report_missing"]
    if missing:
        overlays.append("incomplete")
        return _result("incomplete", overlays, states, outcomes, False)

    failed_phases = [when for when in ("setup", "teardown") if outcomes[when] == "failed"]
    skipped_phases = [when for when in PHASE_ORDER if outcomes[when] == "skipped"]
    call = outcomes["call"]

    if failed_phases:
        # setup / teardown 失败 → error；call 若也失败，其原生结果仍保留在 phase_outcomes 中。
        overlays.append("error")
        for when in failed_phases:
            overlays.append(f"error_phase:{when}")
        if call == "passed":
            overlays.append("call_passed_not_overall_passed")
        if "xpassed_strict" in labels:
            overlays.append("xpassed_strict_call_evidence")
        return _result("error", overlays, states, outcomes, call == "passed")

    if call == "failed":
        if "xpassed_strict" in labels:
            overlays.append("xpassed_strict")
            return _result("xpassed_strict", overlays, states, outcomes, False)
        return _result("failed", overlays, states, outcomes, False)

    if call == "skipped" or (call is None and states["call"] == "not_run"):
        if _wasxfail(record) or "xfailed" in labels:
            overlays.append("xfailed")
            return _result("xfailed", overlays, states, outcomes, False)
        return _result("skipped", overlays, states, outcomes, False)

    if call == "passed":
        if skipped_phases:
            # call 通过但其他阶段被跳过，例如 teardown skip：不能算 passed。
            overlays.append("call_passed_not_overall_passed")
            for when in skipped_phases:
                overlays.append(f"skipped_phase:{when}")
            if "xpassed" in labels:
                overlays.append("xpassed")
                return _result("xpassed", overlays, states, outcomes, True)
            return _result("skipped", overlays, states, outcomes, True)
        # 所需阶段齐备、call passed、其余阶段均无 failed/skipped —— 这才是 passed。
        if _wasxfail(record) or "xpassed" in labels:
            overlays.append("xpassed")
            return _result("xpassed", overlays, states, outcomes, False)
        return _result("passed", overlays, states, outcomes, False)

    overlays.append("call_outcome_unavailable")
    return _result("not_run", overlays, states, outcomes, False)


def _result(summary, overlays, states, outcomes, call_passed_flag):
    return {"summary": summary, "overlays": sorted(set(overlays)),
            "call_passed_but_not_overall_passed": bool(call_passed_flag),
            "phase_states": dict(states), "phase_outcomes": dict(outcomes)}


def vector_of(per_test):
    """逐测试记录字典 → ``{nodeid: 汇总标签}`` 的比较向量。"""
    return {node: summary_of(record)["summary"] for node, record in per_test.items()}


def vector_rows(per_test):
    """可用于逐字节比对的稳定行集合：``[(nodeid, summary, overlays...)]``。"""
    return sorted([node, summary_of(record)["summary"],
                   ",".join(summary_of(record)["overlays"])]
                  for node, record in per_test.items())
