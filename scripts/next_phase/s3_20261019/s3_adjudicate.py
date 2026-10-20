"""ClawsGO Science Agent：S3 判定函数（可执行、纯函数、不导入候选）。

修订版按 Reviewer 001 五组要求收紧：

* 目标身份、断言位置、异常类型、激活与健康参照分别登记；
* 缺失/无效参照、未激活、无关异常不得变成检出或盲区；
* equal-pass 在真实行表上可达，原产品控制参与可评价性；
* 见证空载荷/错误身份/错误语义一律记技术缺口；
* 判定只消费适配器产出的完整观测。
"""
from __future__ import annotations

import argparse
import json
import pathlib

VERDICTS = (
    "RETAINED",
    "RETAINED_OUTSIDE_REQUESTED_CONTEXT",
    "DEGRADED",
    "PRE_EXISTING_BLIND_SPOT",
    "GAINED_DETECTION_ONLY",
    "CONTEXT_RESTRICTED",
    "CHECK_NOT_EXECUTED",
    "NO_DETECTION_NO_REFERENCE",
    "MUTATION_NOT_ACTIVE_IN_CONTEXT",
    "ACTIVATION_UNKNOWN",
    "DETECTION_WITHOUT_ACTIVATION",
    "NOT_EVALUABLE_MISSING_S0",
    "NOT_EVALUABLE_MISSING_ACTIVATION",
    "NOT_EVALUABLE_CONTROL_FAILURE",
    "TECHNICAL_FAILURE",
)

CYAN_FG = {6, 36}      # 基本青色 36 与 256 色索引 36（dark_cyan）
MAGENTA_FG = {5, 35}   # 基本洋红 35 与 256 色索引 35（dark_magenta）
CATEGORY = "Error parsing reStructuredText"
EXCEPTION_DETAIL = "ValueError"
EXCEPTION_MESSAGE = "Fake parse failure"


# --------------------------------------------------------------- usability

def row_is_usable(result: dict | None) -> bool:
    """完整观测的最低条件；不满足即不进入科学判定。"""
    if not isinstance(result, dict):
        return False
    if result.get("status") != "completed":
        return False
    if result.get("timed_out"):
        return False
    returncode = result.get("returncode")
    if not isinstance(returncode, int) or returncode < 0:
        return False
    session = result.get("session")
    if not isinstance(session, dict):
        return False
    if session.get("exitstatus") != returncode:
        return False
    if result.get("malformed_jsonl_lines", 0) != 0:
        return False
    if result.get("tree_after_ok") is False:
        return False
    if result.get("collection_finished") is False:
        return False
    boot = result.get("boot_views")
    if isinstance(boot, dict) and not all(boot.values()):
        return False
    network = result.get("network")
    if isinstance(network, dict) and network.get("ok") is False:
        return False
    if "collection_error" in (result.get("problems") or []):
        return False
    return True


def phase_entry(record: dict | None, phase: str = "call") -> dict:
    if not isinstance(record, dict):
        return {}
    return (record.get("phase_details") or {}).get(phase) or {}


def phase_outcome(record: dict | None, phase: str = "call") -> str | None:
    return phase_entry(record, phase).get("outcome")


def failed_nodes(result: dict, phase: str = "call") -> set[str]:
    out = set()
    for node, record in (result.get("node_outcomes") or {}).items():
        outcome = phase_outcome(record, phase)
        if outcome in ("failed", "error"):
            out.add(node)
    return out


def node_record(result: dict, node: str) -> dict | None:
    return (result.get("node_outcomes") or {}).get(node)


# --------------------------------------------------------------- identity

def _split_node(node: str) -> tuple[str, list[str]]:
    if "[" not in node or not node.endswith("]"):
        return node, []
    function, rest = node.split("[", 1)
    return function, rest[:-1].split("-")


