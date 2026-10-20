"""ClawsGO Science Agent：三案例修复的**独立行为见证**（一次进程一个上下文）。

用法::

    python3 repair_witness.py --case A --mutation A-M1 --context A-C1 --product-root DIR

`--product-root` 指向派生源码树（原产品或其变异体）的根目录；本脚本只导入该树、
不联网、不写候选源码。上下文在导入任何产品模块之前写入环境，因此构造期读取看到的
就是指定上下文。

输出一行 JSON：观测值 + `expected`（来自案例表），由分析器比较，不在本脚本内判定通过。
"""
import argparse
import contextlib
import io
import json
import os
import pathlib
import re
import sys

SGR = re.compile(r"\x1b\[([0-9;]*)m")
BASIC_FOREGROUND = set(range(30, 38)) | set(range(90, 98))
BASIC_BACKGROUND = set(range(40, 48)) | set(range(100, 108))


def apply_context(context):
    spec = CONTEXT_SPECS[context]
    key = spec["key"]
    if spec["state"] == "unset":
        os.environ.pop(key, None)
    elif spec["state"] == "empty":
        os.environ[key] = ""
    else:
        os.environ[key] = spec["value"]
    if key == "NO_COLOR":
        os.environ["FORCE_COLOR"] = "1"


CONTEXT_SPECS = {
    "A-C1": {"key": "NO_COLOR", "state": "unset"},
    "A-C2": {"key": "NO_COLOR", "state": "set", "value": "1"},
    "A-C3": {"key": "NO_COLOR", "state": "empty"},
    "A-C4": {"key": "NO_COLOR", "state": "set", "value": "0"},
    "B-C1": {"key": "COLUMNS", "state": "unset"},
    "B-C2": {"key": "COLUMNS", "state": "set", "value": "40"},
    "B-C3": {"key": "COLUMNS", "state": "set", "value": "80"},
    "B-C4": {"key": "COLUMNS", "state": "set", "value": "120"},
    "C-C1": {"key": "COLUMNS", "state": "unset"},
    "C-C2": {"key": "COLUMNS", "state": "set", "value": "40"},
    "C-C3": {"key": "COLUMNS", "state": "set", "value": "80"},
    "C-C4": {"key": "COLUMNS", "state": "set", "value": "120"},
}


def sgr_components(text):
    """按 SGR 语法解析：38/48 的扩展参数是操作数，不是独立颜色码。"""
    basic, extended, non_color = [], [], []
    for match in SGR.finditer(text):
        params = [int(part) for part in match.group(1).split(";") if part.isdigit()]
        index = 0
        while index < len(params):
            code = params[index]
            if code in (38, 48):
                if index + 1 >= len(params):
                    non_color.append(code)
                    break
                mode = params[index + 1]
                if mode == 5 and index + 2 < len(params):
                    extended.append(";".join(str(p) for p in params[index:index + 3]))
                    index += 3
                    continue
                if mode == 2 and index + 4 < len(params):
                    extended.append(";".join(str(p) for p in params[index:index + 5]))
                    index += 5
                    continue
                non_color.append(code)
                index += 1
                continue
            if code in BASIC_FOREGROUND or code in BASIC_BACKGROUND:
                basic.append(code)
            elif code != 0:
                non_color.append(code)
            index += 1
    return {"basic_color_codes": sorted(set(basic)),
            "extended_color_sequences": extended,
            "non_color_sgr": sorted(set(non_color))}


def color_profile(text):
    components = sgr_components(text)
    return {**components,
            "has_color_components": bool(components["basic_color_codes"]
                                         or components["extended_color_sequences"])}


def probe_a(product_root):
    sys.path.insert(0, str(product_root))
    import argparse
    from rich_argparse import RichHelpFormatter

    parser = argparse.ArgumentParser(prog="PROG", formatter_class=RichHelpFormatter)
    parser.add_argument("--foo", help="foo help")
    parser.add_argument("--bar", help="bar help")
    text = parser.format_help()
    return {"help_text": text, **color_profile(text)}


def probe_b_usage(product_root):
    sys.path.insert(0, str(product_root))
    from rich_argparse import IndentedRichHelpFormatter
    from optparse import OptionParser

    formatter = IndentedRichHelpFormatter()
    parser = OptionParser(prog="PROG", formatter=formatter, usage="%prog [options]")
    parser.add_option("--foo", help="foo help " * 6)
    text = parser.format_help()
    widths = [len(line) for line in text.splitlines()]
    return {"formatter_width": getattr(formatter, "_width", None),
            "constructor_width_argument": None,
            "max_line_width": max(widths) if widths else 0,
            "help_text": text}


