"""ClawsGO Science Agent：打包 S1b 复核接受、既有字节导出与 S3 计划。"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
WS = HERE.parents[3]
REVIEW = pathlib.Path("/tmp/s1brev")
EXPORT = HERE / "export" / "s1b-existing-evidence-20261019"
STAGE = pathlib.Path("/tmp/s1b_review_stage")
OUT = WS / "deliverables" / "PyHost_S1b_ReviewAcceptance_and_S3Plan_20261019.zip"
FIXED = (2026, 10, 19, 0, 0, 0)


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def main():
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    for name in ("S1B_RESULTS_REVIEW_ACCEPTED_20261019.md",
                 "S3_PLAN_20261019.md", "S3_PLAN_20261019.json",
                 "export_existing_evidence.py", "build_s3_plan.py"):
        copy(HERE / name, STAGE / name)
    copy(REVIEW / "REVIEWER001_S1B_RESULTS_REVIEW.md",
         STAGE / "review/REVIEWER001_S1B_RESULTS_REVIEW.md")
    copy(REVIEW / "REVIEWER001_S1B_RESULTS_CLOSURE_20261019.json",
         STAGE / "review/REVIEWER001_S1B_RESULTS_CLOSURE_20261019.json")
    copy(REVIEW / "S3_HANDOFF.md", STAGE / "review/S3_HANDOFF.md")
    copy(REVIEW / "EXISTING_BYTES_REQUEST.json",
         STAGE / "review/EXISTING_BYTES_REQUEST.json")
    copy(REVIEW / "screening_guards.py", STAGE / "review/screening_guards.py")
    copy(REVIEW / "audit_results.py", STAGE / "review/audit_results.py")
    for path in sorted(EXPORT.rglob("*")):
        if path.is_file():
            copy(path, STAGE / "export" / path.relative_to(EXPORT))
    readme = STAGE / "README.md"
    readme.write_text("""# PyHost-Context：S1b 结果复核接受、既有字节导出与 S3 计划（2026-10-19）

本包回应 Reviewer 001 的 `PyHost_S1b_Reviewer001_ResultsReview_20261019.zip`（`4f35ea33…`）。
不含新的候选运行、安装或导入。

## 内容

| 路径 | 内容 |
|---|---|
| `S1B_RESULTS_REVIEW_ACCEPTED_20261019.md` | 逐条接受复核更正：节点计数 158/160/800、基线失败并列、撤回“两个机制”、预测效力收紧、分析器缺口 |
| `S3_PLAN_20261019.md` | S3 计划说明：两轴、行键、三条读取链、逐格字段、判定、预算 |
| `S3_PLAN_20261019.json` | 机器可读计划：192 套件行 + 48 见证行、工件 SHA-256、缺口清单 |
| `export/` | 按请求只读导出的既有字节：526 件必选（150,909,191 B）+ 700 件可选 |
| `export/.../EXPORT_MANIFEST.json` | 逐件哈希、来源路径与总计 |
| `review/` | 审阅意见、签署件、S3 交接、字节请求与两个守门脚本 |

## 关键更正

| 项 | 原报告 | 更正 |
|---|---:|---:|
| 跨项目翻转节点 | 156 | **158** |
| 项目×条件×节点 | — | **160** |
| 五次重复观测 | — | **800** |
| 机制数 | “2 个” | 不签认；保留三条事前路径标签，可描述为两个粗维度 |
| 预测效力 | 34/38 命中 | 成立，但“一律无翻转”基准同为 33/38，实际信号仅 1/5 命中 |

## 复算

```bash
python3 export_existing_evidence.py   # 需原始工作区；逐件核对后复制
python3 -m json.tool S3_PLAN_20261019.json > /dev/null
```

导出清单的逐件哈希可离线核对；S3 计划的行数、唯一行键与预算求和由
`build_s3_plan.py` 生成时校验。

## 边界

S3 执行、S2、repro、新键值仍未授权。S3 计划在 A/B 的 M2/M3 变异工件、见证代码、
目标断言与签名绑定补齐前不构成可执行输入。E2/E4 与最终机制数未签认。
""", encoding="utf-8")
    members = []
    for path in sorted(STAGE.rglob("*")):
        if path.is_file():
            members.append({"path": str(path.relative_to(STAGE)),
                            "sha256": sha(path), "bytes": path.stat().st_size})
    (STAGE / "PACKAGE_MANIFEST.json").write_text(
        json.dumps({"schema_version": "pyhost-context/S1b-review-acceptance/1.0",
                    "members": members}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(STAGE.rglob("*")):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(str(path.relative_to(STAGE)), date_time=FIXED)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    print(json.dumps({"zip": str(OUT), "sha256": sha(OUT), "bytes": OUT.stat().st_size,
                      "members": len(members)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
