"""ClawsGO Science Agent：解析 pytest 原始 JSONL 报告，派生阶段状态、collection 信息与逐测试记录。

原始 schema（由 producer 产出，本模块只解析，不改写 native 语义）：

- ``CollectReport``：pytest **原生** collection 报告（root/package/module 等），原样保留；
- ``CollectionItem``：每个**最终被选中**的测试项一条（``nodeid``）；
- ``CollectionFinished``：collection 结束的身份/顺序摘要；
- ``DeselectedReport``：被取消选择的测试项；
- ``TestReport``：逐测试 setup/call/teardown 的**原生**报告，含 ``when``/``outcome``/
  ``longrepr`` 与 native ``wasxfail``；
- ``RuntimeSnapshot`` / ``SessionFinished``：运行期只读快照与会话结束标记。

设计约束：

- **只用 native ``wasxfail``** 认定 ``xfailed``/``xpassed``；不用 marker 兜底，故
  ``xfail(condition=False)`` 与 ``--runxfail`` 不会被误标；
- 严格 XPASS 保留 native ``call outcome=failed`` 与原 ``longrepr``，仅凭 native
  ``[XPASS(strict)]`` 标记派生 ``xpassed_strict``，**不伪造 ``wasxfail``**；
- 逐测试保留每个阶段的 raw ``outcome``/``wasxfail``/``longrepr``（``phase_details``），
  并对 stage 重复/不合法/缺失给出 ``stage_integrity`` 与 ``raw_complete``；
- 失败签名**不做通用删除**（``0x`` 值或 ``d.ddds`` 可能是 expected/actual/hash/时长信号），
  仅保守替换显式对象 repr 地址与已登记运行目录前缀。
"""
import hashlib
import json
import re

RAW_OUTCOMES = ("passed", "failed", "skipped")
PHASE_STATES = ("reported", "not_run", "report_missing")
DERIVED_LABELS = ("error", "xfailed", "xpassed", "xpassed_strict",
                  "incomplete", "xfail_summary_unresolved")
ROOT_CAUSES = ("target_environment_supported", "external_infrastructure_supported",
               "state_pollution_supported", "unresolved")

COLLECT_REPORT = "CollectReport"
COLLECTION_ITEM = "CollectionItem"
COLLECTION_FINISHED = "CollectionFinished"
DESELECTED_REPORT = "DeselectedReport"
TEST_REPORT = "TestReport"
RUNTIME_SNAPSHOT = "RuntimeSnapshot"
SESSION_FINISHED = "SessionFinished"

# pytest 对严格 XPASS 的原生长报告标记（native，不作为 wasxfail 使用）。
XPASS_STRICT_MARKER = "[XPASS(strict)]"

_PHASE_ORDER = ("setup", "call", "teardown")
# 取结果与 longrepr 时以 call 优先，其次 setup，再退到 teardown。
_OUTCOME_PRIORITY = ("call", "setup", "teardown")

# 失败签名只做保守归一化：显式对象 repr 地址（"at 0x...."）之外一律保留。
_OBJECT_REPR_ADDRESS = re.compile(r"(?<= at )0x[0-9a-fA-F]+(?=>)")


def parse_jsonl(text):
    """逐行解析原始 JSONL；返回 (记录列表, 无法解析的行)。"""
    records = []
    malformed = []
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            malformed.append({"line": number, "raw": line, "error": str(exc)})
    return records, malformed