def match_target(node: str, identity: dict | None) -> str | None:
    """把观测 nodeid 映射到登记的目标身份。

    参数 id 按 pytest 实际顺序以 ``-`` 连接（新增 fixture 参数可能在既有参数之前），
    因此按**完整 token** 判断：节点 token 必须包含基节点的全部 token 与登记 id。
    这仍是精确身份约束，不做无约束前缀或子串兜底。
    """
    identity = identity or {}
    base_nodes = identity.get("base_nodes") or []
    if node in base_nodes:
        return node
    if identity.get("mode") != "parameterized":
        return None
    parameter_ids = identity.get("parameter_ids") or []
    node_function, node_tokens = _split_node(node)
    for base in base_nodes:
        base_function, base_tokens = _split_node(base)
        if node_function != base_function:
            continue
        token_set = set(node_tokens)
        if not set(base_tokens) <= token_set:
            continue
        for parameter_id in parameter_ids:
            if parameter_id in token_set:
                return base
    return None


def signature_match(record: dict | None, signature: dict | None) -> bool:
    """核对登记的异常类型与断言位置，而不是“call 失败”这一循环定义。"""
    if not isinstance(record, dict) or not isinstance(signature, dict):
        return False
    longrepr = record.get("longrepr") or ""
    exception = signature.get("exception")
    if exception and exception not in longrepr:
        return False
    locations = signature.get("assertion_locations") or []
    if not locations:
        return False
    for location in locations:
        file = location.get("file")
        if not file or file not in longrepr:
            continue
        lines = location.get("lines") or []
        if lines:
            if any(f"{file}:{line}" in longrepr for line in lines):
                return True
        elif location.get("function") and location["function"] in longrepr:
            return True
    return False


def assertion_state(node: str, result: dict, row: dict) -> str:
    """断言执行状态：与“行为执行”分开，未知保留未知。"""
    record = node_record(result, node)
    outcome = phase_outcome(record)
    if outcome not in ("passed", "failed"):
        return "not_executed"
    if outcome == "passed":
        return "call_completed_no_failure"
    signature = row["expected"].get("failure_signature")
    if signature_match(record, signature):
        return "executed_registered_assertion"
    return "executed_other_location"


# --------------------------------------------------------------- suite cell

def suite_cell(row: dict, result: dict | None, baseline: dict | None) -> dict:
    identity = row["expected"].get("identity") or {}
    signature = row["expected"].get("failure_signature") or {}
    target_nodes = list(identity.get("base_nodes") or row["expected"].get("target_nodes") or [])
    cell = {
        "row_key": row["row_key"], "case": row["case"], "context": row["context"],
        "mutation_id": row.get("mutation_id"),
        "suite_variant": row["suite_variant"], "product_variant": row["product_variant"],
        "applicability": row["applicability"],
        "effective_context": row["effective_context"],
        "requested_context": row["requested_context"],
        "covers_requested_branch": row.get("covers_requested_branch"),
        "coverage_reason": row.get("coverage_reason"),
        "target_base_nodes": target_nodes,
        "returncode": None, "timed_out": None,
        "s0_passes_in_context": None, "health_reference_usable": False,
        "background_failure": False,
        "matched_nodes": [],
        "observed_target_children": [],
        "target_behaviour_executed": False,
        "target_assertion_executed": "unknown",
        "detected_in_target_phase": False,
        "failure_signature_matches": False,
        "detection": False,
        "verdict": None,
        "notes": [],
    }
    if not row_is_usable(result):
        cell["verdict"] = "TECHNICAL_FAILURE"
        cell["notes"].append("run_missing_or_unusable")
        return cell
    cell["returncode"] = result.get("returncode")
    cell["timed_out"] = bool(result.get("timed_out"))
    baseline_usable = row_is_usable(baseline)
    cell["health_reference_usable"] = baseline_usable
    if baseline_usable:
        cell["s0_passes_in_context"] = baseline.get("returncode") == 0
    baseline_failed = failed_nodes(baseline) if baseline_usable else set()
    child_records = {}
    for node, record in (result.get("node_outcomes") or {}).items():
        base = match_target(node, identity)
        if base is not None:
            child_records[node] = (base, record)
    cell["observed_target_children"] = sorted(child_records)
    cell["matched_nodes"] = sorted({base for base, _ in child_records.values()})
    cell["background_failure"] = bool(set(child_records) & baseline_failed)
    executed = [node for node, (_, record) in child_records.items()
                if phase_outcome(record) in ("passed", "failed")]
    cell["target_behaviour_executed"] = bool(executed)
    if not target_nodes:
        cell["target_assertion_executed"] = "no_target_nodes"
        cell["notes"].append("mutation_declared_no_suite_target")
    elif not executed:
        cell["target_assertion_executed"] = "not_executed"
        cell["notes"].append("target_nodes_not_executed")
    else:
        states = [assertion_state(node, result, row) for node in executed]
        if "executed_registered_assertion" in states:
            cell["target_assertion_executed"] = "executed_registered_assertion"
        elif "executed_other_location" in states:
            cell["target_assertion_executed"] = "executed_other_location"
        else:
            cell["target_assertion_executed"] = "call_completed_no_failure"
    failed = {node for node in child_records if phase_outcome(child_records[node][1]) == "failed"}
    cell["detected_in_target_phase"] = bool(failed)
    if not baseline_usable:
        cell["notes"].append("health_reference_missing")
        cell["detection"] = False
        cell["failure_signature_matches"] = False
        return cell
    attributable = False
    for node in failed:
        if node in baseline_failed:
            continue
        if signature_match(child_records[node][1], signature):
            attributable = True
            break
    cell["detection"] = attributable
    cell["failure_signature_matches"] = attributable
    if failed and not attributable:
        cell["notes"].append("failure_not_at_registered_signature")
    if not sample_collected(result):
        cell["notes"].append("nothing_collected")
    if row["suite_variant"] == "S0_original":
        cell["s0_detection_reference"] = cell["detection"]
    return cell


