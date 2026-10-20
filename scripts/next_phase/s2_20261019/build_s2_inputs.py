"""ClawsGO Science Agent：构建 S2 五个 E2 信号的恢复复核输入（不执行候选）。

只读 S1b 已签署证据，生成：
- ``S2_UNITS_20261019.json``：五个单位的注册翻转集、失败签名、阶段三态与身份集合；
- ``S2_ROSTER_20261019.json``：五单位名册；
- ``S2_SCHEDULE_20261019.json``：30 行调度（两块，哈希序与严格反序）；
- ``S2_TOOLCHAINS_20261019.json``：三项目原工具链（逐文件哈希）；
- ``S2_RULES_20261019.json``：冻结判定规则与等级登记规则；
- ``S2_READ_CHAINS_20261019.json``：五个信号的读取链静态证据。

不重跑、不修改任何历史记录。
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
PROJECT_ROOT = pathlib.Path(os.environ.get("PYHOST_PROJECT") or NEXT.parents[2]).resolve()
S1B = PROJECT_ROOT / "pyhost-context/extension/next_phase/s1b_20261019"
STATE = PROJECT_ROOT / "pyhost-context/extension/next_phase/state/s1b_20261019"
WS = PROJECT_ROOT

UNIT_ORDER = [("typer", "K07_COLUMNS"), ("typer", "K05_NO_COLOR"),
              ("typer", "K06_TERM"), ("twine", "K06_TERM"),
              ("rich-toolkit", "K07_COLUMNS")]
CONDITIONS = {"K05_NO_COLOR": {"NO_COLOR": "1"},
              "K06_TERM": {"TERM": "dumb"},
              "K07_COLUMNS": {"COLUMNS": "40"}}
SEED = "pyhost-context-S2-v1-20261019"
BATCH = "next-S2-20261019"
BLOCKS = 2


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8")


def sha_file(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canon(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def load_runs():
    ledger = read(STATE / "ledger.json")
    runs = {}
    for record in ledger["records"]:
        path = STATE / record["result"]["path"]
        if sha_file(path) != record["result"]["sha256"]:
            raise SystemExit(f"result_drift:{record['run_id']}")
        runs[record["run_id"]] = {"record": record, "detail": read(path)}
    return ledger, runs


def unit_runs(runs, project, condition, kind):
    if kind == "k":
        return sorted(r for r in runs if runs[r]["record"]["arm"] == "main"
                      and runs[r]["record"]["project"] == project
                      and runs[r]["record"]["condition"] == condition)
    return sorted(r for r in runs if runs[r]["record"]["arm"] == "main"
                  and runs[r]["record"]["project"] == project
                  and runs[r]["record"]["condition"] == "B0")


def build_registered_unit(project, condition, analysis_unit, index, runs):
    k_runs = unit_runs(runs, project, condition, "k")
    b0_runs = unit_runs(runs, project, condition, "b0")
    if len(k_runs) != 5 or len(b0_runs) != 5:
        raise SystemExit(f"slot_count:{project}:{condition}")
    flip_nodes = list(analysis_unit["flip_nodes"])
    b0_vectors = [runs[r]["detail"]["vector"] for r in b0_runs]
    if any(v != b0_vectors[0] for v in b0_vectors[1:]):
        raise SystemExit(f"b0_unstable:{project}:{condition}")
    identity_nodes = sorted(b0_vectors[0])
    baseline_vector = {node: b0_vectors[0][node] for node in identity_nodes}
    baseline_flip = {node: b0_vectors[0][node] for node in flip_nodes}
    signatures = {}
    phases = {}
    for node in flip_nodes:
        signatures[node] = set()
        phases[node] = set()
        for run_id in k_runs:
            detail = runs[run_id]["detail"]
            signatures[node].add(detail["per_test"][node]["failure_signature"])
            phases[node].add(canon(detail["summaries"][node]["phase_outcomes"]))
        if len(signatures[node]) != 1 or len(phases[node]) != 1:
            raise SystemExit(f"signature_unstable:{project}:{condition}:{node}")
    for run_id in k_runs:
        detail = runs[run_id]["detail"]
        if sorted(detail["vector"]) != identity_nodes:
            raise SystemExit(f"k_identity_mismatch:{project}:{condition}:{run_id}")
    registered_signatures = {node: signatures[node].pop() for node in flip_nodes}
    registered_phases = {node: json.loads(phases[node].pop()) for node in flip_nodes}
    return {
        "unit_id": f"{project}/{condition}",
        "project": project,
        "position": analysis_unit_position(project),
        "profile": analysis_unit_profile(project),
        "condition": condition,
        "condition_environment": CONDITIONS[condition],
        "s1b": {
            "analysis_unit_index": index,
            "k_run_ids": k_runs,
            "b0_run_ids": b0_runs,
            "criteria": analysis_unit["criteria"],
            "k_returncodes": analysis_unit["k_returncodes"],
            "k_collected": analysis_unit["k_collected"],
        },
        "registered": {
            "flip_nodes": flip_nodes,
            "flip_node_count": len(flip_nodes),
            "flip_nodes_sha256": sha_text("\n".join(sorted(flip_nodes))),
            "failure_signatures": registered_signatures,
            "failure_signature_sha256": {
                node: sha_text(text) for node, text in registered_signatures.items()},
            "phase_outcomes": registered_phases,
            "identity_nodes": identity_nodes,
            "identity_nodes_count": len(identity_nodes),
            "identity_nodes_sha256": sha_text("\n".join(identity_nodes)),
            "baseline_vector_sha256": sha_text(canon(baseline_vector)),
            "baseline_summary_flip_nodes": baseline_flip,
            "baseline_summary_flip_nodes_sha256": sha_text(canon(baseline_flip)),
        },
    }


PROJ_POS = {"typer": 18, "twine": 7, "rich-toolkit": 4}
PROJ_PROFILE = {"typer": "S1a", "twine": "D-R0", "rich-toolkit": "D-R1"}


def analysis_unit_position(project):
    return PROJ_POS[project]


def analysis_unit_profile(project):
    return PROJ_PROFILE[project]


def build_units():
    analysis = read(STATE / "S1B_ANALYSIS_20261019.json")
    _, runs = load_runs()
    units = []
    by_key = {(u["project"], u["condition"]): (i, u)
              for i, u in enumerate(analysis["units"])}
    for project, condition in UNIT_ORDER:
        index, unit = by_key[(project, condition)]
        if not unit.get("confirmed"):
            raise SystemExit(f"unit_not_confirmed:{project}:{condition}")
        units.append(build_registered_unit(project, condition, unit, index, runs))
    return units, sha_file(STATE / "S1B_ANALYSIS_20261019.json")


def build_schedule(units):
    entries = []
    for block in (1, 2):
        ordered = sorted(units, key=lambda u: sha_text(f"{SEED}|unit|{u['unit_id']}"))
        if block == 2:
            ordered = list(reversed(ordered))
        for unit in ordered:
            for phase, condition in (("A1", "B0"), ("B", unit["condition"]),
                                     ("A2", "B0")):
                label = f"s2|block={block}|{unit['unit_id']}|{phase}"
                run_id = (f"s2-b{block}-p{unit['position']:02d}-"
                          f"{unit['condition']}-{phase}")
                entries.append({
                    "block": block, "phase": phase, "arm": "recovery",
                    "project": unit["project"], "position": unit["position"],
                    "unit_id": unit["unit_id"], "condition": condition,
                    "target_condition": unit["condition"],
                    "label": label,
                    "label_sha256": sha_text(f"{SEED}|{label}"),
                    "run_id": run_id,
                })
    for order, entry in enumerate(entries, 1):
        entry["order"] = order
    schedule = {
        "schema_version": "pyhost-context/S2-schedule/1.0",
        "batch_id": BATCH,
        "seed": SEED,
        "algorithm": ("每块按 SHA-256(seed|unit|unit_id) 十六进制升序排列；"
                      "块二取块一的严格反序；块内固定 A1→B→A2。"),
        "blocks": BLOCKS,
        "total_runs": len(entries),
        "entries": entries,
    }
    schedule["schedule_sha256"] = sha_text(
        json.dumps({k: v for k, v in schedule.items() if k != "schedule_sha256"},
                   sort_keys=True, ensure_ascii=False))
    return schedule


def build_toolchains(units):
    source = read(S1B / "S1B_TOOLCHAINS_20261019.json")
    projects = sorted({u["project"] for u in units})
    selected = {name: source["projects"][name] for name in projects}
    for name, tool in selected.items():
        for item in tool["install_files"]:
            path = pathlib.Path(item["path"])
            full = path if path.is_absolute() else WS / path
            if not full.is_file():
                raise SystemExit(f"install_missing:{name}:{item['filename']}")
            if sha_file(full) != item["sha256"] or full.stat().st_size != item["bytes"]:
                raise SystemExit(f"install_drift:{name}:{item['filename']}")
        src = tool["source"]
        full = pathlib.Path(src["path"])
        full = full if full.is_absolute() else WS / full
        if not full.is_file() or sha_file(full) != src["sha256"]:
            raise SystemExit(f"source_drift:{name}")
    return {
        "schema_version": "pyhost-context/S2-toolchains/1.0",
        "batch_id": BATCH,
        "inherits_from": {
            "path": "pyhost-context/extension/next_phase/s1b_20261019/"
                    "S1B_TOOLCHAINS_20261019.json",
            "sha256": sha_file(S1B / "S1B_TOOLCHAINS_20261019.json"),
        },
        "observer": source["observer"],
        "amended_transition_rule": source["amended_transition_rule"],
        "projects": selected,
    }


def build_rules():
    return {
        "schema_version": "pyhost-context/S2-rules/1.0",
        "batch_id": BATCH,
        "scope": "只复核 S1b 已确认的五个 E2 信号；不新增样本、键值或语义变异。",
        "units": ["/".join(u) for u in UNIT_ORDER],
        "design": {
            "per_unit_blocks": 2,
            "per_block_phases": ["A1", "B", "A2"],
            "references_at": [1, 3],
            "runs_per_unit": 6,
            "total_runs": 30,
            "block_order": "块一哈希序，块二严格反序；块内固定 A1→B→A2。",
            "environment": {
                "A1": "B0 基线（仅目标键未设置/未改变）",
                "B": "目标键按 S1b 注册值设置",
                "A2": "B0 基线，与 A1 相同",
            },
            "file_state": "每轮开始由冻结 sdist 恢复源码树；轮间不共享缓存。",
        },
        "validity_gates": [
            "三轮 session 完整、malformed_jsonl_lines=0、未超时",
            "launch_env 三视图有效、扰动有效、启动转换有效",
            "归档 JSONL 与账本哈希一致、进程组清理完成",
        ],
        "block_criteria": [
            {"id": "C1_validity", "rule": "A1、B、A2 三轮全部通过 validity_gates"},
            {"id": "C0_baseline_reproduction",
             "rule": "A1 的 nodeid→summary 向量哈希与 S1b 注册基线向量哈希相同"},
            {"id": "C2_identity", "rule": "A1 收集节点集合与 S1b 注册身份集合逐节点相同"},
            {"id": "C3_baseline_stability", "rule": "A1 与 A2 的 nodeid→summary 向量完全相同"},
            {"id": "C4_flip_set", "rule": "B 相对 A1 的翻转集合与注册翻转集合完全相同"},
            {"id": "C5_signature", "rule": "每个注册翻转节点的 failure_signature 与注册文本完全相同"},
            {"id": "C6_stage", "rule": "每个注册翻转节点的 setup/call/teardown 阶段三态与注册值完全相同"},
            {"id": "C7_restoration", "rule": "A2 向量等于 A1 向量（A2 无残留差异）"},
        ],
        "block_status": {
            "BLOCK_CONFIRMED": "C1—C7 全部满足",
            "NOT_EVALUABLE_VALIDITY": "C1 失败；不据此块作阴性结论",
            "NOT_EVALUABLE_BASELINE_DRIFT": "C0 失败，A1 未复现 S1b 注册基线",
            "NOT_EVALUABLE_IDENTITY_DRIFT": "C2 失败",
            "NOT_EVALUABLE_BASELINE_INSTABILITY": "C3 失败",
            "NOT_RECOVERED_NO_EFFECT": "C4 失败且 B 与 A1 无任何差异",
            "NOT_RECOVERED_FLIP_SET_DRIFT": "C4 失败，差异集合与注册集合不同",
            "NOT_RECOVERED_SIGNATURE_DRIFT": "C5 失败",
            "NOT_RECOVERED_STAGE_DRIFT": "C6 失败",
            "NOT_RECOVERED_A2_RESIDUAL": "C7 失败",
        },
        "unit_status": {
            "RECOVERED_TWO_BLOCKS": "两块均 BLOCK_CONFIRMED；按 E3/E4 阶梯登记",
            "RECOVERED_ONE_BLOCK": "一块确认、另一块不可评价；按 E3 登记",
            "RECOVERY_INCONSISTENT": "一块确认、另一块有效但未复现；维持 E2 并标注不一致",
            "NOT_RECOVERED": "两块均有效且均未复现；该读数降为 E1",
            "NOT_EVALUABLE": "两块均不可评价；E2 维持原登记，恢复未评价",
        },
        "level_registration": {
            "E4": "两块确认 + 块间调度/范围身份/完整作用链检查全部通过",
            "E3": "至少一块确认且另一块不可评价",
            "E2": "维持原登记（不一致或不可评价）",
            "E1": "两块均有效且均未复现",
            "note": "等级按实际结果机械登记；不因期望达到更高等级而改变判据。",
        },
        "multiple_comparison": "五个单位均预先注册、全部报告；不做选择性省略，不按结果追加单位。",
        "negative_claim_limit": "不可评价块不构成阴性证据；零降级不等于不存在降级。",
        "authorization": {
            "S2_execution": False,
            "note": "本输入包未获执行授权；须 Reviewer 001 签署放行后方可运行。",
        },
    }


def scan_lines(path, needles):
    text = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    found = {}
    for needle in needles:
        hits = [{"line": i + 1, "text": line.strip()}
                for i, line in enumerate(text) if needle in line]
        if not hits:
            raise SystemExit(f"read_chain_needle_missing:{path.name}:{needle}")
        found[needle] = hits
    return found


def build_read_chains():
    venv = STATE / "venvs"
    rich_path = venv / "typer/venv/lib/python3.11/site-packages/rich/console.py"
    typer = STATE / "rounds/typer/src/typer"
    twine = STATE / "rounds/twine/src/twine"
    toolkit = STATE / "rounds/rich-toolkit/src/rich-toolkit"
    rich_lines = scan_lines(rich_path, [
        'columns = self._environ.get("COLUMNS")',
        'self._environ.get("NO_COLOR", "") != ""',
        'term = self._environ.get("TERM", "").strip().lower()',
        '_term = self._environ.get("TERM", "")',
    ])
    chains = {
        "schema_version": "pyhost-context/S2-read-chains/1.0",
        "batch_id": BATCH,
        "scope": ("逐信号记录谁读取变量、何时读取、测试是否显式设置、受影响断言。"
                  "同名环境键不自动等同于同一机制。"),
        "shared_dependency": {
            "name": "rich",
            "version": "15.0.0",
            "console_path": "state/s1b_20261019/venvs/typer/venv/lib/python3.11/"
                            "site-packages/rich/console.py",
            "console_sha256": sha_file(rich_path),
            "read_sites": rich_lines,
            "note": ("三个项目在 S1b 环境中安装同一 rich 15.0.0 wheel；"
                     "读取层是 dependency（rich），不是各项目自身代码。"),
        },
        "signals": [
            {
                "unit_id": "typer/K07_COLUMNS",
                "reader": "rich.console.Console.__init__ / Console.size",
                "read_site": {"file": "rich/console.py",
                              "lines": rich_lines['columns = self._environ.get("COLUMNS")']},
                "when_read": "每次 _get_rich_console() 构造 Console 时（帮助渲染入口）",
                "test_sets_environment": False,
                "test_environment_note": "typer 测试不设置 COLUMNS；CliRunner 继承环境",
                "project_path": "typer/rich_utils.py",
                "project_lines": scan_lines(typer / "typer/rich_utils.py",
                                            ["width=MAX_WIDTH", "MAX_WIDTH = int(_TERMINAL_WIDTH)"]),
                "affected_assertions": [
                    "tests/test_cli/test_help.py::test_typer_run_usage 等 152 个帮助输出断言",
                    "断言比较固定宽度的帮助文本（startswith/等值），宽度 40 触发折行",
                ],
                "mechanism_tag": "representation_only",
            },
            {
                "unit_id": "typer/K05_NO_COLOR",
                "reader": "rich.console.Console.__init__（no_color）",
                "read_site": {"file": "rich/console.py",
                              "lines": rich_lines['self._environ.get("NO_COLOR", "") != ""']},
                "when_read": "测试内 Console(force_terminal=True, width=100) 构造时",
                "test_sets_environment": False,
                "test_environment_note": "测试固定 force_terminal 与宽度，未设置 NO_COLOR",
                "affected_assertions": [
                    "tests/test_tutorial/test_printing/test_tutorial001.py::test_cli",
                    "tests/test_tutorial/test_printing/test_tutorial002.py::test_cli",
                    "normalize_rich_output 以 ANSI 转义为占位符；无颜色时占位符缺失",
                ],
                "mechanism_tag": "representation_only",
            },
            {
                "unit_id": "typer/K06_TERM",
                "reader": "rich.console.Console._detect_color_system / is_dumb_terminal",
                "read_site": {"file": "rich/console.py",
                              "lines": rich_lines['_term = self._environ.get("TERM", "")']},
                "when_read": "Console 构造时颜色系统判定",
                "test_sets_environment": False,
                "test_environment_note": "测试未设置 TERM",
                "affected_assertions": [
                    "tests/test_tutorial/test_printing/test_tutorial001.py::test_cli",
                    "tests/test_tutorial/test_printing/test_tutorial002.py::test_cli",
                    "TERM=dumb → color_system=None，ANSI 占位符缺失（与 NO_COLOR 路径不同）",
                ],
                "mechanism_tag": "representation_only",
            },
            {
                "unit_id": "twine/K06_TERM",
                "reader": "rich.console.Console._detect_color_system（经 twine.cli.configure_output）",
                "read_site": {"file": "rich/console.py",
                              "lines": rich_lines['term = self._environ.get("TERM", "").strip().lower()']},
                "when_read": "twine.cli.configure_output() 调用 rich.reconfigure(force_terminal=True)",
                "test_sets_environment": False,
                "test_environment_note": "测试通过 monkeypatch 设置 argv，未设置 TERM",
                "project_path": "twine/cli.py",
                "project_lines": scan_lines(twine / "twine/cli.py", ["rich.reconfigure("]),
                "affected_assertions": [
                    "tests/test_main.py::test_exception_handling",
                    "tests/test_main.py::test_http_exception_handling[400-Bad Request-Error reason]",
                    "tests/test_main.py::test_http_exception_handling[599-Unknown Status-Another reason]",
                    "断言匹配 RED_ERROR（含 \\x1b[31m 前缀）；TERM=dumb 使 color_system=None",
                ],
                "mechanism_tag": "representation_only",
            },
            {
                "unit_id": "rich-toolkit/K07_COLUMNS",
                "reader": "rich.console.Console.__init__（ToolkitConsole 继承）",
                "read_site": {"file": "rich/console.py",
                              "lines": rich_lines['columns = self._environ.get("COLUMNS")']},
                "when_read": "ToolkitConsole(theme=...) 构造时（每个 RichToolkit 实例）",
                "test_sets_environment": False,
                "test_environment_note": "测试未设置 COLUMNS",
                "project_path": "src/rich_toolkit/styles/tagged.py",
                "project_lines": scan_lines(toolkit / "src/rich_toolkit/styles/tagged.py",
                                            ["Column(width=self.tag_width + 2, no_wrap=True)",
                                             'Column(no_wrap=False, overflow="fold")']),
                "affected_assertions": [
                    "tests/test_json_output.py::test_human_mode_output_callback_can_print_with_toolkit",
                    "TaggedStyle 第二列 overflow=fold；宽度 40 时 URL 被折行，"
                    "\"https://demo.fastapicloud.com\" 不再连续出现",
                ],
                "mechanism_tag": "representation_only",
            },
        ],
        "shared_root_cause": {
            "status": "candidate_shared_dependency",
            "statement": ("五个单位的读取层都落在 rich 15.0.0：COLUMNS/NO_COLOR/TERM 分别进入"
                          "Console 宽度、no_color 与 color_system 判定，再影响各项目测试的"
                          "表示层断言。共享的是依赖与读取层，不是同一个机制："
                          "COLUMNS 改变排版宽度，NO_COLOR 关闭颜色，TERM=dumb 关闭颜色系统。"),
            "not_claimed": [
                "不把同名环境键直接算作同一机制",
                "不据此声称跨项目因果确认",
                "不把 rich 依赖的存在等同于任一具体项目的产品缺陷",
            ],
        },
    }
    return chains


def build():
    units, analysis_sha = build_units()
    schedule = build_schedule(units)
    toolchains = build_toolchains(units)
    rules = build_rules()
    chains = build_read_chains()
    roster = {
        "schema_version": "pyhost-context/S2-roster/1.0",
        "batch_id": BATCH,
        "units": [{
            "unit_id": u["unit_id"], "project": u["project"],
            "position": u["position"], "profile": u["profile"],
            "condition": u["condition"], "flip_node_count": u["registered"]["flip_node_count"],
        } for u in units],
        "total_units": len(units),
        "total_runs": 30,
        "s1b_analysis_sha256": analysis_sha,
        "note": "五个单位来自 S1b 已签署确认集；不新增、不替换、不按结果调整。",
    }
    write(HERE / "S2_UNITS_20261019.json", {
        "schema_version": "pyhost-context/S2-units/1.0",
        "batch_id": BATCH,
        "source_analysis": {"path": "pyhost-context/extension/next_phase/state/"
                                    "s1b_20261019/S1B_ANALYSIS_20261019.json",
                            "sha256": analysis_sha},
        "units": units,
    })
    write(HERE / "S2_ROSTER_20261019.json", roster)
    write(HERE / "S2_SCHEDULE_20261019.json", schedule)
    write(HERE / "S2_TOOLCHAINS_20261019.json", toolchains)
    write(HERE / "S2_RULES_20261019.json", rules)
    write(HERE / "S2_READ_CHAINS_20261019.json", chains)
    for path in sorted(HERE.glob("S2_*.json")):
        print(path.name, path.stat().st_size, sha_file(path)[:12])


if __name__ == "__main__":
    build()
