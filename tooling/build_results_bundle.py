"""ClawsGO Science Agent：把核心矩阵的**结果与原始证据**打成单个 ZIP。

与实现包不同，这个包装的是**运行证据**：270 轮归档 JSONL、两个批次账本、筛查分析结果与
结果报告，外加逐文件 SHA-256 与组合指纹，使收件人可以在包内独立复算。

用法：``python3 tooling/build_results_bundle.py [--name 包名] [--out 输出目录]``
"""
import argparse
import datetime
import hashlib
import json
import pathlib
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
MATRIX = ROOT / "matrix"

EXTRA = (
    "matrix/freeze/analysis/RESULTS_core_20261015.md",
    "matrix/freeze/analysis/RESULTS_final_20261015.md",
    "matrix/freeze/analysis/core_20261015.json",
    "matrix/freeze/analysis/recovery_20261015.json",
    "matrix/freeze/qualification/qualified_units_20261015.json",
    "matrix/freeze/reviews/CORE_E2_REVIEW.txt",
    "matrix/freeze/reviews/E2_REVIEW_RECEIPT.json",
    "matrix/freeze/reviews/FULL_RESULTS_REVIEW.txt",
    "matrix/freeze/reviews/E4_FULL_RESULTS_REVIEW_RECEIPT.json",
    "matrix/freeze/reviews/full_results_review_input_MANIFEST.json",
    "matrix/freeze/reviews/full_results_review_core_continuity.json",
    "matrix/freeze/reviews/full_results_review_core_derived_changes.json",
    "matrix/freeze/reviews/full_results_review_independent_recovery_FOW.json",
    "matrix/freeze/reviews/full_results_review_REPRODUCE.txt",
    "matrix/freeze/reviews/full_results_review_full_results_script.py",
    "matrix/freeze/reviews/full_results_review_core_script.py",
    "matrix/freeze/ledgers/pilot-20261015.json",
    "matrix/freeze/ledgers/core-20261015.json",
    "matrix/freeze/ledgers/recovery-b1-after-core-20261015.json",
    "matrix/freeze/ledgers/recovery-b2-after-core-20261015.json",
    "matrix/freeze/ledgers/witness-after-core-20261015.json",
    "matrix/freeze/matrix_pre_run_commitment.json",
    "matrix/freeze/pre_signing_commitment.json",
    "matrix/freeze/signoff_history/pre_signing_commitment_b1402961.json",
    "matrix/freeze/user_signoff_20261015.json",
    "matrix/freeze/user_signoff_20261015_amended.json",
    "matrix/protocol/matrix_protocol_v0.4_reviewed.json",
    "matrix/schedules/core_schedule.json",
    "matrix/tooling/analyze_core.py",
)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def collect():
    members = {}
    for path in sorted((MATRIX / "runs").rglob("*")):
        if path.is_file():
            members[f"matrix/{path.relative_to(MATRIX).as_posix()}"] = path
    # 分析脚本与它依赖的模块必须随包：否则收件人无法从原始证据复算。
    # 用**当时实际执行的同一份代码**，也不用另发版本。
    for pattern in ("tooling/**/*.py", "bindings/*.json", "schedules/*.json",
                    "obligations/*.json", "protocol/matrix_protocol_v0.4_reviewed.json"):
        for path in sorted(MATRIX.glob(pattern)):
            if path.is_file() and "__pycache__" not in path.parts:
                members[f"matrix/{path.relative_to(MATRIX).as_posix()}"] = path
    for pattern in ("tooling/**/*.py",):
        for path in sorted(ROOT.glob(pattern)):
            if path.is_file() and "__pycache__" not in path.parts:
                members[path.relative_to(ROOT).as_posix()] = path
    for name in EXTRA:
        path = ROOT / name
        if path.is_file():
            members[name] = path
    return members


