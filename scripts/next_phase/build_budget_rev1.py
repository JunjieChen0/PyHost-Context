"""ClawsGO Science Agent：预算与绑定修订版（REV1）。

原 `BUDGET_SCHEDULE_BINDINGS_20261018.json` 字节不变（复核方已按其哈希认证）；
本件追加实际具名候选口径与新增输入的绑定。
"""
import hashlib
import json
import pathlib

NEXT = pathlib.Path(__file__).resolve().parent
EXT = NEXT.parent
ROOT = EXT.parent
SOURCE = NEXT / "BUDGET_SCHEDULE_BINDINGS_20261018.json"
OUT = NEXT / "BUDGET_SCHEDULE_BINDINGS_20261018_REV1.json"

BOUND = [
    "extension/next_phase/PRE_REGISTRATION_20261018.json",
    "extension/next_phase/POOLS_20261018.json",
    "extension/next_phase/SCREEN_EVIDENCE_20261018.json",
    "extension/next_phase/STATIC_WITNESSES_20261018.json",
    "extension/next_phase/CASE_TABLES_20261018_REV1.json",
    "extension/next_phase/DERIVED_INDEX_20261018.json",
    "extension/next_phase/INPUT_CLOSURE_NEXT.json",
    "extension/next_phase/FACTUAL_ERRATA_AUTHOR_20261018.json",
    "extension/next_phase/S1_REVIEW_CORRECTIONS_20261018.json",
    "extension/next_phase/scan_candidate.py",
    "extension/next_phase/build_pools.py",
    "extension/next_phase/build_static_witnesses.py",
    "extension/next_phase/build_case_tables.py",
    "extension/next_phase/materialize_derived.py",
    "extension/next_phase/verify_s1_inputs.py",
    "extension/next_phase/run_next_admission.py",
    "extension/next_phase/closure.py",
    "extension/next_phase/admission/scheduler.py",
    "extension/next_phase/admission/synthetic.py",
    "extension/next_phase/test_next_admission.py",
    "extension/next_phase/test_reviewer001_s1_counterexamples.py",
    "extension/next_phase/replay_s1_counterexamples_fixed.py",
    "extension/next_phase/witnesses/repair_witness.py",
    "extension/next_phase/state/plans/next_admission_plan_draft.json",
    "extension/next_phase/state/proofs/SYNTHETIC_CONSUMPTION_PROOF.json",
    "extension/frame/frame_20261015.json",
    "extension/effective/EFFECTIVE_PROTOCOL_v1.1_Reviewer001.json",
    "extension/effective/EFFECTIVE_PROTOCOL_v1.2_Reviewer001.json",
    "tooling/admission_runner/integration/jsonl_producer.py",
    "matrix/tooling/matrix_runner/netwatch_sitecustomize.py",
]


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def main():
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    document = json.loads(json.dumps(source))
    document["schema_version"] = "pyhost-context/next-phase-budget-schedule-bindings/1.1"
    document["supersedes"] = {
        "path": str(SOURCE.relative_to(ROOT)),
        "sha256": sha(SOURCE),
    }
    document["named_candidate_execution"] = {
        "named_candidates_total": 29,
        "mechanism_named": 13,
        "reference_named": 16,
        "mechanism_statically_ineligible": {
            "structlog-sentry": "no_tests_in_sdist",
            "django-structlog": "no_tests_in_sdist",
            "pylint-plugin-utils": "no_tests_in_sdist",
            "schemathesis": "pytest_forces_width_override",
        },
        "mechanism_executable": 9,
        "admission_calls_per_candidate_max": 3,
        "admission_call_ceiling_named": 87,
        "admission_call_ceiling_if_48_candidates": 144,
        "note": "87 是当前具名候选的实际上限；144 只是计划上限的成本口径，不得为用满而发明候选。",
    }
    document["budget"]["named_cost_upper_bound"] = {
        "formula": "87 + 540 + 540 + 36 + 384 + 96",
        "maximum_at_12_projects_6_cases": 1683,
        "total_cost_ceiling_unchanged": 1740,
        "note": "1,740 保留为计划上限；具名范围内按 1,683 计成本。",
    }
    document["unresolved_inputs"] = [
        "参照池 16 项中 7 项属 dev_test_tools（不是 13）；构成不均衡且全部准入知情，报告中披露。",
        "机制池 13 项中 4 项静态不合格（三项无测试、一项被 pytest 固定宽度短路）；"
        "可执行 9 项，按实际 N 固定，不扩池补位。",
        "新案例的上下文四值表须在 S1 结束后按新键的合法输入补齐。",
        "候选 sdist 与依赖闭包在准入取回后逐件绑定；静态证据中的依赖版本须与实际解析版本核对。",
    ]
    document["bindings"] = {
        "rule": "放行件与批次承诺绑定下列字节；任何一份变化都需要重新签署。",
        "files": {path: sha(ROOT / path) for path in BOUND},
        "commitment_algorithm": "SHA256(sorted('path:sha256\\n'))",
        "not_included": "候选 sdist 与 wheel 在准入时按注册 Name/Version/SHA-256 与成员清单单独绑定。",
    }
    document["not_authorized"] = source["not_authorized"]
    OUT.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"bound_files": len(document["bindings"]["files"]),
                      "sha256": sha(OUT)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
