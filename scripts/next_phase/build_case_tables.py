"""ClawsGO Science Agent：三案例修复表的可执行修订版（REV1）。

只读冻结 sdist，逐条核对补丁前像、计算派生文件哈希、抽取目标断言原文。
不执行候选、不安装、不跑测试。

修正 Reviewer 001 I3 指出的问题：

* rich-rst 四个目标实际在 `tests/test_main.py`；
* B 的读取链改为 stdlib optparse 在模块参数化构造期读 COLUMNS（默认宽度减 2），
  Console 的惰性读取另记；
* A-M3 缩成单一偏差（只反转空串语义）；
* B-M1 不再把背景失败格写成可用；
* C-M2 标为仅见证可检出（目标断言是子串，不随宽度变化）；
* C-M1/C-M3 明确只落在解析分支，导出分支为目标之外的控制；
* A-M2 见证只检查颜色分量，不要求所有 SGR 消失；
* A/B/C/D 按策略 × 产品 × 上下文逐格登记。
"""
import hashlib
import json
import pathlib
import re
import tarfile

NEXT = pathlib.Path(__file__).resolve().parent
EXT = NEXT.parent
ROOT = EXT.parent
OUT = NEXT / "CASE_TABLES_20261018_REV1.json"
ORIGINAL = NEXT / "CASE_TABLES_20261018.json"

