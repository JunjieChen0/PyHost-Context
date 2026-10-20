"""ClawsGO Science Agent：把矩阵 v0.4 落实包打成**单个 ZIP**（含 README、清单、复算脚本）。

用户偏好（记忆 ``deliver-as-single-bundle``）：结果打包成单个文件，附 README 与复算脚本，
不逐个文件发。

包内布局刻意保持**与仓库相对路径一致**，因此解压后可以直接运行复算：

    <解压目录>/matrix/tooling/run_all_checks.py

构造步骤：

1. 先核对磁盘上的矩阵事前承诺仍与当前规范文件匹配（不匹配即中止，由驱动重新计算后再打包）；
2. 收集成员：``matrix/``（排除 runs/venvs/缓存）、驱动复用的共享模块、冻结输入、
   三项检查结果输出；
3. 计算每个成员的字节数与 SHA-256，写出 ``ARCHIVE_MANIFEST.json`` 与组合指纹；
4. 写出包级 ``README.md`` 与 ``VERIFY.md``。

用法：``python3 tooling/build_matrix_bundle.py [--name 包名] [--out 输出目录]``
"""
import argparse
import datetime
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
MATRIX = ROOT / "matrix"
sys.path.insert(0, str(MATRIX / "tooling"))
from matrix_runner import commitment as commitment_mod  # noqa: E402

EXCLUDE_DIRS = {"runs", "venvs", "__pycache__", ".pytest_cache", ".git", "batches", "ledgers"}
SHARED_MEMBERS = (
    "tooling/admission_runner/env_state.py",
    "tooling/admission_runner/pytest_jsonl.py",
    "tooling/admission_runner/timeout_ledger.py",
    "tooling/admission_runner/integration/jsonl_producer.py",
    "tooling/restore_source_tree.py",
    "admission_freeze/b0_environment.json",
    "admission_freeze/per_candidate_conditions.json",
    "admission_freeze/installed_package_sets.json",
    "admission_freeze/executor_revision_after_run.json",
)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def matrix_members():
    members = {}
    for path in sorted(MATRIX.rglob("*")):
        if not path.is_file():
            continue
        parts = set(path.relative_to(MATRIX).parts)
        if parts & EXCLUDE_DIRS:
            continue
        members[f"matrix/{path.relative_to(MATRIX).as_posix()}"] = path
    return members


def collect():
    members = matrix_members()
    for name in SHARED_MEMBERS:
        path = ROOT / name
        if path.is_file():
            members[name] = path
    return members


def run(command, label):
    result = subprocess.run(command, capture_output=True, text=True, timeout=3600)
    return {"label": label, "command": [str(part) for part in command],
            "returncode": result.returncode}, result


def check_table(check_outputs, summaries):
    """由本次构建的真实检查结果生成清单，避免说明与实测漂移。"""
    lines = []
    for entry in summaries:
        label, state = entry["label"], "ok" if entry["ok"] else "FAILED"
        payload = check_outputs.get(f"checks/{label}.json")
        detail = ""
        if label == "protocol_consistency" and payload:
            detail = f"{json.loads(payload)['checked']} 项检查"
        elif label == "driver_preflight_and_plan" and payload:
            document = json.loads(payload)
            detail = (f"{document['checks_total']} 项，离线跳过 "
                      f"{len(document.get('checks_offline_skipped') or [])} 项")
        elif label.startswith("synthetic_") and label.endswith("_tests"):
            text = check_outputs.get(f"checks/{label}.txt", b"").decode(
                "utf-8", "replace")
            match = re.search(r"Ran (\d+) tests", text)
            detail = f"{match.group(1)} 项（含反例）" if match else "见 checks/ 内文本"
        elif label == "run_all_checks" and payload:
            detail = f"总判定 all_ok={json.loads(payload)['all_ok']}"
        lines.append(f"  {state:>6}  {label:<30}{detail}")
    return "\n".join(lines) + "\n"


