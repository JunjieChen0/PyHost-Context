"""ClawsGO Science Agent：工具链静态准备包的打包。

收集九份冻结 sdist、pdm M1 原件、声明表、附件、提案、更正确认与复算脚本；
写 README 与 MANIFEST 后压缩。不执行候选、不安装、不联网。
"""
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
EXT = NEXT.parent
ROOT = EXT.parent
DELIVER = ROOT.parent / "deliverables"
NAME = "PyHost_NextExperiment_Toolchain_Preparation_20261018"
STAGE = DELIVER / NAME
ZIP = DELIVER / f"{NAME}.zip"

SDISTS = {
    "ipython": "ipython-9.17.1.tar.gz",
    "structlog": "structlog-26.1.0.tar.gz",
    "rich-toolkit": "rich_toolkit-0.20.5.tar.gz",
    "pylint": "pylint-4.1.1.tar.gz",
    "twine": "twine-7.0.0.tar.gz",
    "rich-click": "rich_click-1.9.9.tar.gz",
    "argparse": "argparse-1.4.0.tar.gz",
    "hatch": "hatch-1.18.1.tar.gz",
    "pdm": "pdm-2.29.2.tar.gz",
}


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def copy(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def main():
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)

    copy(NEXT / "TOOLCHAIN_AMENDMENT_PROPOSAL_20261018.md", STAGE / "TOOLCHAIN_AMENDMENT_PROPOSAL_20261018.md")
    copy(NEXT / "TOOLCHAIN_PROPOSAL_ANNEX_20261018.md", STAGE / "TOOLCHAIN_PROPOSAL_ANNEX_20261018.md")
    copy(NEXT / "ERRATA_ACKNOWLEDGED_20261018.md", STAGE / "ERRATA_ACKNOWLEDGED.md")
    copy(NEXT / "DECLARED_TOOLCHAINS_20261018.json", STAGE / "DECLARED_TOOLCHAINS_20261018.json")
    copy(NEXT / "toolchain" / "EXISTING_BYTES_REQUEST.json", STAGE / "EXISTING_BYTES_REQUEST.json")
    for project, filename in SDISTS.items():
        copy(NEXT / "state" / "artifacts" / project / filename, STAGE / "sdists" / filename)
    m1 = NEXT / "state" / "runs" / "next-admission-20261018" / "pdm" / "attempt-001"
    copy(m1 / "commitment-M1.json", STAGE / "records/pdm/commitment-M1.json")
    copy(m1 / "commitment.json", STAGE / "records/pdm/commitment.json")
    for script in ("analyze_declared_toolchains.py", "render_toolchain_annex.py",
                   "verify_toolchain_preparation.py"):
        copy(NEXT / "toolchain" / script, STAGE / "tools" / script)

    readme = """# PyHost-Context：九项机制候选的声明工具链静态准备

本包只做只读静态准备：导出九份既有冻结 sdist，列出每项的声明测试依赖、
PEP 735 依赖组包含关系、pytest 约束与插件配置，并给出一次统一的工具链修订提案草案。

**本包不构成执行授权。** 提案为 DRAFT，未生效；S1b/S2/S3/repro 仍未授权。
不导入候选、不执行构建或 resolver、不安装、不联网。

## 先读

1. `TOOLCHAIN_AMENDMENT_PROPOSAL_20261018.md`：D 档修订提案草案与需要 Reviewer 001 决定的事项。
2. `TOOLCHAIN_PROPOSAL_ANNEX_20261018.md`：九项的逐项静态清单。
3. `ERRATA_ACKNOWLEDGED.md`：对 S1a 结果回执六项更正的追加确认。
4. `DECLARED_TOOLCHAINS_20261018.json`：机器可读的完整解析结果。

## 关键静态结论

- 九项中没有任何一项能在“仅用既有本地字节”的条件下完成全部测试作用域的精确钉。
- 硬性 pytest 下限：ipython（经 `pytest-asyncio>=1.0`）≥8.2/8.4、rich-toolkit ≥8.3.2、
  pylint ≥8.4、rich-click ≥8.3.5；这些与共同栈的 7.2.0 不相容。
- twine 与 pdm 的可行性取决于所选插件钉；pdm 的 `pytest-httpx` 0.36.2 要求 pytest 9.x。
- argparse 的失败与工具链无关：回移包在 3.11 下遮蔽标准库 `argparse`。
- hatch 的 sdist 不含测试依赖声明（`.github` 被排除），D 档无法据声明重建。
- 依赖组包含关系与自引用展开已逐项记录，例如 structlog `dev ⊃ tests`、pdm `pdm[pytest] → pytest, pytest-mock`。

## 内容结构

| 路径 | 内容 |
|---|---|
| `sdists/` | 九份冻结 sdist，逐件与请求哈希和字节数一致 |
| `records/pdm/` | pdm 的 `commitment-M1.json` 原件（补交）与初始承诺 |
| `DECLARED_TOOLCHAINS_20261018.json` | 每项的声明来源、组包含、pytest 约束、插件入口点、钉状态 |
| `tools/` | 解析、附件渲染与自检脚本 |
| `TOOLCHAIN_PREPARATION_VERIFICATION.json` | 打包前自检结果 |

## 复算

```bash
python3 tools/verify_toolchain_preparation.py --package .
# 生成脚本随包提供，但需在工作区布局（含本地 wheel 索引）运行才能重建钉状态：
#   tools/analyze_declared_toolchains.py
#   tools/render_toolchain_annex.py
```

第一条核对九份字节、pdm M1 原始承诺、声明表与提案不变量；不执行候选、不联网。
"""
    (STAGE / "README.md").write_text(readme, encoding="utf-8")

    # 先在暂存目录自检，把结果一并打包。
    result = subprocess.run([sys.executable, str(STAGE / "tools" / "verify_toolchain_preparation.py"),
                             "--package", str(STAGE)], capture_output=True, text=True)
    (STAGE / "verification_stdout.txt").write_text(result.stdout, encoding="utf-8")
    (STAGE / "verification_stderr.txt").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        raise SystemExit("pre-package verification failed")

    files = sorted(path for path in STAGE.rglob("*") if path.is_file())
    manifest = {
        "schema_version": "pyhost-context/toolchain-preparation-manifest/1.0",
        "count": len(files),
        "sdist_bytes": sum((STAGE / "sdists" / name).stat().st_size for name in SDISTS.values()),
        "files": {path.relative_to(STAGE).as_posix():
                  {"bytes": path.stat().st_size, "sha256": sha(path)}
                  for path in files},
    }
    (STAGE / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if ZIP.exists():
        ZIP.unlink()
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(STAGE.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(STAGE).as_posix())
    with zipfile.ZipFile(ZIP) as archive:
        bad = archive.testzip()
        members = len(archive.namelist())
    print(json.dumps({"zip": str(ZIP), "members": members, "bytes": ZIP.stat().st_size,
                      "sha256": sha(ZIP), "zip_integrity": bad is None,
                      "manifest_count": len(files)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