def sample_collected(result: dict) -> bool:
    collected = result.get("collected")
    return bool(collected) if collected is not None else True


# --------------------------------------------------------------- classify

def classify(cell: dict, s0_cell: dict | None, witness: dict | None,
             control: dict | None = None) -> dict:
    out = dict(cell)
    if out["verdict"] == "TECHNICAL_FAILURE":
        return out
    if control is not None and control.get("verdict") == "CONTROL_FAILURE":
        out["verdict"] = "NOT_EVALUABLE_CONTROL_FAILURE"
        out["control"] = control
        return out
    witness_detects = bool(witness and witness.get("detection") is True)
    witness_state = (witness or {}).get("state", "unknown")
    out["witness_detection"] = witness_detects
    out["witness_state"] = witness_state
    if out["suite_variant"] == "S0_original":
        out["verdict"] = "S0_REFERENCE"
        return out
    if not out["health_reference_usable"]:
        out["verdict"] = "NOT_EVALUABLE_MISSING_S0"
        return out
    s0_usable = bool(s0_cell) and s0_cell.get("verdict") != "TECHNICAL_FAILURE" \
        and s0_cell.get("health_reference_usable")
    if not s0_usable:
        out["verdict"] = "NOT_EVALUABLE_MISSING_S0"
        return out
    if out["target_base_nodes"] and not out["target_behaviour_executed"]:
        out["verdict"] = "CHECK_NOT_EXECUTED"
        return out
    if not out["target_base_nodes"]:
        if witness_detects:
            out["verdict"] = "PRE_EXISTING_BLIND_SPOT"
            out["gained_detection"] = False
            out["notes"].append("suite_declares_no_target_nodes")
        elif witness.get("detection") is None:
            out["verdict"] = "NOT_EVALUABLE_MISSING_ACTIVATION"
        else:
            out["verdict"] = "NO_DETECTION_NO_REFERENCE"
        return out
    s0_detects = bool(s0_cell.get("detection"))
    out["s0_detection"] = s0_detects
    if not s0_detects:
        if witness.get("detection") is None:
            out["verdict"] = "NOT_EVALUABLE_MISSING_ACTIVATION"
        elif witness_detects:
            out["verdict"] = "PRE_EXISTING_BLIND_SPOT"
            out["gained_detection"] = bool(out["detection"])
        else:
            out["verdict"] = "NO_DETECTION_NO_REFERENCE"
        return out
    # S0 在同一上下文检出。
    if out["applicability"] == "restricted" or out["covers_requested_branch"] is False:
        out["verdict"] = ("RETAINED_OUTSIDE_REQUESTED_CONTEXT" if out["detection"]
                          else "CONTEXT_RESTRICTED")
        return out
    if out["detection"]:
        if witness.get("detection") is None:
            out["verdict"] = "ACTIVATION_UNKNOWN"
        elif not witness_detects:
            out["verdict"] = "DETECTION_WITHOUT_ACTIVATION"
        else:
            out["verdict"] = "RETAINED"
    elif witness.get("detection") is None:
        out["verdict"] = "ACTIVATION_UNKNOWN"
    elif witness_detects:
        out["verdict"] = "DEGRADED"
    else:
        out["verdict"] = "MUTATION_NOT_ACTIVE_IN_CONTEXT"
    return out