def readme(name, members, manifest, check_outputs, commitment, summaries):
    check_table_text = check_table(check_outputs, summaries)
    return f"""# {name}

**日期：** {datetime.date.today().isoformat()}
**撰写：** ClawsGO Science Agent
**状态：** 设计已校订并落到驱动；**矩阵未冻结、未授权、未执行任何候选测试**

本包是对独立校订 `PyHost_MatrixProtocol_Reviewed_v0.4_20261014.zip` 的**落实与复算包**：
把校订要求矩阵驱动实现的四项——固定观察器路径、启动转换校验、固定调度消费、
候选/契约与数据源指纹绑定——写成了可检查的代码，并保留校订原文与其校验脚本。

## 先看这两份

| 文件 | 内容 |
|---|---|
| `matrix/README.md` | 本包是什么、一键复算怎么跑、改了哪三处、**没有**做什么 |
| `matrix/IMPLEMENTATION_MAP.md` | 校订条目 → 实现位置 → 检查项，逐条对照（含 D1—D8） |

## 一键复算

```bash
python3 matrix/tooling/run_all_checks.py
```

在本包内它运行的检查（都只读、都不执行候选项目），本次构建的实际结果：

```text
{check_table_text}```

其中"准入 venv 与冻结安装集合一致""候选工件字节与绑定一致""矩阵 venv 构建输入齐备"三项
需要本机工作区（候选工件、准入 venv、工具轮子）。在复算包内它们会被标为
`offline_skipped` 并给出原因，而不是假装通过——`checks/driver_preflight_and_plan.json` 里
可以看到 `workspace_available` 与 `checks_offline_skipped`。

注意两种模式的区别：**草案审查**允许这些跳过（便携只读复算）；**执行模式**把跳过与缺失
都算作拒绝原因，未核验的实体前提不会开始跑。

## 包内清单

```text
README.md                    本文件
VERIFY.md                    逐项复核指引
ARCHIVE_MANIFEST.json        {manifest["member_count"]} 个成员的字节数与 SHA-256，以及按路径排序的组合指纹
checks/                      本次三项检查的原始输出（JSON/文本）
matrix/                      protocol/ schedules/ obligations/ review_tools/ bindings/ tooling/ freeze/
matrix/freeze/               矩阵事前承诺 {commitment[:16]}…（由驱动在任何一轮之前重新计算）
tooling/ matrix/ 复算所需的共享模块   准入期纯函数与冻结输入（逐字节同仓库）
admission_freeze/             B0 环境、候选条件、冻结安装集合、执行器修订记录
```

归档清单一共 **{manifest["member_count"]} 个载荷**；ZIP 内实际条目为
**{manifest["member_count"] + 1} 个**（多出的是 `ARCHIVE_MANIFEST.json` 自身，它不把自己列进载荷表）。

`matrix/freeze/matrix_pre_run_commitment.json` 是**事前承诺**（在任何一轮之前计算并绑定进
每轮元数据），不是运行结果；它随包交付，可以用包内文件复算验证。
`matrix/runs/` 与 `matrix/venvs/`（真实运行时的运行根与自建 venv）当前为空，未纳入归档。

## 本包**不能**支持什么

- 不能支持"矩阵已经跑过"。本轮没有执行任何候选测试、K01—K08、恢复块或义务见证；
  退出码、收集数、逐测试结果在包内都不存在。
- 不能支持"D1—D7 已被用户采纳"。协议里它们仍是 `decided=false`、
  `status=reviewer_proposal_pending_user_acceptance`。
- 不能支持"矩阵已授权"。`final_matrix_frozen`／`formal_protocol_frozen`／
  `matrix_phase_authorized` 三项仍为 false，`run_matrix.py --run` 退出码 2 且不导入候选。
- 不能支持"Babel 见证已通过"。只做了冻结工件的静态契约核对，
  `runtime_value_verified=false`。
- 不能支持"矩阵驱动已在真实候选上验证"。执行路径已实现并被合成测试与预检约束，
  但它的**第一次真实观察就是核心矩阵首轮**；这一点写进了协议的适用性声明。

**撰写：ClawsGO Science Agent**
"""


# 生成到 VERIFY.md 里的字面反斜杠 + n：f-string 里必须写成不经过转义的形式，
# 否则收件人拿到的片段会在引号中间断行（上一版就是这样发的）。
NEWLINE_ESCAPE = chr(92) + "n"


