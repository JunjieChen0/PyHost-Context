"""ClawsGO Science Agent：S3 独立见证探针（在候选环境内运行，输出 JSON）。

用法（由 s3_adapter.py 调用，单独运行不改变任何文件）：

    python witness_probes.py --case A --product-root . --json-out /path/witness.json

约束：

* 只读取 ``--product-root`` 下的产品代码；导入后核对模块文件确实位于该根下。
* 每个探针独立捕获异常，异常写入 ``errors``，不影响其它探针。
* 探针**不运行项目测试套件**，也不修改产品文件；它只调用公开入口并记录观测。
* 观测是原始值；"是否检出某个变异"由 ``s3_adjudicate.py`` 按事前规则判定，
  不在本文件中预先下结论。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import pathlib
import platform
import re
import sys
import tempfile

SGR = re.compile(r"\x1b\[([0-9;]*)m")
BORDER = "│─┌┐└┘├┤┬┴┼╭╮╰╯"
COLOUR_PARAMS = (
    set(range(30, 38)) | set(range(40, 48)) | set(range(90, 98))
    | set(range(100, 108)) | {38, 48}
)


def sgr_parameters(text: str) -> list[list[int]]:
    out = []
    for match in SGR.finditer(text):
        values = [int(part) for part in match.group(1).split(";") if part != ""]
        out.append(values or [0])
    return out


def has_colour_component(text: str) -> bool:
    for parameters in sgr_parameters(text):
        index = 0
        while index < len(parameters):
            code = parameters[index]
            if code in (38, 48):
                return True
            if code in COLOUR_PARAMS:
                return True
            index += 1
    return False


def last_sgr_before(text: str, token: str) -> str | None:
    index = text.find(token)
    if index < 0:
        return None
    matches = [m for m in SGR.finditer(text, 0, index)]
    return matches[-1].group(1) if matches else None


def unwrap(text: str) -> str:
    """去掉面板边框并折叠空白；折行不改变可见文案。"""
    for char in BORDER:
        text = text.replace(char, " ")
    return " ".join(text.split())


def import_provenance(root: pathlib.Path, module_name: str) -> dict | None:
    """核验模块文件确实位于产品根之下；不在根内返回 ``None``（拒绝），不抛给调用方。"""
    module = __import__(module_name.split(".")[0])
    for part in module_name.split(".")[1:]:
        module = getattr(module, part)
    file = getattr(module, "__file__", None)
    if not file:
        return None
    path = pathlib.Path(file).resolve()
    root = pathlib.Path(root).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if path == root:
        return None
    return {"module": module_name, "file": str(path), "root": str(root)}


def require_provenance(root: pathlib.Path, module_name: str) -> dict:
    provenance = import_provenance(root, module_name)
    if provenance is None:
        raise RuntimeError(f"import_outside_product_root:{module_name}")
    return provenance


# ---------------------------------------------------------------- A: colour

def build_argparse_help() -> str:
    import argparse

    from rich_argparse import RichHelpFormatter

    parser = argparse.ArgumentParser(prog="PROG", formatter_class=RichHelpFormatter)
    parser.add_argument("--option", help="An option")
    parser.add_argument("file")
    return parser.format_help()


def probe_a_colour(root: pathlib.Path) -> dict:
    provenance = require_provenance(root, "rich_argparse")
    text = build_argparse_help()
    return {
        "provenance": provenance,
        "colour_present": has_colour_component(text),
        "argparse_args_sgr": last_sgr_before(text, "--option"),
        "sgr_sequence": [";".join(map(str, p)) for p in sgr_parameters(text)][:24],
        "rendered_length": len(text),
    }


# ------------------------------------------------------------ B: formatter width

def probe_b_width(root: pathlib.Path) -> dict:
    provenance = require_provenance(root, "rich_argparse")
    from optparse import OptionParser
    from rich_argparse.optparse import IndentedRichHelpFormatter
    from rich_argparse import RichHelpFormatter as ArgRichHelpFormatter
    import argparse
    description = "x " * 1000

    def visible(text):
        return SGR.sub("", text)

    def optparse_help(width):
        formatter = IndentedRichHelpFormatter(width=width)
        parser = OptionParser(prog="PROG", formatter=formatter, description=description)
        parser.add_option("--option", help="short help")
        output = visible(parser.format_help())
        return formatter.width, output, max(map(len, output.splitlines()), default=0)

    default_width, default_text, default_max = optparse_help(None)
    explicit_width, explicit_text, explicit_max = optparse_help(70)
    argument_formatter = ArgRichHelpFormatter("PROG", width=70)
    parser = argparse.ArgumentParser(prog="PROG", description=description,
        formatter_class=lambda prog: ArgRichHelpFormatter(prog, width=70))
    parser.add_argument("--option", help="short help")
    argument_text = visible(parser.format_help())
    return {
        "provenance": provenance,
        "probe_version": "B-short-tokens-x-description-v2",
        "formatter_width_default": default_width,
        "formatter_width_explicit_70": explicit_width,
        "argparse_width_explicit_70": argument_formatter._width,
        "max_help_line_width_default": default_max,
        "max_help_line_width_explicit_70": explicit_max,
        "argparse_max_line_width_explicit_70": max(map(len, argument_text.splitlines()), default=0),
        "default_visible_text": default_text,
        "explicit_visible_text": explicit_text,
        "argparse_visible_text": argument_text,
        "description_words": 1000,
        "geometry_expected_rule": "for even declared width w, 1000 single ASCII x words wrap to maximum w-1; verify original controls before any attribution",
    }



# --------------------------------------------------------------- C: rich-rst CLI

def run_rich_rst_error_path(root: pathlib.Path, columns: str | None) -> dict:
    provenance = require_provenance(root, "rich_rst")
    import rich_rst

    import rich_rst.__main__ as entry

    saved_argv = sys.argv
    saved_init = rich_rst.RestructuredText.__init__

    def failing_init(self, *args, **kwargs):
        raise ValueError("Fake parse failure")

    with tempfile.TemporaryDirectory() as temp:
        path = pathlib.Path(temp) / "test.rst"
        path.write_text("Some RST.", encoding="utf-8")
        stdout = io.StringIO()
        try:
            rich_rst.RestructuredText.__init__ = failing_init
            sys.argv = ["rich-rst", str(path), "--debug"]
            with contextlib.redirect_stdout(stdout):
                try:
                    exit_code = entry.main()
                except SystemExit as exc:  # CLI 以 SystemExit 结束时也如实记录
                    exit_code = exc.code
        finally:
            rich_rst.RestructuredText.__init__ = saved_init
            sys.argv = saved_argv
    text = stdout.getvalue()
    lines = [line for line in text.splitlines() if line.strip()]
    return {
        "provenance": provenance,
        "exit_code": exit_code,
        "rendered_length": len(text),
        "max_line_width": max((len(line) for line in lines), default=0),
        "unwrapped": unwrap(text),
        "columns_observed": columns,
    }


def probe_c(root: pathlib.Path) -> dict:
    columns = os.environ.get("COLUMNS")
    first = run_rich_rst_error_path(root, columns)
    # 同一进程内第二次运行用于确认可重复（不改变环境）。
    second = run_rich_rst_error_path(root, columns)
    stable = (first["exit_code"], first["max_line_width"], first["unwrapped"]) == (
        second["exit_code"], second["max_line_width"], second["unwrapped"])
    return {
        "provenance": first["provenance"],
        "exit_code": first["exit_code"],
        "max_line_width": first["max_line_width"],
        "unwrapped": first["unwrapped"],
        "repeat_stable": stable,
        "repeat_max_line_width": second["max_line_width"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True, choices=["A", "B", "C"])
    parser.add_argument("--product-root", required=True, type=pathlib.Path)
    parser.add_argument("--json-out", required=True, type=pathlib.Path)
    args = parser.parse_args()
    root = args.product_root.resolve()
    sys.path.insert(0, str(root))
    observations: dict = {}
    errors: dict = {}
    probes = {"A": [("colour", probe_a_colour)],
              "B": [("width", probe_b_width)],
              "C": [("panel", probe_c)]}[args.case]
    for name, probe in probes:
        try:
            observations[name] = probe(root)
        except Exception as exc:  # noqa: BLE001 - 见证必须如实记录失败
            errors[name] = f"{type(exc).__name__}: {exc}"
    payload = {
        "case": args.case,
        "product_root": str(root),
        "python": platform.python_version(),
        "sys_path_first": sys.path[0],
        "environment": {key: os.environ.get(key) for key in
                        ("NO_COLOR", "COLUMNS", "FORCE_COLOR", "TERM", "LINES")},
        "observations": observations,
        "errors": errors,
    }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