# --------------------------------------------------------------- control

def control_cell(row: dict, result: dict | None, baseline: dict | None) -> dict:
    cell = {"row_key": row["row_key"], "case": row["case"], "context": row["context"],
            "suite_variant": row["suite_variant"], "product_variant": "original",
            "applicability": row["applicability"],
            "effective_context": row["effective_context"],
            "covers_requested_branch": row.get("covers_requested_branch"),
            "returncode": None, "failed_nodes": [], "background_failed_nodes": [],
            "new_failures": [], "verdict": None}
    if not row_is_usable(result):
        cell["verdict"] = "TECHNICAL_FAILURE"
        return cell
    cell["returncode"] = result.get("returncode")
    failed = sorted(failed_nodes(result))
    background = sorted(failed_nodes(baseline)) if row_is_usable(baseline) else []
    cell["failed_nodes"] = failed
    cell["background_failed_nodes"] = background
    cell["new_failures"] = sorted(set(failed) - set(background))
    cell["verdict"] = ("CONTROL_FAILURE" if cell["new_failures"]
                       else "CONTROL_ORIGINAL_PRODUCT")
    return cell


# --------------------------------------------------------------- witnesses

def decode_foreground(sgr: str | None):
    """把 SGR 参数解码为前景色；返回基本色索引、256 色索引、RGB 元组或 None。"""
    if not isinstance(sgr, str) or not sgr:
        return None
    try:
        parameters = [int(part) for part in sgr.split(";") if part != ""]
    except ValueError:
        return None
    index = 0
    while index < len(parameters):
        code = parameters[index]
        if 30 <= code <= 37:
            return code - 30
        if 90 <= code <= 97:
            return code - 90 + 8
        if code == 38:
            if index + 1 < len(parameters) and parameters[index + 1] == 5 \
                    and index + 2 < len(parameters):
                return parameters[index + 2]
            if index + 1 < len(parameters) and parameters[index + 1] == 2 \
                    and index + 4 < len(parameters):
                return tuple(parameters[index + 2:index + 5])
            return None
        index += 1
    return None


def _payload_problem(case: str, payload: dict | None) -> str | None:
    if not isinstance(payload, dict):
        return "payload_missing"
    if payload.get("case") != case:
        return "case_mismatch"
    if not isinstance(payload.get("errors"), dict):
        return "errors_not_object"
    if payload["errors"]:
        return "probe_errors"
    observations = payload.get("observations")
    if not isinstance(observations, dict) or not observations:
        return "observations_missing"
    required = {
        "A": ("colour", {"colour_present": bool}),
        "B": ("width", {"formatter_width_default": int,
                        "formatter_width_explicit_70": int,
                        "argparse_width_explicit_70": int,
                        "max_help_line_width_default": int}),
        "C": ("panel", {"exit_code": int, "max_line_width": int,
                        "unwrapped": str, "repeat_stable": bool}),
    }[case]
    name, fields = required
    block = observations.get(name)
    if not isinstance(block, dict):
        return f"{name}_missing"
    for field, expected_type in fields.items():
        if field not in block or not isinstance(block[field], expected_type):
            return f"{name}_{field}_invalid"
    if case == "A":
        if "argparse_args_sgr" not in block:
            return "colour_sgr_missing"
        sgr = block.get("argparse_args_sgr")
        if sgr is not None and not isinstance(sgr, str):
            return "colour_sgr_invalid"
        if block.get("colour_present") is True and not isinstance(sgr, str):
            return "colour_sgr_missing_for_colour"
    return None