def verify_doc(name):
    return f"""# 逐项复核指引

三步，全部只读。

## 1. 归档清单

```bash
python3 - <<'PY'
import hashlib, json, zipfile, pathlib
bundle = pathlib.Path("{name}.zip")
zf = zipfile.ZipFile(bundle)
manifest = json.loads(zf.read("ARCHIVE_MANIFEST.json"))
bad = [m["path"] for m in manifest["members"]
       if hashlib.sha256(zf.read(m["path"])).hexdigest() != m["sha256"]]
payload = "".join(f"{{m['path']}}:{{m['sha256']}}{NEWLINE_ESCAPE}"
                   for m in sorted(manifest["members"], key=lambda x: x["path"]))
print("members:", manifest["member_count"], "mismatch:", bad)
print("aggregate matches:",
      hashlib.sha256(payload.encode()).hexdigest() == manifest["archive_aggregate_hash"])
PY
```

## 2. 一键复算

把包解压到任意目录后：

```bash
python3 matrix/tooling/run_all_checks.py
```

五项检查全过即以 0 退出。要看某一项的细节可以直接跑：

```bash
python3 matrix/tooling/check_matrix_protocol.py      # 结构化一致性（按协议声明的阶段）
python3 matrix/tooling/run_matrix.py --check --plan  # 驱动预检与 270 次计划
python3 matrix/tooling/test_matrix_rules.py          # 合成测试（含反例）
python3 matrix/tooling/seal_signing_anchors.py       # 签署时重算文档锚点（默认 dry-run）
python3 matrix/tooling/bind_source_fingerprints.py   # 重新绑定指纹（需本机工件）
```

## 3. 独立判断的重点

- `matrix/protocol/matrix_protocol_v0.4_reviewed.md` 与 `.json` 是否**逐字节等于**校订包的对应成员——
  `checks/run_all_checks.json` 的 `adopted_files_match_review_manifest` 给出结论，
  也可以拿校订包自行对 SHA-256。
- **签发之后**这两份文件会合法地改变：`protocol_edits_within_signing_scope` 会拿
  `matrix/protocol/original_reviewed/`（逐字节等于校订包的原件副本）做基准，逐路径核对差异
  是否只落在签署字段上，并把越界路径列出来。签署态下这两项检查仍应为绿。
- `ARCHIVE_MANIFEST.json` 的组合指纹能否按**路径排序**由包内成员复算出来；
- `matrix/freeze/matrix_pre_run_commitment.json` 的指纹能否由包内文件复算出来
  （`matrix/tooling/run_all_checks.py` 的最后一项）。
- `matrix/bindings/matrix_freeze_bindings.json` 里 `babel_witness_contract` 的每一条是否只是
  **静态**核对（`runtime_value_verified` 应为 false）。
- `checks/driver_preflight_and_plan.json` 的 `checks_offline_skipped` 是否与
  `workspace_available=false` 相符——离线跳过的项不应被算作通过；**签署态**下这三个实体项
  不允许跳过（`require_workspace = for_execution or phase == "signed"`）。
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="PyHost_MatrixV04Implementation_20261014")
    parser.add_argument("--out", default=str(ROOT.parent / "deliverables"))
    args = parser.parse_args()

    # ---- 0. 事前承诺必须与当前文件一致，否则不打包
    stored_path = MATRIX / "freeze" / "matrix_pre_run_commitment.json"
    stored = json.loads(stored_path.read_text(encoding="utf-8"))["pre_run_commitment_hash"]
    recomputed = commitment_mod.compute_commitment()["pre_run_commitment_hash"]
    if stored != recomputed:
        print(json.dumps({"error": "commitment_is_stale",
                          "stored": stored, "recomputed": recomputed,
                          "hint": "先运行 python3 matrix/tooling/run_matrix.py --write-commitment"},
                         ensure_ascii=False, indent=2))
        return 1

    # ---- 1. 三项检查输出
    checks = {}
    python = sys.executable
    spec, result = run([python, str(MATRIX / "tooling" / "check_matrix_protocol.py")],
                       "protocol_consistency")
    spec["ok"] = result.returncode == 0
    checks["checks/protocol_consistency.json"] = result.stdout.encode("utf-8")
    spec2, result2 = run([python, str(MATRIX / "tooling" / "run_matrix.py"), "--check", "--plan"],
                         "driver_preflight_and_plan")
    spec2["ok"] = result2.returncode == 0
    checks["checks/driver_preflight_and_plan.json"] = result2.stdout.encode("utf-8")
    test_specs = []
    for script, label in (("test_matrix_rules.py", "synthetic_rules_tests"),
                          ("test_matrix_integration.py", "synthetic_integration_tests")):
        spec_tests, result_tests = run([python, str(MATRIX / "tooling" / script)], label)
        spec_tests["ok"] = result_tests.returncode == 0
        spec_tests["summary_line"] = (result_tests.stderr.strip().splitlines()[-1]
                                      if result_tests.stderr.strip() else "")
        checks[f"checks/{label}.txt"] = (result_tests.stdout + result_tests.stderr).encode("utf-8")
        test_specs.append(spec_tests)
    spec4, result4 = run([python, str(MATRIX / "tooling" / "run_all_checks.py")], "run_all_checks")
    spec4["ok"] = result4.returncode == 0
    checks["checks/run_all_checks.json"] = result4.stdout.encode("utf-8")
    summaries = [spec, spec2] + test_specs + [spec4]
    if not all(entry["ok"] for entry in summaries):
        print(json.dumps({"error": "checks_failed", "summaries": summaries},
                         ensure_ascii=False, indent=2))
        return 1

    # ---- 2. 成员
    members = collect()
    payloads = {name: path.read_bytes() for name, path in members.items()}
    payloads.update(checks)
    manifest = {
        "schema_version": "pyhost-context/matrix-bundle-manifest/1.0",
        "bundle": args.name,
        "created_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "member_count": len(payloads),
        "payload_count": len(payloads),
        "members": [{"path": name, "bytes": len(data), "sha256": sha256_bytes(data)}
                    for name, data in sorted(payloads.items())],
        "algorithm": ("members sorted by path, joined as '{path}:{sha256}\\n' → single SHA-256; "
                      "算法说明与下方组合指纹由同一函数计算"),
        "authorization": {"matrix_phase_authorized": False, "matrix_started": False,
                          "formal_protocol_frozen": False},
        "scope": ("v0.4 校订的落实与复算包；没有执行任何候选测试，不含运行结果。"),
        "check_summaries": summaries,
        "excluded_dirs": sorted(EXCLUDE_DIRS),
        "aggregate_scope": ("archive_aggregate_hash 覆盖 members 中的全部成员"
                            "（含 README.md 与 VERIFY.md），拼接前按 path 排序；"
                            "包内 README 不再引用该值，以免自我引用。"),
    }
    def aggregate(members):
        """组合指纹：先按 path 排序再拼接——算法说明与实现必须一致（实现审计第 3 节）。"""
        return sha256_bytes("".join(f"{m['path']}:{m['sha256']}\n"
                                    for m in sorted(members, key=lambda m: m["path"])).encode())

    # README 里写的成员数与组合指纹必须是**最终**值，因此先把 README/VERIFY 计入成员表。
    placeholder = {"path": "README.md", "bytes": 0, "sha256": ""}
    verify_entry = {"path": "VERIFY.md", "bytes": 0, "sha256": ""}
    manifest["members"] = sorted(manifest["members"] + [placeholder, verify_entry],
                                 key=lambda m: m["path"])
    manifest["member_count"] = len(manifest["members"])
    manifest["archive_aggregate_hash"] = aggregate(
        [m for m in manifest["members"] if m["path"] not in ("README.md", "VERIFY.md")])

    readme_text = readme(args.name, payloads, manifest, checks, stored, summaries)
    verify_text = verify_doc(args.name)
    for entry, text in ((placeholder, readme_text), (verify_entry, verify_text)):
        entry["bytes"] = len(text.encode("utf-8"))
        entry["sha256"] = sha256_bytes(text.encode("utf-8"))
    manifest["archive_aggregate_hash"] = aggregate(manifest["members"])

    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = out_dir / f"{args.name}.zip"
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in sorted(payloads.items()):
            zf.writestr(name, data)
        zf.writestr("README.md", readme_text)
        zf.writestr("VERIFY.md", verify_text)
        zf.writestr("ARCHIVE_MANIFEST.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    digest = sha256_bytes(bundle_path.read_bytes())
    (out_dir / f"{args.name}.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"bundle": str(bundle_path), "bytes": bundle_path.stat().st_size,
                      "members": manifest["member_count"],
                      "aggregate": manifest["archive_aggregate_hash"],
                      "bundle_sha256": digest,
                      "commitment": stored}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
