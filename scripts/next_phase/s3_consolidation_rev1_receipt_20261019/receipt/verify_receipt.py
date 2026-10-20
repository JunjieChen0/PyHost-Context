"""Read-only byte verification for the REV1 adoption receipt; no submitted code runs."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import zipfile

SUBMISSION_SHA = "2e7ad39657d7e56439888218e53a9da4188cf8896ce9a866b4dc425d9d84b356"
PRIOR_REVIEW_SHA = "421c54fcc6a2a04afc797dd701fe2827d1c3ff39068bebfcf71c210810689251"
COPIES = {
    "EVIDENCE_CONSOLIDATION.md": "reviewed/EVIDENCE_CONSOLIDATION_REVIEWED.md",
    "results/reviewed_accounting.json": "reviewed/reviewed_accounting.json",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def files(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("duplicate ZIP name")
        for name in names:
            p = PurePosixPath(name)
            if p.is_absolute() or ".." in p.parts or "\\" in name:
                raise ValueError("unsafe ZIP name")
        return {info.filename: archive.read(info) for info in archive.infolist()
                if not info.is_dir()}


def verify(submission, prior_review):
    problems = []
    current_sha = sha(submission.read_bytes())
    prior_sha = sha(prior_review.read_bytes())
    if current_sha != SUBMISSION_SHA:
        problems.append("submission_sha256")
    if prior_sha != PRIOR_REVIEW_SHA:
        problems.append("previous_review_sha256")
    current, previous = files(submission), files(prior_review)
    manifest = json.loads(current["PACKAGE_MANIFEST.json"])
    members = manifest["members"]
    paths = [r["path"] for r in members]
    if len(paths) != len(set(paths)) or len(paths) != 34:
        problems.append("manifest_duplicate_or_count")
    if set(current) != set(paths) | {"PACKAGE_MANIFEST.json"}:
        problems.append("outer_exact_member_set")
    member_matches = 0
    for row in members:
        raw = current.get(row["path"])
        if raw is not None and len(raw) == row["bytes"] and sha(raw) == row["sha256"]:
            member_matches += 1
        else:
            problems.append("outer_member:" + row["path"])
    embedded = {n[len("reviewer_package/"):]: raw for n, raw in current.items()
                if n.startswith("reviewer_package/")}
    if set(embedded) != set(previous):
        problems.append("embedded_exact_member_set")
    embedded_matches = sum(embedded.get(n) == raw for n, raw in previous.items())
    if embedded_matches != len(previous) or len(previous) != 24:
        problems.append("embedded_original_bytes")
    copies = []
    for target, source in COPIES.items():
        equal = current.get(target) == previous.get(source)
        if not equal:
            problems.append("authoritative_copy:" + target)
        copies.append({"path": target, "source": source, "identical": equal,
                       "sha256": sha(current[target])})
    return {
        "ok": not problems, "problems": problems,
        "submission_sha256": current_sha, "prior_review_sha256": prior_sha,
        "outer_zip_file_entries": len(current),
        "outer_manifest_payloads": len(members), "outer_payload_matches": member_matches,
        "embedded_review_files": len(embedded), "embedded_exact_matches": embedded_matches,
        "authoritative_copies": copies,
        "scope": "byte_adoption_and_packaging_only",
        "submitted_code_executed": False, "candidate_or_tool_calls": 0,
        "installs": 0, "network_calls": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submission", type=Path, required=True)
    parser.add_argument("--previous-review", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify(args.submission, args.previous_review)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        result = {"ok": False, "problems": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
