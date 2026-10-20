"""ClawsGO Science Agent：只读核验回执接受件（不运行候选代码、不联网）。"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
RECEIPT_ZIP = HERE / "receipt/PyHost_EvidenceConsolidation_REV1_Receipt_Reviewer001_20261019.zip"
RECEIPT_SHA256 = "cebfa4fccf230c722ea89fbad04df657af5cdecd65011932155a3f5dbf09a549"
SUBMISSION_SHA256 = "2e7ad39657d7e56439888218e53a9da4188cf8896ce9a866b4dc425d9d84b356"
PRIOR_REVIEW_SHA256 = "421c54fcc6a2a04afc797dd701fe2827d1c3ff39068bebfcf71c210810689251"
SUBMISSION_NAME = "PyHost_EvidenceConsolidation_REV1_20261019.zip"


def default_submission() -> pathlib.Path:
    for parent in (HERE, *HERE.parents):
        candidate = parent / "deliverables" / SUBMISSION_NAME
        if candidate.is_file():
            return candidate
    return pathlib.Path("deliverables") / SUBMISSION_NAME


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_receipt(problems: list[str]) -> None:
    if not RECEIPT_ZIP.is_file():
        problems.append("receipt_zip_missing")
        return
    if sha(RECEIPT_ZIP.read_bytes()) != RECEIPT_SHA256:
        problems.append("receipt_zip_sha256")
    with zipfile.ZipFile(RECEIPT_ZIP) as z:
        manifest = json.loads(z.read("MANIFEST.json"))
        for member in manifest["members"]:
            data = z.read(member["path"])
            if len(data) != member["bytes"] or sha(data) != member["sha256"]:
                problems.append("receipt_member:" + member["path"])
        receipt = json.loads(z.read("REVIEWER001_RECEIPT.json"))
    if receipt["decision"] != "RECEIPT_ACCEPTED_CLOSURE_MAINTAINED":
        problems.append("receipt_decision")
    if receipt["new_execution_authorized"] is not False:
        problems.append("receipt_authorization")
    if receipt["publication_tier_certified"] is not False:
        problems.append("receipt_tier")
    if receipt["prior_review"]["sha256"] != PRIOR_REVIEW_SHA256:
        problems.append("receipt_prior_review")
    if receipt["submission"]["sha256"] != SUBMISSION_SHA256:
        problems.append("receipt_submission")
    ids = {x["id"]: x for x in receipt["append_only_clarifications"]}
    for key in ("RECEIPT_N1", "RECEIPT_N2"):
        if key not in ids:
            problems.append("receipt_clarification:" + key)


def check_submission(path: pathlib.Path, problems: list[str]) -> None:
    if not path.is_file():
        problems.append("submission_missing:" + str(path))
        return
    raw = path.read_bytes()
    if sha(raw) != SUBMISSION_SHA256:
        problems.append("submission_sha256")
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        members = json.loads(z.read("PACKAGE_MANIFEST.json"))["members"]
        if len(members) != 34:
            problems.append("submission_payload_count")
        if {m["path"] for m in members} | {"PACKAGE_MANIFEST.json"} != names:
            problems.append("submission_entry_set")
        for member in members:
            data = z.read(member["path"])
            if len(data) != member["bytes"] or sha(data) != member["sha256"]:
                problems.append("submission_member:" + member["path"])
        for left, right in (
            ("EVIDENCE_CONSOLIDATION.md", "reviewer_package/reviewed/EVIDENCE_CONSOLIDATION_REVIEWED.md"),
            ("results/reviewed_accounting.json", "reviewer_package/reviewed/reviewed_accounting.json"),
        ):
            if z.read(left) != z.read(right):
                problems.append("authoritative_copy:" + left)


def check_ack(problems: list[str]) -> None:
    text = (HERE / "ACKNOWLEDGEMENT_AND_ERRATA.md").read_text(encoding="utf-8")
    for marker in ("RECEIPT_N1", "RECEIPT_N2", "四份", "撤回", "不读取外层",
                   "35 个 ZIP 条目", "不新增执行授权"):
        if marker not in text:
            problems.append("ack_marker:" + marker)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--submission", type=pathlib.Path, default=default_submission())
    args = parser.parse_args()
    problems: list[str] = []
    check_receipt(problems)
    check_submission(args.submission, problems)
    check_ack(problems)
    print(json.dumps({
        "ok": not problems,
        "problems": problems,
        "receipt_sha256": RECEIPT_SHA256,
        "submission_sha256": SUBMISSION_SHA256,
        "candidate_or_tool_calls": 0,
        "installs": 0,
        "network_calls": 0,
    }, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
