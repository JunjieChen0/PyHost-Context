# SQJ Submission Checklist (2026-10-20)

Status values: **DONE** = available and checked; **DRAFT** = draft exists with
placeholders; **OPEN** = not started; **BLOCKED** = needs an author decision.

## A. Venue

| Item | Status | Note |
|---|---|---|
| Target journal | DONE | Software Quality Journal, Springer, hybrid |
| Article type | DONE | Original Paper |
| Scope fit | DONE | Testing, quality measurement, empirical case studies |
| LaTeX template | OPEN | Springer Nature `sn-jnl`; template not yet assembled |
| Abstract limit | DONE | 150–250 words |
| Keyword limit | DONE | 4–6 |
| Official CCF rank | BLOCKED | Third-party lists say C; the official catalogue has not been checked. Verify before submission. |
| Current submission guidelines | OPEN | Re-read at submission time for figure, data and AI-use rules |

## B. Author and title page

| Item | Status | Note |
|---|---|---|
| Author name | DONE | Junjie Chen |
| Affiliation | DONE | School of Computer Science and Engineering, Nanjing University of Science and Technology |
| Email | DONE | junjie_chen@njust.edu.cn |
| Corresponding author | DONE | Confirmed: sole author and corresponding author |
| Sole author / co-authors | DONE | Confirmed sole author; no co-authors |
| Postal address | DONE | No. 200, Xiaolingwei Street, Xuanwu District, Nanjing, Jiangsu Province, China, 210094 |
| ORCID | DONE | 0009-0002-3499-3621 |
| Working title | DRAFT | "Terminal Environment Keys and Test Outcomes in Python Test Suites: A Single-Key Perturbation Study" |
| Title page | DRAFT | `TITLE_PAGE_DRAFT.md` |

## C. Manuscript

| Item | Status | Note |
|---|---|---|
| Chinese section draft | DONE | Existing `manuscript/` section draft |
| English full draft | OPEN | Main remaining work |
| Method | OPEN | Write first; numbers and protocol are fixed |
| Results (RQ1/RQ2/RQ3) | OPEN | Use REV2 §4 numbers and denominators |
| Related work | OPEN | Six-paper map plus additional references |
| Introduction and contribution | OPEN | Use the bounded contribution paragraph; no priority claim |
| Discussion | OPEN | Representation-layer oracles, shared dependency, practical implications |
| Threats to validity | OPEN | Sampling bias 10/4/11, frozen stack, strata, screening vs recovery, block non-independence |
| Conclusion | OPEN | No prevalence or functional-defect claim |
| Abstract | OPEN | 150–250 words; four quantitative facts only |
| Keywords | OPEN | 4–6 |

## D. Figures and tables

| Item | Status | Note |
|---|---|---|
| Unit-stratum figure | OPEN | Exploration 144, validation 56, S1b 40 |
| Five-signal read-chain figure | OPEN | Mark the rich-argparse `COLUMNS` stdlib `optparse` counterexample |
| S2 two-block status table | OPEN | Four E4, one E3, three E2 |
| S3 decision-distribution table | OPEN | 108 original cells and 12 corrective cells, reported separately |
| Figure permissions | N/A | All figures generated from own data |

## E. References

| Item | Status | Note |
|---|---|---|
| Six core references | DONE | `LITERATURE_MAP.csv` with DOI/URL |
| Additional references | OPEN | Methodological and Python-testing background |
| Reference style | OPEN | Springer Nature style; check `sn-jnl` bibliography |
| Citation audit | OPEN | Verify every DOI and page range before submission |

## F. Declarations

| Item | Status | Note |
|---|---|---|
| Funding | DONE | No specific funding; statement written |
| Competing interests | DONE | Confirmed: the author declares no competing interests |
| Data availability | DONE | Zenodo DOI 10.5281/zenodo.23491391; GitHub live |
| Code availability | DONE | GitHub live; Zenodo DOI 10.5281/zenodo.23491391 |
| Repository identifiers | DONE | GitHub URL and Zenodo DOI inserted |
| Author contributions | DRAFT | CRediT draft for sole author |
| Ethics / consent | DRAFT | Not applicable; confirm |
| Acknowledgments | OPEN | Add or remove |
| AI use disclosure | DONE | Author-provided statement; English text in the declarations |
| Preprint / prior submission | DONE | No preprint; not under consideration elsewhere |
| Suggested reviewers | OPEN | If the system requires them |

## G. Supplementary and artifacts

| Item | Status | Note |
|---|---|---|
| Frozen evidence bundle | DONE | Existing closed packages and hashes |
| Recomputation scripts | DONE | Existing `verify_*`/`recompute_*` scripts |
| Submission-size bundle | OPEN | Select and compress only what the journal needs |
| README for supplementary | OPEN | Explain hashes, stages and non-execution of candidates |

## H. Pre-submission

| Item | Status | Note |
|---|---|---|
| Internal consistency audit | OPEN | Every number tied to REV2 §4 and its denominator |
| Forbidden-wording scan | OPEN | No "first", "novel", "proves", prevalence, functional defect |
| Mock peer review | OPEN | Recommended before submission |
| Cover letter | DRAFT | `COVER_LETTER_DRAFT.md` |
| Format conversion | OPEN | `sn-jnl` PDF and source |
| Author approval | BLOCKED | Final read and sign-off by Junjie Chen |

## Boundary

No new experiments are required or authorized for this checklist. S2/S3/repro/S1b
replay and new samples remain unauthorized.