def witness_verdict(case: str, context: str, product: str, payload: dict | None,
                    spec: dict) -> dict:
    case_spec = spec[case] if case in spec else spec
    base = {"case": case, "context": context, "product_variant": product,
            "state": "unknown", "detection": None, "observations": {},
            "expected_original": case_spec["expected_original"].get(context, {})}
    problem = _payload_problem(case, payload)
    if problem:
        base["state"] = "technical_gap"
        base["technical_gap"] = problem
        return base
    observations = payload["observations"]
    base["observations"] = observations
    expected = base["expected_original"]
    state = (expected.get("context_environment") or {}).get("state", "set")
    if case == "A":
        block = observations["colour"]
        present = block["colour_present"]
        sgr = block.get("argparse_args_sgr")
        fg = decode_foreground(sgr)
        base["decoded_foreground"] = fg
        if product == "original":
            ok = (present is True) == bool(expected.get("colour_present"))
            base["state"] = "original_behaviour" if ok else "original_deviation"
            base["detection"] = False if ok else None
            return base
        if product == "M1":
            if not expected.get("colour_role_observable"):
                base["state"] = "not_observable"
                return base
            if fg in CYAN_FG:
                base["state"] = "not_detected"
                base["detection"] = False
            elif fg in MAGENTA_FG:
                base["state"] = "detected"
                base["detection"] = True
            elif fg is None:
                base["state"] = "colour_absent"
            else:
                base["state"] = "unexpected_colour_spec"
            return base
        if product == "M2":
            if state != "set":
                base["state"] = "not_applicable"
                return base
            base["detection"] = bool(present)
            base["state"] = "detected" if present else "not_detected"
            return base
        if product == "M3":
            if state != "empty":
                base["state"] = "not_applicable"
                return base
            base["detection"] = present is False
            base["state"] = "detected" if present is False else "not_detected"
            return base
    if case == "B":
        block = observations["width"]
        if product == "original":
            ok = all(block.get(key) == value for key, value in expected.items()
                     if key != "context_environment")
            base["state"] = "original_behaviour" if ok else "original_deviation"
            base["detection"] = False if ok else None
            return base
        if product == "M1":
            width_wrong = block["formatter_width_default"] != expected["formatter_width_default"]
            output_wrong = (block["max_help_line_width_default"]
                            != expected["max_help_line_width_default"])
            base["detection"] = bool(width_wrong and output_wrong)
        elif product == "M2":
            base["state"] = "not_activated_by_declaration"
            base["diagnostic_explicit_width"] = block["formatter_width_explicit_70"]
            return base
        elif product == "M3":
            width_wrong = block["argparse_width_explicit_70"] != expected["argparse_width_explicit_70"]
            output_wrong = (block.get("argparse_max_line_width_explicit_70")
                            != expected["argparse_max_line_width_explicit_70"])
            base["detection"] = bool(width_wrong and output_wrong)
        base["state"] = "detected" if base["detection"] else "not_detected"
        return base
    if case == "C":
        block = observations["panel"]
        text = block["unwrapped"]
        if product == "original":
            ok = (CATEGORY in text and EXCEPTION_DETAIL in text
                  and block["max_line_width"] == expected["max_line_width"])
            base["state"] = "original_behaviour" if ok else "original_deviation"
            base["detection"] = False if ok else None
            return base
        if product == "M1":
            base["detection"] = CATEGORY not in text
        elif product == "M2":
            base["detection"] = block["max_line_width"] != expected["max_line_width"]
        elif product == "M3":
            base["detection"] = not (EXCEPTION_DETAIL in text and EXCEPTION_MESSAGE in text)
        base["state"] = "detected" if base["detection"] else "not_detected"
        return base
    base["state"] = "unsupported_case"
    return base


# --------------------------------------------------------------- top level