def identity_fingerprint(nodeids):
    payload = json.dumps(list(nodeids), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def collection_info(records, baseline_nodeids=None):
    """收集层信息：原生 CollectReport 原样保留；选中项来自 CollectionItem。

    兼容旧 fixture：若没有 ``CollectionItem`` 记录，则回退为“非失败 CollectReport 的
    nodeid 即已收集项”（旧行为）。实际新 producer 不伪造 native CollectReport。
    """
    items, deselected, native_reports = [], [], []
    finished = None
    for record in records:
        kind = record.get("$report_type")
        if kind == COLLECTION_ITEM:
            items.append(record.get("nodeid"))
        elif kind == COLLECTION_FINISHED:
            finished = record
        elif kind == COLLECT_REPORT:
            native_reports.append(record)
        elif kind == DESELECTED_REPORT:
            deselected.append(record.get("nodeid"))

    errors = [{"nodeid": record.get("nodeid"), "longrepr": record.get("longrepr")}
              for record in native_reports if record.get("outcome") == "failed"]
    if items:
        nodeids = [node for node in items if node is not None]
    else:
        nodeids = [record.get("nodeid") for record in native_reports
                   if record.get("outcome") != "failed" and record.get("nodeid") is not None]
    info = {
        "collected": nodeids,
        "collected_count": len(nodeids),
        "order": list(nodeids),
        "errors": errors,
        "deselected": deselected,
        "identity_fingerprint": identity_fingerprint(nodeids),
        "native_collect_reports": native_reports,
        "collection_finished": finished,
        "uses_collection_items": bool(items),
    }
    if baseline_nodeids is not None:
        base = set(baseline_nodeids)
        current = set(nodeids)
        info["added"] = [node for node in nodeids if node not in base]
        info["missing"] = [node for node in baseline_nodeids if node not in current]
    return info


def _node_reports(records, nodeids):
    """node → {when: [report, ...]}；保留同阶段重复以支持完整性判定。"""
    known = set(nodeids)
    by_node = {}
    for record in records:
        if record.get("$report_type") != TEST_REPORT:
            continue
        node = record.get("nodeid")
        if node not in known:
            continue
        by_node.setdefault(node, {}).setdefault(record.get("when"), []).append(record)
    return by_node


def _single_phases(reports_by_when):
    return {when: reports[0] for when, reports in reports_by_when.items()}


def _phase_states_from(by_node, nodeids):
    result = {}
    for node in nodeids:
        phases = _single_phases(by_node.get(node, {}))
        setup = phases.get("setup")
        if setup is None:
            setup_state = "report_missing"
            call_state = "report_missing"
        else:
            setup_state = "reported"
            if setup.get("outcome") in ("failed", "skipped"):
                call_state = "reported" if "call" in phases else "not_run"
            else:
                call_state = "reported" if "call" in phases else "report_missing"
        teardown_state = "reported" if "teardown" in phases else "report_missing"
        result[node] = {"setup": setup_state, "call": call_state, "teardown": teardown_state}
    return result


def phase_states(records, nodeids):
    """逐测试三阶段状态：reported / not_run / report_missing。

    setup 失败或跳过时，可确定 call 未进入，记为 not_run；其余缺失无法确认执行与否，
    记为 report_missing。
    """
    return _phase_states_from(_node_reports(records, nodeids), nodeids)


def _raw_outcome(phases):
    for when in _OUTCOME_PRIORITY:
        report = phases.get(when)
        if report is not None and report.get("outcome") in RAW_OUTCOMES:
            return report.get("outcome")
    return None


def _longrepr(phases):
    for when in _OUTCOME_PRIORITY:
        report = phases.get(when)
        if report is not None and report.get("longrepr"):
            return report["longrepr"]
    return None


def failure_signature(text, run_dir_prefixes=()):
    """失败签名（保守）：只替换显式对象 repr 地址（``at 0x....``）与已登记运行目录前缀。

    其余内容——包括可能是 expected/actual/hash/时长信号的 ``0x`` 值与 ``d.ddds``——
    一律原样保留，避免抹去语义。
    """
    if text is None:
        return None
    normalized = _OBJECT_REPR_ADDRESS.sub("<object_address>", text)
    for prefix in run_dir_prefixes:
        if prefix:
            normalized = normalized.replace(prefix, "<run_dir>")
    return normalized


def _is_strict_xpass(call):
    longrepr = call.get("longrepr")
    return bool(longrepr) and XPASS_STRICT_MARKER in longrepr


def derived_labels(phases, phase_state_map):
    """派生汇总标签：error / xfailed / xpassed / xpassed_strict / incomplete / xfail_summary_unresolved。

    只依据 native ``wasxfail`` 认定 xfail/xpass；严格 XPASS 只依据 native ``[XPASS(strict)]``
    长报告，且不伪造 ``wasxfail``。
    """
    labels = set()
    setup, call, teardown = phases.get("setup"), phases.get("call"), phases.get("teardown")
    if (setup and setup.get("outcome") == "failed") or (teardown and teardown.get("outcome") == "failed"):
        labels.add("error")
    if call is not None:
        outcome = call.get("outcome")
        native_xfail = call.get("wasxfail") is not None
        if outcome == "skipped" and native_xfail:
            labels.add("xfailed")
        elif outcome == "passed" and native_xfail:
            labels.add("xpassed")
        elif outcome == "failed" and _is_strict_xpass(call):
            labels.add("xpassed_strict")
    elif setup is not None and setup.get("wasxfail") is not None:
        # setup 阶段 xfail（如 xfail(run=False)）：setup skipped + native wasxfail。
        labels.add("xfailed")
    elif any(report.get("wasxfail") is not None for report in phases.values()):
        labels.add("xfail_summary_unresolved")
    if "report_missing" in phase_state_map.values():
        labels.add("incomplete")
    return sorted(labels)


def _xfail_fields(phases):
    for when in _OUTCOME_PRIORITY:
        report = phases.get(when)
        marker = None if report is None else report.get("wasxfail")
        if marker is not None:
            reason = marker.get("reason") if isinstance(marker, dict) else marker
            return True, reason
    return False, None


def _stage_integrity(reports_by_when):
    """stage 重复/不合法/缺失 → 完整性问题列表（空即完整）。"""
    problems = set()
    for when, reports in reports_by_when.items():
        if when not in _PHASE_ORDER:
            problems.add("illegal_stage")
        elif len(reports) > 1:
            problems.add("duplicate_stage")
    phases = _single_phases(reports_by_when)
    setup = phases.get("setup")
    if setup is None:
        problems.add("missing_setup")
    else:
        if "teardown" not in phases:
            problems.add("missing_teardown")
        if setup.get("outcome") == "passed" and "call" not in phases:
            problems.add("missing_call")
    return sorted(problems)


def _phase_details(phases):
    return {
        when: {
            "outcome": report.get("outcome"),
            "wasxfail": report.get("wasxfail"),
            "longrepr": report.get("longrepr"),
        }
        for when, report in phases.items() if when in _PHASE_ORDER
    }


def build_per_test(records, nodeids, baseline_nodeids=None, run_dir_prefixes=()):
    """逐测试记录：raw 结果、阶段状态、逐阶段 raw 明细、派生标签、失败签名、完整性、identity 叠加。

    ``identity_changed`` 独立保存，可与失败、错误、跳过并存，不覆盖原始结果。
    """
    by_node = _node_reports(records, nodeids)
    phase_map = _phase_states_from(by_node, nodeids)
    base = set(baseline_nodeids) if baseline_nodeids is not None else None
    per_test = {}
    for node in nodeids:
        reports = by_node.get(node, {})
        phases = _single_phases(reports)
        labels = derived_labels(phases, phase_map[node])
        has_xfail, reason = _xfail_fields(phases)
        integrity = _stage_integrity(reports)
        longrepr = _longrepr(phases)
        per_test[node] = {
            "nodeid": node,
            "raw_outcome": _raw_outcome(phases),
            "phases": phase_map[node],
            "phase_details": _phase_details(phases),
            "derived_labels": labels,
            "wasxfail": has_xfail,
            "xfail_reason": reason,
            "xfail_strict": "xpassed_strict" in labels,
            "longrepr": longrepr,
            "failure_signature": failure_signature(longrepr, run_dir_prefixes=run_dir_prefixes),
            "stage_integrity": integrity,
            "raw_complete": not integrity,
            "identity_changed": base is not None and node not in base,
            "root_cause": "unresolved",
        }
    return per_test


def with_root_cause(record, root_cause):
    """设置 root_cause；未知取值回退为 unresolved。"""
    updated = dict(record)
    updated["root_cause"] = root_cause if root_cause in ROOT_CAUSES else "unresolved"
    return updated
