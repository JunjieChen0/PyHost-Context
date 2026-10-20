"""ClawsGO Science Agent：把 S1a 准入结果打成单个交付包。

收集账本、批次承诺、放行件、安装记录、逐尝试 receipt、A6/A7 日志、
结果 JSON/CSV、事后诊断与复算脚本；写出 README 与 MANIFEST 后压缩。
不执行候选、不安装、不联网。
"""
import hashlib
import json
import pathlib
import shutil
import zipfile

NEXT = pathlib.Path(__file__).resolve().parent
EXT = NEXT.parent
ROOT = EXT.parent
BATCH_ID = "next-admission-20261018"
RUNS = NEXT / "state" / "runs" / BATCH_ID
DELIVER = ROOT.parent / "deliverables"
NAME = "PyHost_NextExperiment_S1a_Admission_Results_20261018"
STAGE = DELIVER / NAME
ZIP = DELIVER / f"{NAME}.zip"


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def copy(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def main():
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)

    copy(NEXT / "REPORT_S1A_ADMISSION_20261018.md", STAGE / "REPORT.md")
    copy(NEXT / "S1A_ADMISSION_RESULTS_20261018.json",
         STAGE / "results/S1A_ADMISSION_RESULTS_20261018.json")
    copy(NEXT / "S1A_ADMISSION_PER_CANDIDATE_20261018.csv",
         STAGE / "results/S1A_ADMISSION_PER_CANDIDATE_20261018.csv")
    copy(RUNS / "ledger.json", STAGE / "evidence/ledger.json")
    copy(NEXT / "state" / "batches" / f"{BATCH_ID}.json",
         STAGE / f"evidence/batch/{BATCH_ID}.json")
    copy(NEXT / "state" / "plans" / "next_admission_plan_draft.json",
         STAGE / "evidence/plans/next_admission_plan_draft.json")
    copy(NEXT / "state" / "candidates_next.json", STAGE / "evidence/candidates_next.json")
    copy(NEXT / "effective" / "REVIEWER001_NEXT_ADMISSION_CLEARANCE.json",
         STAGE / "evidence/clearance/REVIEWER001_NEXT_ADMISSION_CLEARANCE.json")
    receipt = NEXT / "reviews" / "reviewer001_s1a_20261018" / "INSTALL_RECORD.json"
    if receipt.is_file():
        copy(receipt, STAGE / "evidence/install/INSTALL_RECORD.json")
    for name in ("recompute_s1a_results.py", "build_s1a_results.py"):
        copy(NEXT / name, STAGE / "tools" / name)

    ledger = json.loads((RUNS / "ledger.json").read_text(encoding="utf-8"))
    receipts = {"started.json", "commitment.json", "environment_lock.json",
                "calls_started.json", "result.json", "failure.json"}
    log_count = 0
    for record in ledger["records"]:
        attempt = ROOT / record["run_dir"]
        project = record["project"]
        for name in sorted(receipts):
            path = attempt / name
            if path.is_file():
                copy(path, STAGE / f"evidence/attempts/{project}/attempt-001/{name}")
        logs_root = attempt / "runtime" / project / "logs"
        if logs_root.is_dir():
            for path in sorted(logs_root.rglob("*")):
                if path.is_file():
                    copy(path, STAGE / f"evidence/logs/{project}/attempt-001/{path.name}")
                    log_count += 1

    diagnostics = RUNS / "diagnostics" / "post_hoc"
    if diagnostics.is_dir():
        for path in sorted(diagnostics.rglob("*")):
            if path.is_file():
                copy(path, STAGE / f"evidence/diagnostics/{path.relative_to(diagnostics)}")

    readme = f"""# PyHost-Context S1a 有界 B0 准入结果包

批次 `{BATCH_ID}`。本包含 S1a 准入的全部结果与可复算证据；不含候选源码树、
虚拟环境或 wheelhouse。

## 先读

- `REPORT.md`：结果、拒绝类别、成本与限制。
- `results/S1A_ADMISSION_RESULTS_20261018.json`：机器可读的完整结果。
- `results/S1A_ADMISSION_PER_CANDIDATE_20261018.csv`：逐候选简表。

## 结论摘要

- 29 个冻结位置：25 个可派发候选全部尝试，4 个静态不合格位置跳过。
- 机制组 0/8，参照组 2/4；实际准入 N=2（typer 0.27.2、pytest-xdist 3.8.0）。
- 计费调用 45 次（A6 23、A7 22），派发上限 75，具名上限 87。
- 无候选替换、无顺延补位、无重复尝试、无仪器中止。

## 证据结构

| 路径 | 内容 |
|---|---|
| `evidence/ledger.json` | 账本：记录、跳过、摘要、批次承诺 |
| `evidence/batch/` | 冻结批次承诺（36 个静态输入） |
| `evidence/clearance/` | Reviewer 001 签署的 S1a 放行件 |
| `evidence/install/` | 安装记录（六份补丁文件前后哈希） |
| `evidence/plans/` | 冻结的 29 位置计划草稿 |
| `evidence/candidates_next.json` | 候选工件与依赖闭包记录 |
| `evidence/attempts/<项目>/attempt-001/` | 每次尝试的 started/commitment/environment_lock/calls_started/result/failure |
| `evidence/logs/<项目>/attempt-001/` | A6 收集与 A7 基线的 JSONL、stdout/stderr、网络日志 |
| `evidence/diagnostics/` | 三条依赖解析失败的事后诊断（非准入调用） |
| `tools/` | 结果生成与复算脚本 |

## 复算

```bash
python3 tools/recompute_s1a_results.py --package .
```

核对清单字节、放行件与承诺链、位置覆盖、调用上限、逐候选承诺、结果与账本一致性、
通过集合与分类总数。输出 `RECOMPUTATION.json`；不执行候选、不联网。

## 边界

本包只覆盖 S1a。S1b 主矩阵、S2 恢复、S3 修复与工具 repro 均未执行；G1 仍未满足，
证据等级未变。参照池保留既有 B0 准入信息暴露标签，不声称任何候选完全未被见过。
"""
    (STAGE / "README.md").write_text(readme, encoding="utf-8")

    files = sorted(path for path in STAGE.rglob("*") if path.is_file())
    manifest = {
        "schema_version": "pyhost-context/s1a-delivery-manifest/1.0",
        "batch_id": BATCH_ID,
        "count": len(files),
        "logs": log_count,
        "files": {path.relative_to(STAGE).as_posix():
                  {"bytes": path.stat().st_size, "sha256": sha(path)}
                  for path in files},
    }
    (STAGE / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")

    if ZIP.exists():
        ZIP.unlink()
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(STAGE.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(STAGE).as_posix())
    with zipfile.ZipFile(ZIP) as archive:
        members = archive.namelist()
        bad = archive.testzip()
    print(json.dumps({"zip": str(ZIP), "members": len(members), "bytes": ZIP.stat().st_size,
                      "sha256": sha(ZIP), "manifest_count": len(files),
                      "logs": log_count, "zip_integrity": bad is None},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