def adjudicate(rows_doc: dict, results: dict, witness_spec: dict,
               witness_meta: dict | None = None) -> dict:
    """results: {row_key: result}；witness_meta 可提供每行的见证身份核验。"""
    suite_rows = rows_doc["suite_rows"]
    witness_rows = rows_doc["witness_rows"]
    by_key = {(row["case"], row["context"], row["product_variant"],
               row["suite_variant"]): row for row in suite_rows}
    witness_payloads = {}
    for row in witness_rows:
        result = results.get(row["row_key"])
        payload = None
        if result and result.get("status") == "completed":
            payload = result.get("witness") or result.get("payload")
        key = (row["case"], row["context"], row["product_variant"])
        meta = (witness_meta or {}).get(row["row_key"])
        if meta and meta.get("identity_ok") is False:
            payload = None
        witness_payloads[key] = payload

    original_controls = []
    original_state = {}
    for row in witness_rows:
        if row.get("product_variant") != "original":
            continue
        key = (row["case"], row["context"])
        verdict = witness_verdict(row["case"], row["context"], "original",
                                  witness_payloads.get((row["case"], row["context"], "original")),
                                  witness_spec)
        original_controls.append(verdict)
        original_state[key] = verdict.get("state")

    controls = []
    control_by_key = {}
    for row in suite_rows:
        if row["suite_variant"] == "S0_original" or row.get("product_variant") != "original":
            continue
        baseline_row = by_key.get((row["case"], row["context"], "original", "S0_original"))
        baseline = results.get(baseline_row["row_key"]) if baseline_row else None
        control = control_cell(row, results.get(row["row_key"]), baseline)
        controls.append(control)
        control_by_key[(row["case"], row["context"], row["suite_variant"])] = control

    cells = []
    s0_cells = []
    for row in suite_rows:
        if row["suite_variant"] == "S0_original":
            if row.get("product_variant") == "original":
                continue
            baseline_row = by_key.get((row["case"], row["context"], "original", "S0_original"))
            baseline = results.get(baseline_row["row_key"]) if baseline_row else None
            s0_cell = suite_cell(row, results.get(row["row_key"]), baseline)
            s0_cell["verdict"] = ("S0_REFERENCE" if s0_cell["verdict"] != "TECHNICAL_FAILURE"
                                  else "TECHNICAL_FAILURE")
            s0_cells.append(s0_cell)
            continue
        if row.get("product_variant") == "original":
            continue
        baseline_row = by_key.get((row["case"], row["context"], "original", "S0_original"))
        baseline = results.get(baseline_row["row_key"]) if baseline_row else None
        cell = suite_cell(row, results.get(row["row_key"]), baseline)
        s0_row = by_key.get((row["case"], row["context"], row["product_variant"], "S0_original"))
        s0_cell = suite_cell(s0_row, results.get(s0_row["row_key"]), baseline) if s0_row else None
        payload = witness_payloads.get((row["case"], row["context"], row["product_variant"]))
        witness = witness_verdict(row["case"], row["context"], row["product_variant"],
                                  payload, witness_spec)
        if original_state.get((row["case"], row["context"])) != "original_behaviour":
            witness = {"case": row["case"], "context": row["context"],
                       "product_variant": row["product_variant"],
                       "state": "control_broken", "detection": None,
                       "original_control_state": original_state.get((row["case"], row["context"])),
                       "missing_activation_evidence": True}
        control = control_by_key.get((row["case"], row["context"], row["suite_variant"]))
        cells.append(classify(cell, s0_cell, witness, control))

    summary = {}
    for cell in cells:
        summary.setdefault(cell["case"], {}).setdefault(cell["suite_variant"], {})
        bucket = summary[cell["case"]][cell["suite_variant"]]
        bucket[cell["verdict"]] = bucket.get(cell["verdict"], 0) + 1
    equal_pass = equal_pass_comparison(cells + s0_cells, controls)
    return {
        "schema_version": "pyhost-context/S3-adjudication/3.0",
        "cells": cells,
        "s0_reference_cells": s0_cells,
        "controls_original_product": controls,
        "witness_original_controls": original_controls,
        "summary": summary,
        "equal_pass": list(equal_pass),
        "equal_pass_exclusions": equal_pass.exclusions,
        "verdict_vocabulary": list(VERDICTS) + ["S0_REFERENCE", "CONTROL_ORIGINAL_PRODUCT",
                                               "CONTROL_FAILURE"],
        "notes": [
            "健康参照、激活、行为执行与断言执行分开登记；未知保留未知。",
            "缺失或无效健康参照、无关异常、未激活不能变成检出或盲区。",
            "原产品控制出现新增失败时，变异格记为 NOT_EVALUABLE_CONTROL_FAILURE。",
            "equal-pass 只在共同可评价的 S0 与覆盖请求分支的 local 格上比较杀死集合。",
        ],
    }


