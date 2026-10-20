"""ClawsGO Science Agent：CCF-C 定位档案 REV1 的只读推导与字节一致性校验。

本脚本把两类东西分开：
1. 从两份权威签署快照（S2 前总账、S2 关闭件）重新推导数字；
2. 核对档案中的主张措辞、证据路径、证据字节与文献条目结构。

它能证明“档案与快照一致、证据字节未变”。它不能证明文献语义理解正确、
贡献具有绝对新颖性、稿件会被录用，或审核者是人工外审。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
from collections import Counter

HERE = pathlib.Path(__file__).resolve().parent

BASE_REL = "pyhost-context/extension/next_phase/s3_consolidation_rev1_20261019/results/reviewed_accounting.json"
S2_REL = "pyhost-context/extension/next_phase/s2_closure_20261020/REVIEWER001_CLOSURE.json"

EXPECTED = {
    "pre_research_calls": 3056,
    "pre_suite_sessions": 2984,
    "pre_witness_calls": 72,
    "pre_covered_calls": 3425,
    "post_research_calls": 3086,
    "post_suite_sessions": 3014,
    "post_witness_calls": 72,
    "post_covered_calls": 3455,
    "admission_calls": 59,
    "native_direct_calls": 310,
    "registered_units": 8,
    "e4_units": 4,
    "e3_units": 1,
    "e2_units": 3,
    "screened_units": 240,
    "exploration_units": 144,
    "validation_units": 56,
    "s1b_units": 40,
    "primary_units": 200,
    "project_names": 30,
    "primary_projects": 25,
    "supplementary_projects": 5,
    "s2_sessions": 30,
    "s2_blocks": 10,
    "s2_e4_units": 2,
    "s2_e2_units": 3,
    "s1b_signal_units": 5,
    "typer_s2_units": 3,
    "repair_exploration_passed": 6,
    "repair_exploration_denominator": 6,
    "repair_validation_passed": 3,
    "repair_validation_denominator": 3,
    "s3_original_cells": 108,
    "s3_retained": 9,
    "s3_corrective_cells": 12,
    "s3_blindspots": 1,
    "s3_signed_degradation": 0,
}


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: pathlib.Path) -> str:
    return sha_bytes(path.read_bytes())


def find_workspace(explicit: pathlib.Path | None) -> pathlib.Path | None:
    if explicit is not None:
        return explicit
    for parent in (HERE, *HERE.parents):
        if (parent / "pyhost-context").is_dir():
            return parent
    return None


def derive(base: dict, s2: dict) -> dict:
    calls = base["registered_calls"]
    pre = {
        "pre_research_calls": calls["research_total"],
        "pre_suite_sessions": calls["research_call_kinds"]["pytest_suite_session_calls"],
        "pre_witness_calls": calls["research_call_kinds"]["standalone_witness_calls"],
        "pre_covered_calls": calls["covered_registration_total"],
        "admission_calls": calls["admission_S1a_and_D_only"],
        "native_direct_calls": calls["native_direct_pytest_calls"],
    }
    if s2["registered_calls"] != s2["registered_suite_sessions"]:
        raise ValueError("S2 registered_calls != registered_suite_sessions")
    added = s2["registered_suite_sessions"]
    post = {
        "post_research_calls": pre["pre_research_calls"] + added,
        "post_suite_sessions": pre["pre_suite_sessions"] + added,
        "post_witness_calls": pre["pre_witness_calls"],
        "post_covered_calls": pre["pre_covered_calls"] + added,
    }

    levels: dict[tuple[str, str], str] = {}
    for unit in base["highest_registered_unit_levels"]:
        levels[(unit["project"], unit["condition"])] = unit["highest_signed_level"]
    s2_levels = []
    for unit in s2["registered_levels"]:
        project, condition = unit["unit_id"].split("/")
        levels[(project, condition)] = unit["registered_level"]
        s2_levels.append((project, condition, unit["registered_level"]))
    counts = Counter(levels.values())

    strata = base["RQ1_strata"]
    projects = base["projects"]
    repair = base["repair_endpoints"]
    s3 = base["S3"]

    derived = dict(pre)
    derived.update(post)
    derived.update({
        "registered_units": len(levels),
        "e4_units": counts.get("E4", 0),
        "e3_units": counts.get("E3", 0),
        "e2_units": counts.get("E2", 0),
        "screened_units": strata["exploration"]["units"] + strata["validation"]["units"] + strata["S1b"]["units"],
        "exploration_units": strata["exploration"]["units"],
        "validation_units": strata["validation"]["units"],
        "s1b_units": strata["S1b"]["units"],
        "primary_units": strata["exploration"]["units"] + strata["validation"]["units"],
        "project_names": projects["research_registered_project_names"]["count"],
        "primary_projects": projects["primary_exploration_and_validation"]["count"],
        "supplementary_projects": projects["supplementary_S1b"]["count"],
        "s2_sessions": s2["registered_suite_sessions"],
        "s2_blocks": 2 * len(s2["registered_levels"]),
        "s2_e4_units": sum(1 for _, _, level in s2_levels if level == "E4"),
        "s2_e2_units": sum(1 for _, _, level in s2_levels if level == "E2"),
        "s1b_signal_units": len(s2["registered_levels"]),
        "typer_s2_units": sum(1 for project, _, _ in s2_levels if project == "typer"),
        "repair_exploration_passed": repair["exploration"]["passed"],
        "repair_exploration_denominator": repair["exploration"]["denominator"],
        "repair_validation_passed": repair["validation"]["passed"],
        "repair_validation_denominator": repair["validation"]["denominator"],
        "s3_original_cells": sum(s3["original_108_repair_cell_labels"].values()),
        "s3_retained": s3["original_108_repair_cell_labels"]["RETAINED"],
        "s3_corrective_cells": sum(s3["corrective_12_repair_cell_labels"].values()),
        "s3_blindspots": s3["independent_original_suite_blindspots"],
        "s3_signed_degradation": s3["same_context_degradation_signed"],
    })
    return derived


def verify(workspace: pathlib.Path | None, evidence_root: pathlib.Path | None = None) -> dict:
    problems: list[str] = []
    checks = 0
    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal checks
        checks += 1
        if not ok:
            problems.append(name + ((":" + detail) if detail else ""))

    if workspace is None:
        return {"ok": False, "checks": 0, "problems": ["workspace_not_found"], "derived": {}}
    evidence_root = evidence_root or workspace

    ledger = json.loads((HERE / "CLAIM_LEDGER.json").read_text(encoding="utf-8"))
    hashes = json.loads((HERE / "EVIDENCE_HASHES.json").read_text(encoding="utf-8"))

    # 1. 证据字节：存在、非空、哈希一致
    hash_entries = {entry["path"]: entry for entry in hashes.get("entries", [])}
    check("evidence_hash_entries", len(hash_entries) >= 12, str(len(hash_entries)))
    for rel, entry in hash_entries.items():
        path = evidence_root / rel
        if not path.is_file():
            check("evidence_missing", False, rel)
            continue
        data = path.read_bytes()
        check("evidence_nonempty", len(data) > 0, rel)
        check("evidence_bytes", len(data) == entry.get("bytes"), rel)
        check("evidence_sha256", sha_bytes(data) == entry.get("sha256"), rel)

    # 2. 主张结构：十条、编号连续、证据被哈希清单覆盖
    claims = ledger.get("claims") or []
    check("claim_count", len(claims) == 10, str(len(claims)))
    check("claim_ids", [c.get("id") for c in claims] == [f"C{i}" for i in range(1, 11)])
    for claim in claims:
        evidence = claim.get("evidence") or []
        check("claim_evidence_present", bool(evidence), claim.get("id", "?"))
        for item in evidence:
            rel = item.get("path") if isinstance(item, dict) else item
            check("claim_evidence_hashed", rel in hash_entries, f"{claim.get('id')}:{rel}")

    # 3. 权威快照：哈希绑定与推导
    snapshots = ledger.get("source_snapshots") or {}
    base_path = workspace / BASE_REL
    s2_path = workspace / S2_REL
    check("base_snapshot_present", base_path.is_file())
    check("s2_snapshot_present", s2_path.is_file())
    if not (base_path.is_file() and s2_path.is_file()):
        return {"ok": False, "checks": checks, "problems": problems, "derived": {}}

    check("base_snapshot_sha256", sha_file(base_path) == snapshots.get("base", {}).get("sha256"))
    check("s2_snapshot_sha256", sha_file(s2_path) == snapshots.get("s2", {}).get("sha256"))

    base = json.loads(base_path.read_text(encoding="utf-8"))
    s2 = json.loads(s2_path.read_text(encoding="utf-8"))
    try:
        derived = derive(base, s2)
    except (KeyError, ValueError) as exc:
        return {"ok": False, "checks": checks,
                "problems": problems + ["derivation_failed:" + str(exc)], "derived": {}}

    for key, expected in EXPECTED.items():
        check("derived:" + key, derived.get(key) == expected, f"{derived.get(key)} != {expected}")

    check("covered_scope_sum",
          derived["post_covered_calls"] == derived["post_research_calls"]
          + derived["admission_calls"] + derived["native_direct_calls"])
    check("kind_sum",
          derived["post_research_calls"] == derived["post_suite_sessions"] + derived["post_witness_calls"])
    check("units_sum",
          derived["screened_units"] == derived["exploration_units"] + derived["validation_units"] + derived["s1b_units"])
    check("projects_sum",
          derived["project_names"] == derived["primary_projects"] + derived["supplementary_projects"])
    check("level_sum", derived["e4_units"] + derived["e3_units"] + derived["e2_units"] == derived["registered_units"])
    check("s2_level_sum", derived["s2_e4_units"] + derived["s2_e2_units"] == derived["s1b_signal_units"])
    check("mechanical_selection_not_recertified",
          base["projects"].get("original_26_mechanical_selection_claim_not_recertified") is True)
    check("toolchain_strata_not_pooled",
          base["RQ1_strata"]["S1b"].get("toolchain_strata_not_pooled_with_primary") is True)

    # 3b. 身份选择偏差：10 / 4 / 11 口径与裁定原件
    selection = json.loads((HERE / "SELECTION_BIAS_FACTS.json").read_text(encoding="utf-8"))
    wrong = selection["wrong_source_unselected"]["names"]
    resolver = selection["resolver_only"]["names"]
    selected = selection["selected_invalid"]["names"]
    derived["wrong_source_unselected"] = selection["wrong_source_unselected"]["count"]
    derived["resolver_only"] = selection["resolver_only"]["count"]
    derived["bounded_readmission_total"] = selection["bounded_readmission_total"]
    check("selection_wrong_count", derived["wrong_source_unselected"] == len(wrong) == 10)
    check("selection_resolver_count", derived["resolver_only"] == len(resolver) == 4)
    check("selection_selected_count", len(selected) == 1 and selected == ["pytz-deprecation-shim"])
    check("selection_sets_disjoint", not (set(wrong) & set(resolver)) and not (set(wrong) & set(selected))
          and not (set(resolver) & set(selected)))
    check("selection_collision_total",
          selection["potential_name_collisions"] == len(wrong) + len(resolver) + len(selected) == 15)
    check("selection_readmission_total",
          derived["bounded_readmission_total"] == len(wrong) + len(selected) == 11)
    check("selection_not_recertified",
          selection["original_mechanical_selection_claim_recertified"] is False)
    authority = selection["authority"]
    authority_path = workspace / authority["path"]
    check("selection_authority_present", authority_path.is_file())
    if authority_path.is_file():
        check("selection_authority_sha256", sha_file(authority_path) == authority["sha256"])
        authority_text = authority_path.read_text(encoding="utf-8")
        check("selection_authority_10", "10 项有 A6 错误源码收集记录" in authority_text)
        check("selection_authority_4", "四项停在依赖解析阶段" in authority_text)
        check("selection_authority_11", "共 11 个" in authority_text)

    # 4. 主张数字必须等于推导值
    for claim in claims:
        for key, expected in (claim.get("numeric_assertions") or {}).items():
            if key not in derived:
                check("claim_number_known", False, f"{claim.get('id')}:{key}")
            else:
                check("claim_number:" + claim.get("id", "?") + ":" + key,
                      derived[key] == expected, f"{derived[key]} != {expected}")

    # 5. 主张措辞：必须包含限定语，不得包含禁止语
    for claim in claims:
        statement = claim.get("statement", "")
        for token in claim.get("requires") or []:
            check("claim_requires:" + claim.get("id", "?") + ":" + token, token in statement)
        for token in claim.get("must_not_contain") or []:
            check("claim_forbids:" + claim.get("id", "?") + ":" + token, token not in statement)
        check("claim_allowed_wording", bool(claim.get("allowed_wording")), claim.get("id", "?"))
        check("claim_forbidden_wording", bool(claim.get("forbidden_wording")), claim.get("id", "?"))

    # 6. 主件：关键口径、D1-D3 限定与旧值上下文
    doc_path = HERE / "CCFC_POSITIONING_AND_EVIDENCE_AUDIT_REV2.md"
    check("main_document_present", doc_path.is_file())
    if doc_path.is_file():
        doc = doc_path.read_text(encoding="utf-8")
        for marker in ("3,086", "3,014", "3,455", "S2 前", "五个 S1b", "stdlib optparse",
                       "不扩展为全部八个单位", "选择偏差", "分阶段冻结", "同一个", "6/6", "3/3",
                       "Original Paper", "150–250", "4–6", "绝对新颖性", "定向检索",
                       "10 / 4 / 11", "10 个未入选", "四项停在依赖解析阶段", "共 11 项",
                       "240 是筛查单位", "只对选定合格单位", "不构成统计独立",
                       "S3 原批", "先于 S2", "阶段编号", "不是实际发生顺序",
                       "37 个数字", "13 条", "3 项缺件", "290 项检查", "作者记录",
                       "不核验 Reviewer 签署件"):
            check("doc_marker:" + marker, marker in doc)
        for line_no, line in enumerate(doc.splitlines(), 1):
            if any(old in line for old in ("3,056", "2,984", "3,425")):
                check("doc_old_total_contextual", "S2 前" in line, f"line {line_no}")
        for banned in ("本研究首次", "唯一有效方法", "已证明绝对新颖性", "生态患病率已测量",
                       "14 个未被选入", "每个筛查单位都做恢复", "296 项检查全部通过",
                       "独立复现 296", "两块统计独立"):
            check("doc_no_overclaim:" + banned, banned not in doc, banned)

    # 7. 文献表：结构、链接与方法描述
    with (HERE / "LITERATURE_MAP.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    check("literature_rows", len(rows) == 6, str(len(rows)))
    by_id = {row.get("id"): row for row in rows}
    check("literature_ids", list(by_id) == [f"L{i}" for i in range(1, 7)])
    for row in rows:
        link = (row.get("doi_or_url") or "").strip()
        check("literature_link:" + row.get("id", "?"), link.startswith("https://"))
    if "L1" in by_id:
        check("L1_commit_mining", "commits" in by_id["L1"]["method"].lower())
    if "L3" in by_id:
        method = by_id["L3"]["method"].lower()
        check("L3_single_resource", "single" in method or "individual resources" in method)
    if "L4" in by_id:
        check("L4_repeatable_compatibility", "repeatable compatibility" in by_id["L4"]["delta_of_this_study"].lower())
    if "L5" in by_id:
        check("L5_year_distinction", "2022" in by_id["L5"]["venue_year"] and "2021" in by_id["L5"]["venue_year"])
    if "L6" in by_id:
        check("L6_doi", by_id["L6"]["doi_or_url"] == "https://doi.org/10.1109/TSE.2026.3729363")
        check("L6_fresh_environment", "fresh original ci environments" in by_id["L6"]["method"].lower())

    # 8. 来源核验：七条、期刊事实、逐条限制
    sources = json.loads((HERE / "SOURCE_VERIFICATION.json").read_text(encoding="utf-8"))
    entries = sources.get("entries") or []
    check("source_entries", len(entries) == 7, str(len(entries)))
    venue = next((entry for entry in entries if entry.get("id") == "VENUE"), {})
    check("venue_hybrid", venue.get("publication_model") == "hybrid")
    check("venue_original_paper", venue.get("research_article_type") == "Original Paper")
    check("venue_sn_jnl", "sn-jnl" in (venue.get("latex_template") or ""))
    check("venue_abstract_range", venue.get("abstract_word_range") == [150, 250])
    check("venue_keyword_range", venue.get("keyword_range") == [4, 6])
    check("venue_ccf_unverified", "to be verified" in (venue.get("ccf_rank_claim") or ""))
    for entry in entries[:6]:
        check("source_limit:" + entry.get("id", "?"), bool(entry.get("limit")))
    l6 = next((entry for entry in entries if entry.get("id") == "L6"), {})
    check("source_L6_doi", l6.get("doi") == "10.1109/TSE.2026.3729363")
    check("source_L6_full_text_limit", "not obtained" in (l6.get("inspection") or ""))

    # 9. 全局禁止主张存在
    forbidden = " ".join(ledger.get("global_forbidden_claims") or []).lower()
    for token in ("prevalence", "functional defect", "absolute novelty", "one-shot pre-registration",
                  "all eight units"):
        check("global_forbidden:" + token, token in forbidden)

    return {
        "ok": not problems,
        "checks": checks,
        "problems": problems,
        "derived": derived,
        "scope": ("Derives numbers from the two signed snapshots and checks evidence bytes, claim wording, "
                  "selection-bias counts and literature structure. It compares recorded hashes and text markers; "
                  "it does not verify Reviewer signatures, does not prove literature semantics, absolute novelty, "
                  "acceptance probability, or human peer review."),
        "candidate_or_tool_calls": 0,
        "new_experiments": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=pathlib.Path)
    parser.add_argument("--evidence-root", type=pathlib.Path)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    result = verify(find_workspace(args.workspace), args.evidence_root)
    if args.summary:
        print(json.dumps({key: result[key] for key in ("ok", "checks", "problems", "scope")},
                         ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