VERIFY = """# 逐项复核指引

## 1. 归档清单

```bash
python3 - <<'PY'
import hashlib, json, zipfile, pathlib
zf = zipfile.ZipFile("{name}.zip")
m = json.loads(zf.read("ARCHIVE_MANIFEST.json"))
bad = [x["path"] for x in m["members"]
       if hashlib.sha256(zf.read(x["path"])).hexdigest() != x["sha256"]]
payload = "".join(f"{{x['path']}}:{{x['sha256']}}\\n"
                  for x in sorted(m["members"], key=lambda y: y["path"]))
print("members:", m["member_count"], "mismatch:", bad)
print("aggregate matches:",
      hashlib.sha256(payload.encode()).hexdigest() == m["archive_aggregate_hash"])
PY
```

## 2. 从原始证据重算筛查结果

```bash
python3 matrix/tooling/analyze_core.py \\
    --ledger matrix/freeze/ledgers/core-20261015.json \\
    --out /tmp/recomputed.json
python3 matrix/tooling/analyze_recovery.py --json /tmp/recovery.json
```

前者回到 `matrix/runs/core/<project>/logs/<run_id>.jsonl` 重新解析每一轮、重新推导逐测试标签，
再与账本逐条对照（`totals.raw_evidence_mismatches` 必须为空）；后者从归档证据重算 4 个恢复块
与 54 个见证上下文。

## 3. 独立判断的重点

- 账本 `records` 里每一轮的 `jsonl_sha256` 是否等于包内对应 JSONL 的 SHA-256；
- `units` 里 54 个单位是否**全部** `stable=true`、`identity_changed=false`、
  `incomplete_rounds=0`、`perturbation_invalid_rounds=0`；
- `comparisons` 里有差异的组合是否**只有两个**；两处的方向是否 5/5 轮一致；
- `claim_levels` 是否只有 `K06_TERM@termcolor` 与 `K07_COLUMNS@click` 为 **E2**
  （E2 由**独立复核**给出的前置证据审核登记；未声明审核时不得写 E2）；
- `matrix/freeze/analysis/recovery_20261015.json` 里两个单位是否都是
  `qualified_blocks = 2`、`schedule_gate_satisfied = true`、`level_from_recovery = E4`；
- 见证是否 54 个上下文全部 `satisfied_in_all_contexts`，且 `safe_elimination_claimed = false`；
- 账本 `ledger_consistency.ok`、`not_run_count`、`failed` 三项。

## 4. 记账口径（独立复核更正后）

正式分析执行 **336** = 核心 270 + 恢复 12 + 见证 54；另有成功的引航轮 **1** 次，
归档完成的项目执行合计 **337**。失败的环境准备尝试**另列**为开发尝试，
**不**据此断言操作系统总进程数，也不使用先前报告中的 343 / 338。

## 5. 独立复核归档

`matrix/freeze/reviews/` 下保存两轮独立复核的原始件：核心轮 `CORE_E2_REVIEW.txt`、
`E2_REVIEW_RECEIPT.json`；完整结果轮 `FULL_RESULTS_REVIEW.txt`（结论 PASS）、
`E4_FULL_RESULTS_REVIEW_RECEIPT.json`，以及复核方的复算脚本、连续性与派生差异对照、
逐块与逐上下文的独立复核输出。复核者**未执行任何候选项目进程**
（`candidate_executions_by_reviewer = 0`），也**未改写**原始证据。
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="PyHost_MatrixCoreResults_20261015")
    parser.add_argument("--out", default=str(ROOT.parent / "deliverables"))
    args = parser.parse_args()

    members = collect()
    payloads = {}
    manifest_members = []
    for name, path in sorted(members.items()):
        data = path.read_bytes()
        payloads[name] = data
        manifest_members.append({"path": name, "bytes": len(data), "sha256": sha256_bytes(data)})

    verify_text = VERIFY.format(name=args.name)
    payloads["VERIFY.md"] = verify_text.encode("utf-8")
    manifest_members.append({"path": "VERIFY.md", "bytes": len(verify_text.encode()),
                             "sha256": sha256_bytes(verify_text.encode())})
    payload = "".join(f"{m['path']}:{m['sha256']}\n"
                      for m in sorted(manifest_members, key=lambda x: x["path"]))
    aggregate = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    manifest = {
        "schema_version": "pyhost-context/results-archive/1.0",
        "bundle": args.name,
        "built_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "member_count": len(manifest_members),
        "archive_aggregate_algorithm":
            "sorted('{path}:{sha256}\\n') → single SHA-256（与实现包同一算法）",
        "archive_aggregate_hash": aggregate,
        "members": manifest_members,
        "batch": {"core": "core-20261015", "pilot": "pilot-20261015",
                  "recovery": ["recovery-b1-after-core-20261015",
                               "recovery-b2-after-core-20261015"],
                  "witness": "witness-after-core-20261015",
                  "core_runs_completed": 270, "recovery_runs": 12,
                  "witness_contexts": 54, "pilot_runs": 1,
                  "formal_analysis_executions": 336,
                  "successful_pilot_executions_separate": 1,
                  "completed_archived_project_executions_including_pilot": 337,
                  "failed_environment_preparation_attempts_recorded_separately": True,
                  "all_os_process_count_established": False,
                  "budget_ceiling": 660,
                  "core_denominator_unchanged": 270},
        "independent_review": {
            "core_e2_review": {"verdict": "PASS",
                               "package": "PyHost_Core_E2_IndependentReview_20261015.zip"},
            "full_results_review": {"verdict": "PASS",
                                    "package": "PyHost_FullResults_IndependentReview_20261015.zip",
                                    "reviewed_at_utc": "2026-10-15T04:23:20+00:00",
                                    "feasibility_validation_can_close": True,
                                    "functional_defect_confirmed": False,
                                    "safe_elimination_claimed": False}},
        "note": ("原始运行证据包；不修改任何证据，不含候选源码。"
                 "候选测试只执行了预注册的 270 轮核心、12 轮恢复、54 个见证上下文，"
                 "外加一次预注册之外的引航轮（不计入核心分母）。"
                 "记账口径：正式分析执行 336 = 核心 270 + 恢复 12 + 见证 54；"
                 "加成功的引航 1 次，归档完成的项目执行合计 337；"
                 "失败的环境准备尝试另列为开发尝试，不据此断言操作系统总进程数。"),
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")

    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle = out_dir / f"{args.name}.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("ARCHIVE_MANIFEST.json", manifest_bytes)
        for name in sorted(payloads):
            zf.writestr(name, payloads[name])

    out_dir.joinpath(f"{args.name}.manifest.json").write_bytes(manifest_bytes)
    print(json.dumps({"bundle": str(bundle), "bytes": bundle.stat().st_size,
                      "members": manifest["member_count"], "aggregate": aggregate,
                      "bundle_sha256": sha256_bytes(bundle.read_bytes())},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