class Comparison(list):
    """equal-pass 结果列表；附带不入比较的排除原因。"""

    def __init__(self, rows, exclusions):
        super().__init__(rows)
        self.exclusions = exclusions


def equal_pass_comparison(cells: list[dict], controls: list[dict] | None = None):
    """共同可评价格上的杀死集合比较。

    入口过滤：技术失败、缺失健康参照、激活未知、原产品控制失败、restricted 上下文、
    未覆盖请求分支的 local 格全部排除，并保留排除原因。只比较各套件共同登记的
    同一变异集合；无数据、坏控制或无共同变异时不产生科学比较。
    """
    control_ok = {}
    for control in controls or []:
        control_ok[(control["case"], control["context"], control["suite_variant"])] = \
            control.get("verdict") == "CONTROL_ORIGINAL_PRODUCT"
    exclusions = []
    groups = {}
    for cell in cells:
        key = (cell["case"], cell["context"], cell["suite_variant"])
        reason = None
        if cell.get("verdict") == "TECHNICAL_FAILURE":
            reason = "technical_failure"
        elif cell.get("health_reference_usable") is False:
            reason = "missing_health_reference"
        elif "witness_detection" in cell and cell.get("witness_detection") is None:
            reason = "activation_unknown"
        elif control_ok.get(key) is False:
            reason = "control_failure"
        elif cell.get("applicability") == "restricted":
            reason = "context_restricted"
        elif cell.get("applicability") == "local" and not cell.get("covers_requested_branch"):
            reason = "requested_branch_not_covered"
        if reason:
            exclusions.append({"row_key": cell.get("row_key"),
                               "case": cell["case"], "context": cell["context"],
                               "suite_variant": cell["suite_variant"],
                               "mutation_id": cell.get("mutation_id"), "reason": reason})
            continue
        mutation = cell.get("mutation_id") or "S0"
        groups.setdefault((cell["case"], cell["context"]), {}).setdefault(
            cell["suite_variant"], {})[mutation] = bool(cell.get("detection"))
    rows = []
    for (case, context), suites in sorted(groups.items()):
        if len(suites) < 2:
            exclusions.append({"case": case, "context": context,
                               "reason": "fewer_than_two_evaluable_suites"})
            continue
        common = set.intersection(*[set(kills) for kills in suites.values()])
        if not common:
            exclusions.append({"case": case, "context": context,
                               "reason": "no_common_mutations"})
            continue
        kill_sets = {suite: {mutation: kills[mutation] for mutation in sorted(common)}
                     for suite, kills in suites.items()}
        signatures = {json.dumps(kills, sort_keys=True) for kills in kill_sets.values()}
        rows.append({
            "case": case, "context": context,
            "mutation_set": sorted(common),
            "kill_sets": kill_sets,
            "identical": len(signatures) == 1,
            "jointly_evaluable_suites": sorted(kill_sets),
            "control_ok": {suite: control_ok.get((case, context, suite))
                           for suite in sorted(kill_sets)},
            "excluded_suites": sorted(set(suites) - set(kill_sets)),
        })
    return Comparison(rows, exclusions)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", required=True, type=pathlib.Path)
    parser.add_argument("--results", required=True, type=pathlib.Path)
    parser.add_argument("--witness-spec", required=True, type=pathlib.Path)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    args = parser.parse_args()
    rows_doc = json.loads(args.rows.read_text(encoding="utf-8"))
    results = json.loads(args.results.read_text(encoding="utf-8"))
    spec = json.loads(args.witness_spec.read_text(encoding="utf-8"))["spec"]
    document = adjudicate(rows_doc, results, spec)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(json.dumps({"cells": len(document["cells"]),
                      "summary": document["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