SDISTS = {
    "rich-argparse": ROOT / "admission_artifacts" / "rich-argparse" / "rich_argparse-1.8.0.tar.gz",
    "rich-rst": ROOT / "admission_artifacts" / "rich-rst" / "rich_rst-2.2.0.tar.gz",
}
PREFIX = {"rich-argparse": "rich_argparse-1.8.0/", "rich-rst": "rich_rst-2.2.0/"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_member(project, rel):
    with tarfile.open(SDISTS[project]) as tar:
        return tar.extractfile(PREFIX[project] + rel).read()


def lines_of(data):
    return data.decode("utf-8").splitlines(keepends=True)


def apply_hunks(project, rel, hunks):
    """按行号从大到小应用补丁；每处前像必须逐字相符。"""
    original = load_member(project, rel)
    lines = lines_of(original)
    for hunk in sorted(hunks, key=lambda h: h["line"], reverse=True):
        index = hunk["line"] - 1
        before = lines[index].rstrip("\n")
        if before != hunk["before"]:
            raise AssertionError(
                f"patch_anchor_mismatch:{project}:{rel}:{hunk['line']}\n"
                f"expected={hunk['before']!r}\nactual={before!r}")
        indent = before[:len(before) - len(before.lstrip())]
        lines[index] = indent + hunk["after"] + "\n"
    return "".join(lines).encode("utf-8")


def assert_lines(project, rel, function):
    text = load_member(project, rel).decode("utf-8")
    lines = text.splitlines()
    rows, inside = [], False
    for index, line in enumerate(lines, 1):
        if re.match(r"^def %s\(" % re.escape(function), line):
            inside = True
            continue
        if inside and re.match(r"^def ", line):
            break
        if inside and re.match(r"^\s+assert\b", line):
            rows.append({"line": index, "text": line.strip()})
    return rows


def node_function(nodeid):
    return nodeid.split("::", 1)[1].split("[", 1)[0]


A_TARGETS = [
    "tests/test_argparse.py::test_actions_spans_in_usage",
    "tests/test_argparse.py::test_arg_default_spans",
    "tests/test_argparse.py::test_boolean_optional_action_spans",
    "tests/test_argparse.py::test_default_highlights",
    "tests/test_argparse.py::test_generated_usage",
    "tests/test_argparse.py::test_help_with_control_codes",
    "tests/test_argparse.py::test_metavar_spans",
    "tests/test_argparse.py::test_rich_renderables",
    "tests/test_argparse.py::test_subparsers_usage",
    "tests/test_argparse.py::test_text_highlighter",
    "tests/test_argparse.py::test_user_usage[default]",
    "tests/test_argparse.py::test_user_usage[markup]",
]

B_TARGETS = [
    "tests/test_optparse.py::test_generated_usage[indented]",
    "tests/test_optparse.py::test_generated_usage[indented-desc]",
    "tests/test_optparse.py::test_generated_usage[indented-long]",
    "tests/test_optparse.py::test_generated_usage[titled]",
]

B_M3_TARGETS = [f"tests/test_argparse.py::test_help_formatter_args[{s}-70-{m}-{i}]"
                for s in (0, 1) for m in (25, 26, 27) for i in (1, 3)]
B_M3_CONTROLS = [f"tests/test_argparse.py::test_help_formatter_args[{s}-None-{m}-{i}]"
                 for s in (0, 1) for m in (25, 26, 27) for i in (1, 3)]

C_PARSE = ["tests/test_main.py::test_cli_parse_error_handling",
           "tests/test_main.py::test_cli_parse_error_handling_no_debug"]
C_EXPORT = ["tests/test_main.py::test_cli_export_exception_handling",
            "tests/test_main.py::test_cli_export_exception_handling_no_debug"]

CONTEXTS_A = [
    {"id": "A-C1", "environment": {"state": "unset"}, "semantic_branch": "键不存在",
     "original_suite": {"status": "pass_observed", "failed_nodes": 0,
                        "source": "exploration/rich-argparse B0"}},
    {"id": "A-C2", "environment": {"state": "set", "value": "1"},
     "semantic_branch": "非空，原 B",
     "original_suite": {"status": "background_failing_observed", "failed_nodes": 22,
                        "source": "exploration/rich-argparse K05 r1"}},
    {"id": "A-C3", "environment": {"state": "empty", "value": ""},
     "semantic_branch": "空串：按约定不视为禁用",
     "original_suite": {"status": "unmeasured", "failed_nodes": None}},
    {"id": "A-C4", "environment": {"state": "set", "value": "0"},
     "semantic_branch": "非空，与 1 同属禁用分支",
     "original_suite": {"status": "unmeasured_same_branch_as_C2", "failed_nodes": None}},
]

CONTEXTS_BC = [
    {"id": "C1", "environment": {"state": "unset"}, "semantic_branch": "键不存在"},
    {"id": "C2", "environment": {"state": "set", "value": "40"}, "semantic_branch": "原 B"},
    {"id": "C3", "environment": {"state": "set", "value": "80"}, "semantic_branch": "显式 80"},
    {"id": "C4", "environment": {"state": "set", "value": "120"}, "semantic_branch": "显式 120"},
]


def contexts_bc(prefix, c1_status, c2_status):
    rows = []
    for base in CONTEXTS_BC:
        env = base["environment"]
        status = {"C1": c1_status, "C2": c2_status,
                  "C3": {"status": "unmeasured", "failed_nodes": None},
                  "C4": {"status": "unmeasured", "failed_nodes": None}}[base["id"]]
        rows.append({"id": f"{prefix}-{base['id']}", "environment": env,
                     "semantic_branch": base["semantic_branch"],
                     "original_suite": status})
    return rows


def mutation(case, ident, semantic, hunks, target_nodes, assertion_file,
             assertion_function, applicable, activation, suite_detectable,
             witness, controls=(), target_assertions=None):
    project = case["project"]
    patched = {}
    for rel in {h["file"] for h in hunks}:
        data = apply_hunks(project, rel, [h for h in hunks if h["file"] == rel])
        patched[rel] = {"before_sha256": sha(load_member(project, rel)),
                        "after_sha256": sha(data)}
    return {
        "id": ident, "semantic_change": semantic,
        "semantic_distinct": True,
        "patch": {"hunks": [{"file": h["file"], "line": h["line"],
                             "before": h["before"], "after": h["after"]} for h in hunks],
                  "derived_files": patched},
        "target_nodes": target_nodes if suite_detectable else [],
        "affected_nodes": target_nodes,
        "target_assertions": target_assertions or [
            {"file": assertion_file, "function": assertion_function,
             "lines": assert_lines(project, assertion_file, assertion_function)}],
        "control_nodes": list(controls),
        "applicable_contexts": applicable,
        "activation": activation,
        "suite_detectable": suite_detectable,
        "witness": witness,
    }


def build():
    cases = []
    # ---------------- A：rich-argparse / NO_COLOR ----------------
    a_hunks_m1 = [
        {"file": "rich_argparse/_argparse.py", "line": 35,
         "before": '        "argparse.args": "cyan",',
         "after": '"argparse.args": "magenta",'},
        {"file": "rich_argparse/_optparse.py", "line": 23,
         "before": '        "optparse.args": "cyan",',
         "after": '"optparse.args": "magenta",'},
    ]
    a_hunks_m2 = [
        {"file": "rich_argparse/_argparse.py", "line": 111,
         "before": "            self._console = r.Console()",
         "after": "self._console = r.Console(no_color=False)"},
    ]
    a_hunks_m3 = [
        {"file": "rich_argparse/_argparse.py", "line": 8,
         "before": "import re", "after": "import os\nimport re"},
        {"file": "rich_argparse/_argparse.py", "line": 111,
         "before": "            self._console = r.Console()",
         "after": 'self._console = r.Console(no_color=("NO_COLOR" in os.environ))'},
    ]
    cases.append({
        "case_id": "A", "project": "rich-argparse", "version": "1.8.0",
        "condition": "K05_NO_COLOR",
        "reading_layer": "dependency rich 15.0.0",
        "reading_chain": [
            {"step": 1, "symbol": "rich.console.Console.__init__",
             "file": "rich/console.py", "line": 724,
             "expression": "self.no_color = no_color if no_color is not None else "
                           'self._environ.get("NO_COLOR", "") != ""',
             "timing": "Console 构造期（formatter 首次取 console 时）"},
            {"step": 2, "symbol": "rich.console.Console.__init__ FORCE_COLOR",
             "file": "rich/console.py", "line": 964,
             "expression": "FORCE_COLOR 决定强制终端与颜色系统",
             "timing": "同一构造期"},
        ],
        "contexts": CONTEXTS_A,
        "obligations": [
            {"id": "A-O1", "scope": "product",
             "statement": "NO_COLOR 非空时禁用颜色分量（前景/背景色），即使 FORCE_COLOR 已设",
             "source": "rich 15.0.0 console.py:724-729；jcs/no_color 约定"},
            {"id": "A-O2", "scope": "product",
             "statement": "NO_COLOR 为空串时按未设处理，不禁用颜色",
             "source": "rich console.py:728 的非空比较"},
            {"id": "A-O3", "scope": "product",
             "statement": "NO_COLOR 未设时颜色由终端探测与 FORCE_COLOR 决定",
             "source": "rich console.py:950-970"},
            {"id": "A-O4", "scope": "content_level",
             "statement": "颜色启用时各 span 保留配置的颜色角色（args=cyan）",
             "source": "项目 styles 表；内容级而非外部规范"},
        ],
        "mutations": [
            mutation(
                {"project": "rich-argparse"}, "A-M1",
                "颜色角色值改变：argparse.args/optparse.args cyan→magenta",
                a_hunks_m1, A_TARGETS, "tests/test_argparse.py",
                "test_actions_spans_in_usage",
                applicable={"A-C1": "applicable", "A-C2": "background_failing",
                            "A-C3": "applicable_unmeasured", "A-C4": "background_failing"},
                activation={"predicate": "颜色启用且目标断言比较完整渲染文本",
                            "per_context": {"A-C1": "predicted_active",
                                            "A-C3": "predicted_active_unmeasured"}},
                suite_detectable=True,
                witness={"id": "W-A-M1",
                         "probe": "以 force_terminal Console 渲染 RichHelpFormatter 帮助文本，"
                                  "读取 --option span 的前景色 SGR",
                         "expected_original": "38;5;? / 标准青色 36",
                         "expected_mutant": "洋红色 35",
                         "oracle": "颜色分量，不是任意 SGR 的存在"},
                controls=[]),
            mutation(
                {"project": "rich-argparse"}, "A-M2",
                "忽略 NO_COLOR：Console 显式 no_color=False",
                a_hunks_m2, A_TARGETS, "tests/test_argparse.py",
                "test_actions_spans_in_usage",
                applicable={"A-C1": "not_applicable", "A-C2": "background_failing",
                            "A-C3": "not_applicable", "A-C4": "background_failing"},
                activation={"predicate": "NO_COLOR 非空（仅 C2/C4）",
                            "per_context": {"A-C2": "predicted_active",
                                            "A-C4": "predicted_active_same_branch"}},
                suite_detectable=False,
                witness={"id": "W-A-M2",
                         "probe": "在 NO_COLOR=1 下渲染并检查前景/背景色分量是否消失",
                         "expected_original": "无颜色分量（粗体等非颜色 SGR 可保留）",
                         "expected_mutant": "出现颜色分量",
                         "oracle": "只判色分量；不要求所有 SGR 消失"},
                controls=[]),
            mutation(
                {"project": "rich-argparse"}, "A-M3",
                "空串语义反转：仅当 NO_COLOR 存在且为空串时禁用颜色；"
                "未设与非空值保持原语义",
                a_hunks_m3, A_TARGETS, "tests/test_argparse.py",
                "test_actions_spans_in_usage",
                applicable={"A-C1": "control_unchanged", "A-C2": "background_failing",
                            "A-C3": "applicable", "A-C4": "background_failing"},
                activation={"predicate": "NO_COLOR 恰为空串（仅 C3）",
                            "per_context": {"A-C3": "predicted_active_unmeasured"}},
                suite_detectable=True,
                witness={"id": "W-A-M3",
                         "probe": '在 NO_COLOR="" 下渲染，比较原产品与变异体的色分量',
                         "expected_original": "有颜色分量",
                         "expected_mutant": "无颜色分量",
                         "oracle": "空串与未设分别保存，不把字面 'unset' 传入环境"},
                controls=[]),
        ],
        "controls": {"positive": "原产品在 A-C1 通过（B0 实跑 0 失败）；A-C3 待测",
                     "negative": "A-C2 原套件已有 22 个 call 期失败，不得记作变异检出"},
    })

    # ---------------- B：rich-argparse / COLUMNS ----------------
    b_hunks_m1 = [
        {"file": "rich_argparse/_optparse.py", "line": 60,
         "before": "        super().__init__(indent_increment, max_help_position, width, short_first)",
         "after": "super().__init__(indent_increment, max_help_position, 80, short_first)"},
    ]
    b_hunks_m2 = [
        {"file": "rich_argparse/_optparse.py", "line": 60,
         "before": "        super().__init__(indent_increment, max_help_position, width, short_first)",
         "after": "super().__init__(indent_increment, max_help_position, "
                  "(width - 10 if width is not None else None), short_first)"},
    ]
    b_hunks_m3 = [
        {"file": "rich_argparse/_argparse.py", "line": 91,
         "before": "        super().__init__(prog, indent_increment, max_help_position, width, **kwargs)",
         "after": "super().__init__(prog, indent_increment, max_help_position, None, **kwargs)"},
    ]
    cases.append({
        "case_id": "B", "project": "rich-argparse", "version": "1.8.0",
        "condition": "K07_COLUMNS",
        "reading_layer": "stdlib optparse（项目 formatter 继承）",
        "reading_chain": [
            {"step": 1, "symbol": "optparse.HelpFormatter.__init__",
             "file": "optparse.py", "line": 190,
             "expression": "width = int(_os.environ['COLUMNS']); width -= 2",
             "timing": "test_optparse 模块级参数化构造 formatter 时（收集期，先于会话 fixture）"},
            {"step": 2, "symbol": "rich_argparse._optparse.RichHelpFormatter",
             "file": "rich_argparse/_optparse.py", "line": 60,
             "expression": "super().__init__(indent_increment, max_help_position, width, short_first)",
             "timing": "同上，self.width 固定为 COLUMNS-2"},
            {"step": 3, "symbol": "rich.console.Console.width",
             "file": "rich_argparse/_optparse.py", "line": 67,
             "expression": "self._console = r.Console(theme=r.Theme(self.styles))",
             "timing": "惰性构造，首次渲染时；conftest 会话 fixture 已把 COLUMNS 设为 100"},
            {"step": 4, "symbol": "test_generated_usage 断言",
             "file": "tests/test_optparse.py", "line": 437,
             "expression": "assert parser.format_help() == dedent(expected)",
             "timing": "call 期"},
        ],
        "known_values": {
            "collection_env_columns": {"C1": "unset -> 80", "C2": "40", "C3": "80", "C4": "120"},
            "formatter_width": {"C1": 78, "C2": 38, "C3": 78, "C4": 118},
            "session_fixture_sets_columns": 100,
            "console_width_at_call": 100,
            "note": "启动上下文只决定收集期 formatter.width；渲染期 Console 宽度由 conftest 覆盖为 100。"
                    "B-M1/B-M2 改的是 formatter.width 传播，不是替代旧机制。",
        },
        "contexts": contexts_bc(
            "B",
            {"status": "pass_observed", "failed_nodes": 0,
             "source": "exploration/rich-argparse B0"},
            {"status": "background_failing_observed", "failed_nodes": 4,
             "source": "exploration/rich-argparse K07 r1"}),
        "obligations": [
            {"id": "B-O1", "scope": "product",
             "statement": "未显式指定 width 时，formatter 宽度取自构造时 COLUMNS-2",
             "source": "CPython optparse.HelpFormatter.__init__；rich console.py:685 为另一读取点"},
            {"id": "B-O2", "scope": "product",
             "statement": "显式 width 参数覆盖环境值",
             "source": "argparse.HelpFormatter 契约；test_help_formatter_args 参数化 (None,70)"},
            {"id": "B-O3", "scope": "content_level",
             "statement": "usage 与帮助正文在同一有效宽度下折行",
             "source": "项目实现一致性；内容级"},
        ],
        "mutations": [
            mutation(
                {"project": "rich-argparse"}, "B-M1",
                "formatter 宽度忽略环境：stdlib 初始化固定 80",
                b_hunks_m1, B_TARGETS, "tests/test_optparse.py", "test_generated_usage",
                applicable={"B-C1": "applicable_unverified_activation",
                            "B-C2": "background_failing",
                            "B-C3": "applicable_unverified_activation",
                            "B-C4": "applicable_predicted_activation"},
                activation={"predicate": "formatter.width 与 80 的差改变了 usage/help 折行",
                            "per_context": {"B-C1": "delta_2_unverified",
                                            "B-C3": "delta_2_unverified",
                                            "B-C4": "delta_38_predicted_active"}},
                suite_detectable=True,
                witness={"id": "W-B-M1",
                         "probe": "按 COLUMNS=40/80/120 构造 formatter 并格式化长帮助，比较 max 行宽",
                         "expected_original": "行宽随 COLUMNS 变化（COLUMNS-2）",
                         "expected_mutant": "行宽固定为 80",
                         "oracle": "formatter.width 与输出折行"},
                controls=[]),
            mutation(
                {"project": "rich-argparse"}, "B-M2",
                "宽度传播错误：formatter 使用 COLUMNS-10",
                b_hunks_m2, B_TARGETS, "tests/test_optparse.py", "test_generated_usage",
                applicable={"B-C1": "not_activated_default_width",
                            "B-C2": "background_failing",
                            "B-C3": "not_activated_default_width",
                            "B-C4": "not_activated_default_width"},
                activation={"predicate": "width 非 None 时减 10；目标构造器传 None，故不激活",
                            "per_context": {"B-C1": "inactive_width_none",
                                            "B-C3": "inactive_width_none",
                                            "B-C4": "inactive_width_none"},
                            "note": "四个 test_generated_usage 的模块级构造器不传 width；"
                                    "改启动 COLUMNS 不会让该参数从 None 变成非 None。"
                                    "保留原变异记录，不换成易检出者。"},
                suite_detectable=False,
                witness={"id": "W-B-M2",
                         "probe": "记录目标构造器的 width 实参与变异分支；见证不主张检出",
                         "expected_original": "width=None",
                         "expected_mutant": "width=None，仍走原语义",
                         "oracle": "未激活；正控制不足，不计入 C"},
                controls=[]),
            mutation(
                {"project": "rich-argparse"}, "B-M3",
                "显式 width 被忽略：argparse formatter 总传 None",
                b_hunks_m3, B_M3_TARGETS, "tests/test_argparse.py", "test_help_formatter_args",
                applicable={"B-C1": "applicable_context_invariant",
                            "B-C2": "applicable_context_invariant",
                            "B-C3": "applicable_context_invariant",
                            "B-C4": "applicable_context_invariant"},
                activation={"predicate": "显式 width=70 被忽略，改由 COLUMNS 决定",
                            "per_context": {"B-C1": "active_after_session_fixture",
                                            "B-C2": "active_after_session_fixture",
                                            "B-C3": "active_after_session_fixture",
                                            "B-C4": "active_after_session_fixture"},
                            "note": "会话 fixture 把 COLUMNS 固定为 100，故四个启动上下文的观测相同；"
                                    "本变异不是上下文探针"},
                suite_detectable=True,
                witness={"id": "W-B-M3",
                         "probe": "以 width=20 构造 formatter，检查输出行宽",
                         "expected_original": "行宽约 20",
                         "expected_mutant": "行宽由 COLUMNS 决定",
                         "oracle": "显式参数覆盖环境"},
                controls=B_M3_CONTROLS),
        ],
        "controls": {"positive": "原产品在 B-C1 通过；B-C2 有 4 个背景失败",
                     "negative": "背景失败格不参与 C"},
    })

    # ---------------- C：rich-rst / COLUMNS ----------------
    c_hunks_m1 = [
        {"file": "rich_rst/__main__.py", "line": 310,
         "before": "                    f'[bold red]Error parsing reStructuredText[/bold red]\\n\\n'",
         "after": "                    '[bold red]Diagnostic[/bold red]\\n\\n'"},
    ]
    c_hunks_m2 = [
        {"file": "rich_rst/__main__.py", "line": 266,
         "before": "    console = Console(force_terminal=args.force_color, width=args.width, record=record)",
         "after": "    console = Console(force_terminal=args.force_color, "
                  "width=(args.width or 80), record=record)"},
    ]
    c_hunks_m3 = [
        {"file": "rich_rst/__main__.py", "line": 311,
         "before": "                    f'[yellow]{type(e).__name__}[/yellow]: {e}'",
         "after": "                    '[yellow]Exception[/yellow]: error'"},
    ]
    cases.append({
        "case_id": "C", "project": "rich-rst", "version": "2.2.0",
        "condition": "K07_COLUMNS",
        "reading_layer": "dependency rich 15.0.0（产品调用点 rich_rst.__main__:266）",
        "reading_chain": [
            {"step": 1, "symbol": "rich_rst.__main__.main Console 构造",
             "file": "rich_rst/__main__.py", "line": 266,
             "expression": "Console(force_terminal=args.force_color, width=args.width, record=record)",
             "timing": "call 期（CLI 入口），width 为 None 时读 COLUMNS"},
            {"step": 2, "symbol": "rich.console.Console.__init__",
             "file": "rich/console.py", "line": 685,
             "expression": 'columns = self._environ.get("COLUMNS")',
             "timing": "同一构造期"},
        ],
        "contexts": contexts_bc(
            "C",
            {"status": "pass_observed", "failed_nodes": 0,
             "source": "validation_7/rich-rst B0"},
            {"status": "background_failing_observed", "failed_nodes": 4,
             "source": "validation_7/rich-rst K07 r1"}),
        "obligations": [
            {"id": "C-O1", "scope": "content_level",
             "statement": "诊断面板保留类别文本（Error parsing reStructuredText / Error during export）"
                          "与异常细节（类型名与消息）的可检测子串",
             "source": "tests/test_main.py:200,217,239,259 的既有断言"},
            {"id": "C-O2", "scope": "product",
             "statement": "未给 --width 时输出宽度取自 COLUMNS",
             "source": "rich console.py:685；CLI --width 帮助文本"},
        ],
        "mutations": [
            mutation(
                {"project": "rich-rst"}, "C-M1",
                "解析分支类别文本缺失（单点，仅解析分支）",
                c_hunks_m1, C_PARSE, "tests/test_main.py", "test_cli_parse_error_handling",
                applicable={"C-C1": "applicable", "C-C2": "background_failing",
                            "C-C3": "applicable_unmeasured", "C-C4": "applicable_unmeasured"},
                activation={"predicate": "解析错误路径被触发",
                            "per_context": {"C-C1": "predicted_active",
                                            "C-C3": "predicted_active",
                                            "C-C4": "predicted_active"}},
                suite_detectable=True,
                witness={"id": "W-C-M1",
                         "probe": "构造解析失败输入，检查面板文本是否含类别短语",
                         "expected_original": "含 'Error parsing reStructuredText'",
                         "expected_mutant": "不含该短语",
                         "oracle": "内容级子串"},
                controls=C_EXPORT),
            mutation(
                {"project": "rich-rst"}, "C-M2",
                "忽略 COLUMNS：Console 固定 width=80",
                c_hunks_m2, C_PARSE + C_EXPORT, "tests/test_main.py",
                "test_cli_parse_error_handling",
                applicable={"C-C1": "witness_only", "C-C2": "background_failing",
                            "C-C3": "witness_only_inactive", "C-C4": "witness_only_unverified"},
                activation={"predicate": "Console 实际宽度与 COLUMNS 不同且输出行超过较窄宽度",
                            "per_context": {"C-C1": "inactive_fallback_80",
                                            "C-C3": "inactive_width_equal_80",
                                            "C-C4": "unverified_wrap_dependent"}},
                suite_detectable=False,
                witness={"id": "W-C-M2",
                         "probe": "在 COLUMNS=40/80/120 下运行 CLI 失败路径，比较面板行宽",
                         "expected_original": "面板宽度随 COLUMNS 变化",
                         "expected_mutant": "面板宽度固定 80",
                         "oracle": "面板行宽；仅在本注册的四个目标断言与未变异原产品控制下，"
                                   "目标子串短于面板宽度、宽度变化不改变这些断言；"
                                   "不外推到一般排版或连续子串义务"},
                controls=[]),
            mutation(
                {"project": "rich-rst"}, "C-M3",
                "解析分支异常细节缺失（单点，仅解析分支）",
                c_hunks_m3, C_PARSE, "tests/test_main.py", "test_cli_parse_error_handling",
                applicable={"C-C1": "applicable", "C-C2": "background_failing",
                            "C-C3": "applicable_unmeasured", "C-C4": "applicable_unmeasured"},
                activation={"predicate": "解析错误路径被触发",
                            "per_context": {"C-C1": "predicted_active",
                                            "C-C3": "predicted_active",
                                            "C-C4": "predicted_active"}},
                suite_detectable=True,
                witness={"id": "W-C-M3",
                         "probe": "构造解析失败输入，检查面板是否含异常类型名与消息",
                         "expected_original": "含 ValueError 与 Fake parse failure",
                         "expected_mutant": "不含",
                         "oracle": "内容级子串；补丁按登记行号单点替换，导出分支保持不变"},
                controls=C_EXPORT),
        ],
        "controls": {"positive": "原产品在 C-C1 通过；C-C2 有 4 个背景失败",
                     "negative": "导出分支节点作为解析分支变异的目标外控制"},
    })

    for case in cases:
        case["cell_matrix"] = {
            "suites": ["S0_original", "R1_global_fix", "R2_local_isolation",
                       "R3_explicit_parameterization"],
            "products": ["original"] + [mutation["id"] for mutation in case["mutations"]],
            "contexts": [context["id"] for context in case["contexts"]],
            "per_cell_fields": ["returncode", "collected", "target_node_outcomes",
                                "non_target_failures", "activation_observed",
                                "s0_status", "target_executed", "mutation_killed",
                                "counted_in_C", "unknown_reason"],
        }
    document = {
        "schema_version": "pyhost-context/next-case-tables/2.1",
        "supersedes": {"path": "extension/next_phase/CASE_TABLES_20261018.json",
                       "sha256": sha(ORIGINAL.read_bytes())},
        "written_at_utc": "2026-10-18T00:00:00Z",
        "authority": "Reviewer 001 S0 审核 I3；旧 E4/E3 与限定修复签认不变",
        "changes": [
            "rich-rst 四个目标改为 tests/test_main.py 的四个函数（含精确 nodeid）",
            "B 案例读取链改为 stdlib optparse 在模块参数化构造期读 COLUMNS，Console 惰性读取另记",
            "A-M3 缩成单一偏差：仅空串分支反转（get(..., 'x') == ''）",
            "B-M1 不再把背景失败格列为可用；可用性按 S0 实跑 + 变异激活双重条件判定",
            "C-M2 标为仅见证可检出：目标断言是子串，不随宽度变化；80 列明确为未激活",
            "C-M1/C-M3 只落在解析分支，导出分支作为控制；补丁按行号单点替换",
            "A-M2 见证只检查颜色分量，粗体等非颜色 SGR 可保留",
            "A/B/C/D 改为按策略 × 产品 × 上下文逐格登记",
        ],
        "evaluation_rule": {
            "A": "适用上下文中，原产品的目标失败是否消失",
            "B": "有依据的具体义务是否保留；unknown 与内容级限定照录",
            "C_denominator": "由事前适用性、独立激活与有效原产品控制决定："
                             "S0 原产品在该格通过 ∧ 变异在该格确实激活 ∧ 目标断言在该格被执行"
                             "（target_executed）；mutation_killed 只作分子，"
                             "未杀死但满足前三者的格保留在分母",
            "C_numerator": "目标断言失败，且失败阶段与签名与登记一致（mutation_killed）",
            "D": "实际建立并断言的输入上下文与语义分支；见证发现不计入套件检错能力",
            "all_cells_reported": "所有计划格及 background_failing/not_activated/"
                                  "activation_unverified/unknown 一并报告",
        },
        "cell_matrix_contract": {
            "suites": ["S0_original", "R1_global_fix", "R2_local_isolation",
                       "R3_explicit_parameterization"],
            "products_rule": "每个案例为 ['original'] + 本案例变异 ID；不得引用其他案例的变异",
            "contexts": "本案例四上下文",
            "per_cell_fields": ["returncode", "collected", "target_node_outcomes",
                                "non_target_failures", "activation_observed",
                                "s0_status", "counted_in_C", "unknown_reason"],
            "note": "每案例 4 套件 × 4 产品 × 4 上下文；没有变异的格子记 not_applicable，"
                    "不伪造补足。",
        },
        "cases": cases,
        "budget_reference": {
            "repair_suites_maximum": 384,
            "repair_witnesses_maximum": 96,
            "formula": "6 案例 × 4 套件 × 4 产品 × 4 上下文；见证 6 × 4 × 4",
        },
        "candidate_tests_or_installs": 0,
    }
    OUT.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "cases": len(cases),
        "mutations": sum(len(c["mutations"]) for c in cases),
        "patches_verified": sum(len(m["patch"]["hunks"])
                                for c in cases for m in c["mutations"]),
        "targets": {c["case_id"]: [len(m["target_nodes"]) for m in c["mutations"]]
                    for c in cases},
        "sha256": sha(OUT.read_bytes()),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
