"""ClawsGO Science Agent：PyHost-Context 准入 B0 通过判定（可测试函数，不启动候选项目）。

``admission_gate`` 对**若干轮**原始 JSONL 工件做准入判定。它只消费 observer/producer
产出的 native JSONL 与进程元信息，不自行启动任何进程，故可对合成套件反复测试。

准入条件（全部满足才 ``admitted``）：

1. 轮数恰为 ``expected_rounds``（默认 5）；
2. 每轮 JSONL 可解析（无 malformed 行）；
3. 每轮进程 returncode 为**严格整数 0**（非 bool、非字符串），且未超时；
4. 每轮含完整 ``SessionFinished``，其 ``exitstatus`` 为严格整数 0；
5. 每轮含 ``CollectionFinished``，collection 无错误且至少有一个 nodeid；
6. 每轮原始报告完整（无 stage 重复/不合法/缺失）；
7. 每轮至少有一个 call 真正执行（call outcome 为 passed/failed）；
8. 每轮无未预期结果：``error``、``xpassed_strict`` 或普通 ``failed`` 一律拒绝；
9. 每轮的 skip/xfail/non-strict XPASS 按**单独明示 policy** 允许或拒绝（不算普通 pass）；
10. 五轮身份指纹与结果向量（nodeid→raw_outcome+labels）完全一致。

反例（不得通过）：五轮稳定失败、全 skip、returncode 非 0、缺 SessionFinished 等。
"""
from pytest_jsonl import build_per_test, collection_info, parse_jsonl

EXPECTED_ROUNDS = 5
POLICY_KEYS = ("allow_skips", "allow_xfails", "allow_xpasses")


def _is_strict_int_zero(value):
    return isinstance(value, int) and not isinstance(value, bool) and value == 0


def _classify(prefix, per_test, policy):
    reasons = []
    for node, record in sorted(per_test.items()):
        labels = record["derived_labels"]
        raw = record["raw_outcome"]
        if "error" in labels:
            reasons.append(f"{prefix}:error:{node}")
        elif "xpassed_strict" in labels:
            reasons.append(f"{prefix}:strict_xpass:{node}")
        elif raw == "failed":
            reasons.append(f"{prefix}:failure:{node}")
        elif "xfailed" in labels or "xfail_summary_unresolved" in labels:
            if not policy.get("allow_xfails"):
                reasons.append(f"{prefix}:xfail:{node}")
        elif raw == "skipped":
            if not policy.get("allow_skips"):
                reasons.append(f"{prefix}:skip:{node}")
        elif "xpassed" in labels:
            if not policy.get("allow_xpasses"):
                reasons.append(f"{prefix}:xpass:{node}")
    return reasons


def _check_round(index, round_artifact, policy):
    prefix = f"round{index}"
    reasons = []
    text = round_artifact.get("jsonl")
    if round_artifact.get("timed_out"):
        reasons.append(f"{prefix}:timed_out")
    returncode = round_artifact.get("returncode")
    if not _is_strict_int_zero(returncode):
        reasons.append(f"{prefix}:returncode_not_int_zero")

    records, malformed = parse_jsonl(text or "")
    if malformed:
        reasons.append(f"{prefix}:malformed_jsonl")

    sessions = [r for r in records if r.get("$report_type") == "SessionFinished"]
    if not sessions:
        reasons.append(f"{prefix}:session_finished_missing")
    elif not _is_strict_int_zero(sessions[-1].get("exitstatus")):
        reasons.append(f"{prefix}:session_exitstatus_not_zero")

    collection = collection_info(records)
    if collection["collection_finished"] is None:
        reasons.append(f"{prefix}:collection_finished_missing")
    if collection["errors"]:
        reasons.append(f"{prefix}:collection_errors")
    if not collection["collected"]:
        reasons.append(f"{prefix}:no_collected_nodeids")

    per_test = build_per_test(records, collection["collected"])
    if any(not record["raw_complete"] for record in per_test.values()):
        reasons.append(f"{prefix}:incomplete_raw_reports")

    executed = any(
        record["phase_details"].get("call", {}).get("outcome") in ("passed", "failed")
        for record in per_test.values())
    if not executed:
        reasons.append(f"{prefix}:no_call_executed")

    reasons.extend(_classify(prefix, per_test, policy))

    digest = {
        "index": index,
        "identity_fingerprint": collection["identity_fingerprint"],
        "result_vector": tuple(sorted(
            (node, record["raw_outcome"], tuple(record["derived_labels"]))
            for node, record in per_test.items())),
        "collected_count": collection["collected_count"],
    }
    return reasons, digest


def admission_gate(rounds, *, policy, expected_rounds=EXPECTED_ROUNDS):
    """对若干轮工件做准入判定，返回 ``{"admitted", "reasons", "rounds"}``。

    ``rounds``：``{jsonl, returncode, timed_out}`` 的列表（见 ``integration.pipeline.run_round``）。
    ``policy``：**必须明示**的 skip/xfail/xpass 策略，键为
    ``allow_skips``/``allow_xfails``/``allow_xpasses``（缺失即视为拒绝）。
    """
    missing = [key for key in POLICY_KEYS if key not in policy]
    reasons = []
    if missing:
        reasons.append("policy_incomplete:" + ",".join(missing))
    if len(rounds) != expected_rounds:
        reasons.append(f"round_count:{len(rounds)}!={expected_rounds}")

    digests = []
    for index, round_artifact in enumerate(rounds, 1):
        round_reasons, digest = _check_round(index, round_artifact, policy)
        reasons.extend(round_reasons)
        digests.append(digest)

    if len(digests) > 1:
        if len({d["identity_fingerprint"] for d in digests}) != 1:
            reasons.append("identity_drift")
        if len({d["result_vector"] for d in digests}) != 1:
            reasons.append("result_vector_drift")
    return {"admitted": not reasons, "reasons": reasons, "rounds": digests}