def probe_b_explicit(product_root):
    sys.path.insert(0, str(product_root))
    import argparse
    from argparse import HelpFormatter
    from rich_argparse import RichHelpFormatter

    def build(formatter_class):
        parser = argparse.ArgumentParser(prog="program", formatter_class=formatter_class)
        parser.add_argument("option-of-certain-length",
                            help="This is the help of the said option")
        return parser

    standard = build(lambda prog: HelpFormatter(prog, 2, 24, 70))
    rich = build(lambda prog: RichHelpFormatter(prog, 2, 24, 70))
    expected = standard.format_help()
    actual = rich.format_help()
    return {"stdlib_width": getattr(standard._get_formatter(), "_width", None),
            "rich_width": getattr(rich._get_formatter(), "_width", None),
            "equal": expected == actual,
            "rich_max_line_width": max((len(l) for l in actual.splitlines()), default=0)}


def probe_c(product_root):
    sys.path.insert(0, str(product_root))
    import tempfile

    captured = io.StringIO()
    argv = sys.argv
    try:
        with tempfile.TemporaryDirectory() as tmp:
            sample = pathlib.Path(tmp) / "input.rst"
            sample.write_text("Some RST.", encoding="utf-8")
            sys.argv = ["rich-rst", str(sample)]
            import rich_rst
            import rich_rst.__main__ as cli

            def boom(self, *args, **kwargs):
                raise ValueError("Fake parse failure")

            rich_rst.RestructuredText.__init__ = boom
            with contextlib.redirect_stdout(captured):
                exit_code = cli.main()
    finally:
        sys.argv = argv
    text = captured.getvalue()
    widths = [len(line) for line in text.splitlines()]
    return {"exit_code": exit_code,
            "input_file_created": True,
            "contains_category": "Error parsing reStructuredText" in text,
            "contains_detail": "Fake parse failure" in text,
            "max_line_width": max(widths) if widths else 0,
            "output": text}


PROBES = {
    ("A", "A-M1"): probe_a,
    ("A", "A-M2"): probe_a,
    ("A", "A-M3"): probe_a,
    ("B", "B-M1"): probe_b_usage,
    ("B", "B-M2"): probe_b_usage,
    ("B", "B-M3"): probe_b_explicit,
    ("C", "C-M1"): probe_c,
    ("C", "C-M2"): probe_c,
    ("C", "C-M3"): probe_c,
}


EXPECTED = {
    "A-M1": {"original": "color_codes 含 36 且不含 35；has_color_components 依上下文",
             "mutant": "color_codes 含 35 且不含 36"},
    "A-M2": {"original": "NO_COLOR 非空时 has_color_components=false；粗体等非颜色 SGR 可保留",
             "mutant": "has_color_components=true"},
    "A-M3": {"original": 'NO_COLOR="" 时 has_color_components=true',
             "mutant": "has_color_components=false"},
    "B-M1": {"original": "formatter_width = COLUMNS-2（未设时 78）",
             "mutant": "formatter_width = 80"},
    "B-M2": {"original": "formatter_width = COLUMNS-2",
             "mutant": "formatter_width = COLUMNS-10（未设时仍为 None）"},
    "B-M3": {"original": "rich_width=70 且 equal=true",
             "mutant": "rich_width 由 COLUMNS 决定；equal=false"},
    "C-M1": {"original": "contains_category=true", "mutant": "contains_category=false"},
    "C-M2": {"original": "max_line_width 随 COLUMNS 变化", "mutant": "max_line_width 固定不超过 80"},
    "C-M3": {"original": "contains_detail=true", "mutant": "contains_detail=false"},
}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True, choices=["A", "B", "C"])
    parser.add_argument("--mutation", required=True)
    parser.add_argument("--context", required=True, choices=sorted(CONTEXT_SPECS))
    parser.add_argument("--product-root", required=True)
    args = parser.parse_args(argv)
    probe = PROBES.get((args.case, args.mutation))
    if probe is None:
        parser.error("unknown case/mutation")
    if not args.context.startswith(args.case + "-"):
        parser.error("context does not belong to case")
    apply_context(args.context)
    try:
        observed = probe(pathlib.Path(args.product_root))
        status = "observed"
    except Exception as exc:  # 见证失败照实保留
        observed = {"error": f"{type(exc).__name__}: {exc}"}
        status = "witness_error"
    print(json.dumps({"case": args.case, "mutation": args.mutation,
                      "context": args.context, "status": status,
                      "observed": observed, "expected": EXPECTED[args.mutation],
                      "env": {CONTEXT_SPECS[args.context]["key"]: os.environ.get(
                          CONTEXT_SPECS[args.context]["key"])}},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
