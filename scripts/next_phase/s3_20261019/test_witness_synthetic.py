"""ClawsGO Science Agent：见证探针的合成验证（假产品，不导入真实候选）。

运行：``python3 -m pytest test_witness_synthetic.py -q``
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import textwrap

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE / "witness_probes.py"

FAKE_RICH_ARGPARSE_INIT = '''\
import argparse
import os


class RichHelpFormatter(argparse.HelpFormatter):
    def format_help(self):
        coloured = os.environ.get("NO_COLOR", "") == ""
        return ("\\x1b[36m--option\\x1b[0m  An option\\n" if coloured
                else "--option  An option\\n")
'''

FAKE_RICH_ARGPARSE_OPTPARSE = '''\
import optparse


class IndentedRichHelpFormatter(optparse.HelpFormatter):
    def __init__(self, indent_increment=2, max_help_position=24, width=None,
                 short_first=1):
        super().__init__(indent_increment, max_help_position, width, short_first)

    def format_usage(self, usage):
        return usage

    def format_heading(self, heading):
        return ""

    def format_description(self, description):
        return description or ""

    def format_option(self, option):
        return ("x" * (self.width - 1)) + "\\n"

    def format_option_help(self, formatter=None):
        return ("x" * (self.width - 1)) + "\\n"

    def format_help(self):
        return ("x" * (self.width - 1)) + "\\n"
'''

FAKE_RICH_RST_INIT = '''\
class RestructuredText:
    def __init__(self, code, **kwargs):
        self.code = code
'''

FAKE_RICH_RST_MAIN = '''\
from rich_rst import RestructuredText


def main():
    try:
        RestructuredText("x")
    except Exception as exc:
        print("| Error parsing reStructuredText |")
        print(f"| {type(exc).__name__}: {exc} |")
        return 1
    print("| ok |")
    return 0
'''


def run_probe(tmp_path, case, package_files):
    root = tmp_path / "product"
    for relative, content in package_files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content), encoding="utf-8")
    out = tmp_path / "witness.json"
    process = subprocess.run([sys.executable, str(SCRIPT), "--case", case,
                              "--product-root", str(root), "--json-out", str(out)],
                             capture_output=True, text=True, timeout=120)
    assert out.exists(), process.stderr
    return json.loads(out.read_text(encoding="utf-8"))


def test_fake_rich_argparse_probe(tmp_path):
    payload = run_probe(tmp_path, "A", {
        "rich_argparse/__init__.py": FAKE_RICH_ARGPARSE_INIT,
        "rich_argparse/optparse.py": FAKE_RICH_ARGPARSE_OPTPARSE,
    })
    colour = payload["observations"]["colour"]
    assert colour["colour_present"] is True
    assert colour["argparse_args_sgr"] == "36"
    assert payload["errors"] == {}
    assert payload["sys_path_first"] == str((tmp_path / "product").resolve())


def test_fake_optparse_width_probe(tmp_path):
    payload = run_probe(tmp_path, "B", {
        "rich_argparse/__init__.py": FAKE_RICH_ARGPARSE_INIT,
        "rich_argparse/optparse.py": FAKE_RICH_ARGPARSE_OPTPARSE,
    })
    width = payload["observations"]["width"]
    assert width["formatter_width_default"] == 78
    assert width["formatter_width_explicit_70"] == 70
    assert width["max_help_line_width_default"] == 77
    assert payload["errors"] == {}


def test_fake_rich_rst_probe(tmp_path):
    payload = run_probe(tmp_path, "C", {
        "rich_rst/__init__.py": FAKE_RICH_RST_INIT,
        "rich_rst/__main__.py": FAKE_RICH_RST_MAIN,
    })
    panel = payload["observations"]["panel"]
    assert panel["exit_code"] == 1
    assert "Error parsing reStructuredText" in panel["unwrapped"]
    assert "ValueError: Fake parse failure" in panel["unwrapped"]
    assert panel["repeat_stable"] is True
    assert payload["errors"] == {}
