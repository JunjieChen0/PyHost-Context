"""ClawsGO Science Agent：从原始账本复算 S3 判定与证据哈希。

只读输入与状态目录；不执行候选、不联网、不写回任何签署输入。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, type=pathlib.Path)
    parser.add_argument("--inputs", required=True, type=pathlib.Path)
    parser.add_argument("--results", type=pathlib.Path, default=HERE.parent / "results")
    parser.add_argument("--out", type=pathlib.Path,
                        default=HERE.parent / "results" / "recomputed")
    args = parser.parse_args()

    sys.path.insert(0, str(args.inputs.resolve()))
    import s3_adjudicate as adjudicate  # noqa: E402

    ledger = json.loads((args.state / "ledger.json").read_text(encoding="utf-8"))
    records = list(ledger["records"].values())
    rows = json.loads((args.inputs / "S3_ROWS_20261019.json").read_text(encoding="utf-8"))
    spec = json.loads((args.inputs / "inputs/witness_spec.json").read_text(encoding="utf-8"))["spec"]
    commitment = json.loads((args.inputs / "S3_PRE_RUN_COMMITMENT_20261019.json").read_text(encoding="utf-8"))

    results = {}
    problems = []
    for record in records:
        if record["row_key"] in results:
            problems.append("duplicate_row:" + record["row_key"])
        results[record["row_key"]] = record
    if len(records) != 240:
        problems.append(f"record_count:{len(records)}")
    all_rows = rows["suite_rows"] + rows["witness_rows"]
    if canonical(all_rows) != rows["rows_sha256"]:
        problems.append("rows_digest")
    if ledger.get("rows_sha256") != rows["rows_sha256"]:
        problems.append("ledger_rows_digest")
    if ledger.get("commitment_sha256") != sha256(
            args.inputs / "S3_PRE_RUN_COMMITMENT_20261019.json"):
        problems.append("ledger_commitment")
    if commitment.get("rows_sha256") != rows["rows_sha256"]:
        problems.append("commitment_rows")

    report_checked = 0
    for record in records:
        if record.get("call_kind") != "suite":
            continue
        report = pathlib.Path(record["run_dir"]) / "report.jsonl"
        if not report.is_file():
            # 交付包内的证据目录布局：<state>/runs/<run_id>/attempt-<n>/
            report = (args.state / "runs" / record["run_id"]
                      / f"attempt-{record.get('attempt', 1):03d}" / "report.jsonl")
        if not report.is_file():
            problems.append("report_missing:" + record["run_id"])
            continue
        if sha256(report) != record.get("report_jsonl_sha256"):
            problems.append("report_hash:" + record["run_id"])
        report_checked += 1

    expected = json.loads((args.results / "adjudication.json").read_text(encoding="utf-8"))
    actual = adjudicate.adjudicate(rows, results, spec)
    if json.dumps(actual, sort_keys=True, ensure_ascii=False) != json.dumps(
            expected, sort_keys=True, ensure_ascii=False):
        problems.append("adjudication_mismatch")

    invalid = []
    for archive in sorted((args.inputs / "inputs/trees").rglob("*.tar.gz")):
        import tarfile
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                if not (member.isfile() and member.name.endswith(".py")):
                    continue
                try:
                    ast.parse(tar.extractfile(member).read(), filename=member.name)
                except SyntaxError as error:
                    invalid.append({"tree": archive.name, "member": member.name,
                                    "line": error.lineno, "message": error.msg})
    if len(invalid) != 4:
        problems.append(f"syntax_invalid_count:{len(invalid)}")

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "adjudication.json").write_text(
        json.dumps(actual, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    summary = {
        "ok": not problems,
        "problems": problems,
        "records": len(records),
        "suite_reports_verified": report_checked,
        "cells": len(actual["cells"]),
        "equal_pass": len(actual["equal_pass"]),
        "rows_sha256": rows["rows_sha256"],
        "commitment_sha256": ledger.get("commitment_sha256"),
        "adjudication_sha256": canonical(actual),
        "syntax_invalid_files": invalid,
        "candidate_imports_or_calls": 0,
        "network_calls": 0,
    }
    (args.out / "recompute.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
