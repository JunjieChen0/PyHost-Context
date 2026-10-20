"""ClawsGO Science Agent：核验总账 REV1 的权威文件与复核包自校验。"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    problems = []
    reviewed_doc = ROOT / "EVIDENCE_CONSOLIDATION.md"
    reviewed_json = ROOT / "results/reviewed_accounting.json"
    package_doc = ROOT / "reviewer_package/reviewed/EVIDENCE_CONSOLIDATION_REVIEWED.md"
    package_json = ROOT / "reviewer_package/reviewed/reviewed_accounting.json"
    for left, right in ((reviewed_doc, package_doc), (reviewed_json, package_json)):
        if not (left.is_file() and right.is_file()) or sha(left) != sha(right):
            problems.append("byte_mismatch:" + left.name)
    errata = (ROOT / "ACCEPTANCE_AND_ERRATA.md").read_text(encoding="utf-8")
    for marker in ("3,056", "2,984", "48 个名称", "12 个 `TECHNICAL_FAILURE`",
                   "同一单位", "不等于只能投 CCF-C", "不签认投稿档次"):
        if marker not in errata:
            problems.append("errata_marker_missing:" + marker)
    account = json.loads(reviewed_json.read_text(encoding="utf-8"))
    if account["RQ1_strata"]["exploration"]["units"] != 144:
        problems.append("exploration_units")
    if account["RQ1_strata"]["validation"]["units"] != 56:
        problems.append("validation_units")
    if account["S3"]["registered_attempts"] != 296:
        problems.append("s3_attempts")
    if account["claim_limits"]["publication_tier_certified"] is not False:
        problems.append("publication_tier")
    if account["claim_limits"]["G1_satisfied"] is not False:
        problems.append("G1")

    verify_script = ROOT / "reviewer_package" / "verify_review.py"
    result = subprocess.run([sys.executable, str(verify_script)],
                            capture_output=True, text=True)
    review_ok = False
    if result.returncode == 0:
        try:
            review_ok = json.loads(result.stdout).get("ok") is True
        except json.JSONDecodeError:
            review_ok = False
    if not review_ok:
        problems.append("reviewer_package_verify_failed")

    print(json.dumps({
        "ok": not problems,
        "problems": problems,
        "reviewed_doc_sha256": sha(reviewed_doc),
        "reviewed_accounting_sha256": sha(reviewed_json),
        "reviewer_package_ok": review_ok,
        "candidate_calls": 0,
        "network_calls": 0,
    }, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
