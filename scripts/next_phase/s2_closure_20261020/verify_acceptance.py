"""ClawsGO Science Agent：S2 关闭件接受与登记附记的自校验（不联网、不运行候选）。"""
from __future__ import annotations

import hashlib
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def main() -> int:
    problems = []
    closure = read(HERE / "REVIEWER001_CLOSURE.json")
    addendum = read(HERE / "S2_REGISTRATION_ADDENDUM_20261020.json")
    levels = read(HERE / "registered_levels.json")
    disposition = read(HERE / "defect_disposition.json")

    if closure.get("decision") != "S2_RESULTS_CLOSED_WITH_REVIEWER_PARSER_CORRECTION":
        problems.append("closure_decision")
    for key in ("pre_run_commitment_hash", "schedule_sha256"):
        if addendum.get(key) != closure.get(key):
            problems.append("closure_field:" + key)
    if addendum.get("registered_suite_sessions") != closure.get("registered_calls"):
        problems.append("closure_field:registered_calls")
    if addendum.get("author_submission_sha256") != closure.get("author_submission_sha256"):
        problems.append("submission_sha")

    by_id = {u["unit_id"]: u for u in levels}
    if len(addendum.get("registrations") or []) != 5:
        problems.append("registration_count")
    for row in addendum["registrations"]:
        unit = by_id.get(row["unit_id"])
        if unit is None:
            problems.append("unknown_unit:" + row["unit_id"])
            continue
        if row["registered_level"] != unit["registered_level"]:
            problems.append("level_mismatch:" + row["unit_id"])
        if row["previous_level"] != unit["previous_level"]:
            problems.append("previous_level:" + row["unit_id"])
        if row["block_statuses"] != [unit["block_statuses"]["1"], unit["block_statuses"]["2"]]:
            problems.append("block_status:" + row["unit_id"])

    e4 = [u for u in addendum["registrations"] if u["registered_level"] == "E4"]
    e2 = [u for u in addendum["registrations"] if u["registered_level"] == "E2"]
    if len(e4) != 2 or {u["unit_id"] for u in e4} != {"twine/K06_TERM",
                                                      "rich-toolkit/K07_COLUMNS"}:
        problems.append("e4_units")
    if len(e2) != 3 or not all(u["unit_id"].startswith("typer/") for u in e2):
        problems.append("e2_units")
    if addendum["level_distribution_after"].get("E4") != 4:
        problems.append("distribution_e4")
    if addendum["level_distribution_after"].get("E3") != 1:
        problems.append("distribution_e3")
    if addendum["level_distribution_after"].get("E2") != 3:
        problems.append("distribution_e2")

    counts = next((item for item in disposition["items"] if item["id"] == "P-S2-2"), {})
    old_counts = counts.get("old_parser_signature_node_counts") or {}
    if old_counts.get("typer_16_sessions") != 195:
        problems.append("n1_195")
    if old_counts.get("typer_K07_B_2_sessions") != 278:
        problems.append("n1_278")
    if old_counts.get("other_12_sessions") != 0:
        problems.append("n1_zero")

    reference = HERE / "reference/PyHost_S2_Reviewer001_Closure_20261020.zip"
    if not reference.is_file():
        problems.append("closure_package_missing")
    elif sha(reference) != addendum["closure"]["sha256"]:
        problems.append("closure_package_sha256")

    for name in ("verify_review.json", "recompute_review.json"):
        result = read(HERE / "verification" / name)
        if result.get("ok") is not True:
            problems.append("verification_not_ok:" + name)

    print(json.dumps({"ok": not problems, "problems": problems,
                      "registered_levels": {u["unit_id"]: u["registered_level"]
                                            for u in addendum["registrations"]},
                      "candidate_or_tool_calls": 0, "installs": 0,
                      "network_calls": 0}, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
