"""ClawsGO Science Agent：SQJ 投稿材料底稿的只读一致性检查。

它检查作者元数据、标题页、cover letter、声明与清单的结构和关键限定。
它不判断稿件质量、期刊适配或录用概率，也不替代作者确认。
"""
from __future__ import annotations

import json
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
PROBLEMS: list[str] = []
CHECKS = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if not ok:
        PROBLEMS.append(name + ((":" + detail) if detail else ""))


def text(name: str) -> str:
    return (HERE / name).read_text(encoding="utf-8")


def orcid_checksum_ok(value: str) -> bool:
    compact = value.replace("-", "")
    if len(compact) != 16 or not compact[:15].isdigit():
        return False
    total = sum(int(digit) * (2 ** (15 - index)) for index, digit in enumerate(compact[:15]))
    expected = (12 - total % 11) % 11
    expected_char = "X" if expected == 10 else str(expected)
    return compact[15] == expected_char


def main() -> int:
    metadata = json.loads(text("AUTHOR_METADATA.json"))
    author = metadata["authors"][0]
    check("single_author_record", len(metadata["authors"]) == 1)
    check("author_name", author["full_name"] == "Junjie Chen")
    check("author_affiliation", "Nanjing University of Science and Technology" in author["affiliation"])
    check("author_email", author["email"] == "junjie_chen@njust.edu.cn")
    check("email_shape", re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", author["email"]) is not None)
    check("orcid_value", author["orcid"] == "0009-0002-3499-3621")
    check("orcid_shape", re.fullmatch(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", author["orcid"]) is not None)
    check("orcid_checksum", orcid_checksum_ok(author["orcid"]))
    check("orcid_confirmed", metadata["confirmed_by_author"]["orcid"] is True)
    check("target_journal", metadata["target_journal"] == "Software Quality Journal")
    check("article_type", metadata["article_type"] == "Original Paper")
    check("name_confirmed", metadata["confirmed_by_author"]["name"] is True)
    check("affiliation_confirmed", metadata["confirmed_by_author"]["affiliation"] is True)
    check("email_confirmed", metadata["confirmed_by_author"]["email"] is True)
    check("sole_and_corresponding", metadata["confirmed_by_author"]["sole_author"] is True
          and metadata["confirmed_by_author"]["corresponding_author"] is True)
    check("postal_address", author["postal_address"] ==
          "No. 200, Xiaolingwei Street, Xuanwu District, Nanjing, Jiangsu Province, China, 210094")
    check("postal_confirmed", metadata["confirmed_by_author"]["postal_address"] is True)
    check("no_funding", metadata["funding"]["has_funding"] is False
          and "no specific grant" in metadata["funding"]["statement"])
    check("funding_confirmed", metadata["confirmed_by_author"]["funding"] is True)
    check("no_preprint_or_prior", metadata["preprint_and_prior_submission"]["preprint"] is False
          and metadata["preprint_and_prior_submission"]["under_consideration_elsewhere"] is False)
    check("preprint_confirmed", metadata["confirmed_by_author"]["preprint_or_prior_submission"] is True)
    check("competing_interests_confirmed", metadata["confirmed_by_author"]["competing_interests"] is True
          and metadata["competing_interests"]["has_competing_interests"] is False
          and "no competing interests" in metadata["competing_interests"]["statement"])
    check("data_route_confirmed", metadata["confirmed_by_author"]["data_availability_route"] is True
          and metadata["data_and_code"]["route"] == "zenodo_and_github"
          and metadata["data_and_code"]["confirmed"] is True)
    check("data_identifiers_pending", metadata["data_and_code"]["zenodo_doi"] is None
          and metadata["data_and_code"]["github_url"] is None)
    check("ai_author_provided", metadata["ai_use_disclosure"]["status"] == "author_provided"
          and metadata["ai_use_disclosure"]["package_drafts_wording"] is False)
    check("ai_text_zh", "本研究使用 OpenAI ChatGPT" in metadata["ai_use_disclosure"]["source_text"])
    check("ai_text_en", "This study used OpenAI ChatGPT" in metadata["ai_use_disclosure"]["english_text"]
          and "I take responsibility" in metadata["ai_use_disclosure"]["english_text"])

    title_page = text("TITLE_PAGE_DRAFT.md")
    for marker in ("Terminal Environment Keys", "Junjie Chen", "Nanjing University of Science and Technology",
                   "junjie_chen@njust.edu.cn", "Original Paper", "[[TODO: 150–250 words",
                   "No. 200, Xiaolingwei Street", "210094", "sole author",
                   "0009-0002-3499-3621"):
        check("title_page:" + marker, marker in title_page)

    cover = text("COVER_LETTER_DRAFT.md")
    for marker in ("Software Quality Journal", "Original Paper", "COLUMNS", "TERM", "NO_COLOR",
                   "25\nprimary projects", "200 units", "40 units", "four are at E4", "one at E3",
                   "three remain at E2", "6/6", "3/3", "not under consideration elsewhere",
                   "does not claim ecosystem prevalence", "not treated as statistically independent"):
        check("cover:" + marker, marker in cover)

    declarations = text("DECLARATIONS_DRAFT.md")
    for marker in ("Funding", "Competing interests", "Data availability", "Code availability",
                   "Author contributions", "Ethics approval", "AI use disclosure",
                   "Preprint and prior submission", "[[TODO",
                   "No preprint has been posted",
                   "has not been submitted to, and is not",
                   "no specific grant", "The author declares no competing interests",
                   "Zenodo and GitHub", "insert the Zenodo DOI",
                   "This study used OpenAI ChatGPT", "I take responsibility",
                   "Author-provided statement"):
        check("declarations:" + marker, marker in declarations)

    checklist = text("SQJ_SUBMISSION_CHECKLIST.md")
    for marker in ("## A. Venue", "## B. Author and title page", "## C. Manuscript",
                   "## D. Figures and tables", "## E. References", "## F. Declarations",
                   "## G. Supplementary and artifacts", "## H. Pre-submission",
                   "150–250 words", "4–6", "sn-jnl", "Official CCF rank",
                   "No new experiments are required or authorized",
                   "Confirmed: sole author and corresponding author",
                   "No specific funding; statement written",
                   "Zenodo + GitHub; DOI and repository URL pending",
                   "Author-provided statement; English text in the declarations",
                   "No preprint; not under consideration elsewhere",
                   "Insert Zenodo DOI and GitHub URL before submission"):
        check("checklist:" + marker, marker in checklist)

    forbidden = ("novel contribution", "first study", "first to", "we prove", "proves that",
                 "groundbreaking", "state-of-the-art", "guaranteed acceptance")
    combined = (title_page + "\n" + cover).lower()
    for phrase in forbidden:
        check("no_overclaim:" + phrase, phrase not in combined)

    # 数字与 REV2 已核定口径一致
    for value in ("200 units", "40 units", "four are at E4", "one at E3", "three remain at E2",
                  "6/6", "3/3"):
        check("number_present:" + value, value in cover)
    check("no_stale_3086_claim_in_cover", "3,086" not in cover and "3,455" not in cover)

    print(json.dumps({
        "ok": not PROBLEMS,
        "checks": CHECKS,
        "problems": PROBLEMS,
        "scope": ("Structural and wording checks on author metadata and submission drafts. "
                  "No submission was made; no experiment was run; author confirmations remain open."),
        "new_experiments": 0,
        "network_calls": 0,
    }, ensure_ascii=False, indent=2))
    return 0 if not PROBLEMS else 1


if __name__ == "__main__":
    raise SystemExit(main())
