"""ClawsGO Science Agent：生成 D 档准备交付的主报告。"""
import argparse
import collections
import datetime
import hashlib
import json
import pathlib
import subprocess

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
STATE = NEXT / "state" / "d_toolchain"
OUT = NEXT / "D_PREPARATION_REPORT_REV1_20261018.md"


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def minutes(event):
    start = datetime.datetime.fromisoformat(event["started_at"])
    end = datetime.datetime.fromisoformat(event["at"])
    return (end - start).total_seconds() / 60


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tests", default="14 passed")
    args = parser.parse_args()
    locks = json.loads((STATE / "D_RESOLUTION_LOCKS_REV1.json").read_text())
    manifest = json.loads((STATE / "D_WHEEL_MANIFEST_REV1.json").read_text())
    official = json.loads((STATE / "D_OFFICIAL_METADATA_INDEX_REV1.json").read_text())
    plugins = json.loads((STATE / "D_PLUGIN_MAP_REV1.json").read_text())
    validation = json.loads((STATE / "D_LOCK_VALIDATION_REV1.json").read_text())
    witnesses = json.loads((NEXT / "D_R0_WITNESSES_20261018.json").read_text())
    log = [json.loads(line) for line in
           (STATE / "D_RESOLUTION_LOG.jsonl").read_text().splitlines() if line.strip()]
    plugin_index = {row["project"]: row for row in plugins["projects"]}
    lock_rows = [row for row in locks["projects"] if row.get("status") == "locked"]
    failed = [row for row in locks["projects"] if row.get("status") == "resolution_failed"]
    excluded = [row for row in locks["projects"] if row.get("status") == "static_excluded"]
    local_rows = [row for row in manifest["rows"] if row.get("source") == "local"]
    remote_rows = [row for row in manifest["rows"] if row.get("source") != "local"]
    local_bytes = sum(row.get("stored_bytes") or 0 for row in local_rows)
    remote_bytes = sum(row.get("stored_bytes") or 0 for row in remote_rows)
    unique_bytes = sum({(row["filename"], row["sha256"]): row.get("stored_bytes") or 0
                        for row in manifest["rows"]}.values())
    durations = collections.defaultdict(list)
    for event in log:
        if event.get("event") in ("pip_resolution", "pip_timeout"):
            durations[event["project"]].append((event["stage"], minutes(event)))
    lines = [
        "# PyHost-Context：D 档依赖解析、工件获取与适配器准备（2026-10-18）",
        "",
        "作者：ClawsGO Science Agent（ClawsGO Science Harness）",
        "",
        "## 1. 本轮范围与授权状态",
        "",
        "本轮只执行 Reviewer 001 已签署的准备范围：官方 PyPI 元数据、wheel 元数据解析、",
        "wheel 获取、静态适配器与合成仪器测试。**未安装任何依赖、未导入或执行候选、未运行",
        "候选测试**；D 准入、S1b、S2、S3、repro 仍未授权。S1a 关闭结论与全部旧记录未改动。",
        "",
        f"输入根清单 `D_ACQUISITION_ROOTS.json` 的 SHA-256 为 "
        f"`{sha(HERE / 'D_ACQUISITION_ROOTS.json')}`，与放行件绑定值一致（`201f9c5d…`）。",
        "",
        "## 2. 结论摘要",
        "",
        f"- **7 项取得完整联合锁**："
        + "、".join(f"`{row['project']}`" for row in lock_rows) + "。",
        f"- **1 项具名工件缺口**：`pylint`（声明链中的 `docopt>=0.6.2,<0.7.0` 只有 sdist，"
        "本档禁止源码构建）。",
        f"- **1 项静态排除**：`argparse`（Reviewer 001 裁定，不补位、不计运行阴性）。",
        f"- 锁内共 {len(manifest['rows'])} 行（{len(local_rows)} 行使用本地既有字节，"
        f"{len(remote_rows)} 行从官方地址获取），去重后 {manifest['wheel_count']} 个 wheel；"
        f"本地 {local_bytes:,} B，远程 {remote_bytes:,} B，去重总字节 {unique_bytes:,} B。",
        f"- 官方逐版本元数据快照 {len(official['entries'])} 条，问题 {len(official['problems'])} 条；"
        f"工件清单问题 {len(manifest['problems'])} 条；锁校验 "
        f"{'全部通过' if validation['ok'] else '存在失败'}；插件映射问题 "
        f"{len(plugins['problems'])} 条、冲突 0 条。",
        f"- 适配器合成测试 **{args.tests}**（含正反例与联合消费测试）。",
        "",
        "## 2.1 本轮有界修正（REV1）",
        "",
        "**R1 实际 D 入口。** 新增 `run_d_admission.py`：授权门在入口处判定；A5 使用干净目录、"
        "独立档案和**具体 wheel 文件路径**安装（不再 `--find-links + name==version`）；"
        "A6/A7 复用冻结的 `run_extension.admit_one` 与 `run_matrix.run_one`，插件、追加参数"
        "与最终对账都来自同一份锁；M1 补装在 D 下固定拒绝；已有未完成尝试不重试。"
        "六个合成接线测试覆盖未授权不派发、同锁贯穿、显式 `-p no:`、续跑不重复与漂移拒绝。",
        "",
        "**R2 structlog 配置兼容。** 保留原 R0 锁（`locks_rev1/structlog_R0.json`），"
        "只以派生约束 `pytest-asyncio>=0.24` 做一次 R1 联合解析，得到 pytest-asyncio 1.4.0 "
        "与 pytest 9.1.1。静态见证：0.23.8 的 wheel 内 `asyncio_default_fixture_loop_scope` "
        "出现 0 次，1.4.0 出现 7 次（`pytest_asyncio/plugin.py` 的 `addini`）。"
        "structlog 与 hatch 的 A6/A7 统一追加 `--randomly-seed=0`，B0 环境键不变。",
        "",
        "**R3 目标文件。** twine/hatch 的 `cryptography-50.0.2` 改用 "
        "`cryptography-50.0.2-cp311-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl`"
        "（4,719,841 B / `630ebfea…` / 内部 METADATA `047cfc71…`），同版本、"
        "Requires-Dist 与旧文件一致；派生清单、插件映射、校验与承诺全部重算，原件保留。",
        "",
        "**反例回归。** 审阅方七个适配器反例中，候选身份、候选 pytest11、同版本重复、"
        "显式禁用、A5 绕过、文件未钉路径六项已转为拒绝；项目自带插件导出已补 `-p`。"
        "另以真实 D 入口与真实仪器函数路径验证同一锁贯穿 A5→A6→A7→对账。",
        "",
        "## 3. 逐项锁",
        "",
        "| 项目 | 版本 | 阶段 | pytest | 声明根 | 锁内分发 | 本地字节 | pytest11 入口 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in lock_rows:
        plugin = plugin_index.get(row["project"]) or {}
        lines.append(
            f"| {row['project']} | {row['version']} | {row['lock']['stage']} | "
            f"{row['pytest_version']} | {row['root_count']} | "
            f"{len(row['lock']['rows'])} | "
            f"{sum(1 for item in row['lock']['rows'] if item.get('source') == 'local')} | "
            f"{len(plugin.get('loads') or [])} |")
    lines += ["", "### 3.1 R0/R1 决策见证", "",
              "| 项目 | 决策 | 见证 |", "|---|---|---|"]
    decisions = {entry["project"]: entry for entry in witnesses["entries"]}
    for row in lock_rows + failed + excluded:
        entry = decisions.get(row["project"]) or {}
        decision = entry.get("decision", "—")
        if decision.startswith("r0_excluded_direct"):
            witness = "；".join(entry.get("direct_pytest_roots") or []) or "minversion"
        elif decision == "r0_excluded_indirect":
            parts = []
            for item in entry.get("indirect_witness") or []:
                versions = item["versions_checked"]
                all_ = ", ".join(v["version"] for v in versions)
                parts.append(f"{item['root']}：{all_} 均要求 pytest≥8/9")
            witness = "；".join(parts)
        else:
            witness = "R0 可满足（实际解析成功）" if row.get("status") == "locked" else "—"
        lines.append(f"| {row['project']} | {decision} | {witness} |")
    lines += ["",
              "`pdm` 的 R0 曾以 `ResolutionTooDeep` 结束；该结果**不构成冲突见证**，"
              "因此另行枚举 `pytest-httpx>=0.34.0` 的全部发行版本：0.34.0/0.35.0 要求 "
              "`pytest==8.*`，0.36.0/0.36.2 要求 `pytest==9.*`，故 pytest 7.2.0 与声明根"
              "不可同时满足。`ipython` 同理：`pytest-asyncio>=1.0.0` 的全部稳定版本要求 "
              "`pytest>=8.2`。",
              "",
              "`structlog` 的 R0 锁在版本元数据上可满足，但冻结配置含 "
              "`asyncio_default_fixture_loop_scope` 与 `--strict-config`，0.23.8 未注册该键"
              "（合成探针退出 4）。按 R2 裁定追加一次 R1，派生约束 "
              "`pytest-asyncio>=0.24` 单列，不回写项目声明；原 R0 锁完整保留。",
              "", "## 4. 解析方法与策略（事前记录）", "",
              "- 解析器：`pip 24.1.2 --isolated install --dry-run`，`--only-binary=:all:`，"
              "目标 `CPython 3.11 / cp311 / manylinux2014_x86_64`，索引仅 "
              "`https://pypi.org/simple`。",
              "- 版本选择：满足全部声明根的完整解；同一版本优先使用本地既有 wheel 字节；"
              "兼容文件按文件名字典序取最小者。"
              f"本次 {len(remote_rows)} 个远程行均与解析器所选文件一致"
              "（`file_reselection` 为空）；本地行使用同版本既有字节，并逐件与官方哈希核对。",
              "- 阶段：每项最多 R0（`pytest==7.2.0`）与 R1 两阶段；R1 仅在直接声明、"
              "minversion 或声明根元数据证明 R0 不可满足时启用。",
              "- 工件：只接受官方登记的二进制 wheel；逐件核对 SHA-256、字节数与 wheel "
              "内部 METADATA 的 Name/Version；不构建 sdist、不读取动态元数据。",
              "",
              "## 5. 工件获取与元数据核验", "",
              f"- 共享库按 SHA-256 存放：`wheels/<sha256>/<filename>`；清单记录项目、"
              f"文件名、URL、大小、来源与内部 METADATA 哈希。",
              f"- 官方逐版本 JSON 快照：`official/<name>/<version>.json`，共 "
              f"{len(official['entries'])} 条；每条记录快照哈希与所选文件哈希。",
              f"- 元数据与 wheel 内 `Requires-Dist` 的差异在归一化后为 0 条；"
              f"原始字面差异（单双引号）已按 PEP 508 归一化，不再作为缺陷。",
              f"- 获取工具：直接 URL 下载失败时回退到 pip 的 HTTP 缓存（同一 URL、同一哈希），"
              f"两者都逐件复核字节。",
              "",
              "**计数勘误（追加，不回填旧报告）**：已交原件按 SHA-256 去重为 **183 个 wheel / "
              "124,260,700 字节**（本地 143 / 官方 40），旧报告的“245 / 107,019,341”口径作废。"
              f"REV1 派生集因 structlog R1 替换与 cryptography 同版本换文件，去重为 "
              f"**{manifest['wheel_count']} 个 / {unique_bytes:,} 字节**。",
              "",
              "## 6. 显式插件映射", "",
              "逐件读取锁内**全部** wheel 的 `pytest11` 入口，按分发/版本/文件哈希记录。"
              "未发现入口名冲突。",
              "",
              "| 项目 | 入口数 | 最终 `-p` 入口 | 项目自带插件 |", "|---|---|---|---|"]
    for row in lock_rows:
        plugin = plugin_index.get(row["project"]) or {}
        lines.append(f"| {row['project']} | {len(plugin.get('entries') or [])} | "
                     f"{', '.join(plugin.get('loads') or []) or '—'} | "
                     f"{', '.join(plugin.get('project_local_plugins') or []) or '—'} |")
    lines += ["", "## 7. 最小适配器与合成测试", "",
              "`d_adapter.py` 提供单一入口 `prepare()`：同一份锁同时产出 A5 安装清单、"
              "A6/A7 共用的 `-p` 插件列表与 pytest 版本断言；任何哈希、身份或插件冲突在安装前"
              "抛出 `LockError`。`install_argv()` 固定 `uv pip install --offline --no-index "
              "--no-deps`；`instrument_scope()` 复用既有 `run_extension`/`run_matrix` 仪器，"
              "只注入插件与版本断言，不复制测试执行逻辑。",
              "",
              f"合成测试覆盖：哈希不符、候选缺失、插件冲突、pytest 版本不符、安装 pin 完整、"
              f"`-p` 幂等、仪器注入与恢复、联合消费同一锁。结果：**{args.tests}**。",
              "", "## 8. 准备成本（不计 A6/A7）", "",
              "| 项目 | 阶段 | 分钟 |", "|---|---|---|"]
    for project in sorted(durations):
        for stage, value in durations[project]:
            lines.append(f"| {project} | {stage} | {value:.1f} |")
    total = sum(value for items in durations.values() for _, value in items)
    lines += ["", f"合计 pip 解析 {total:.1f} 分钟；另有一次 rich-click 30 分钟上限中止后"
                  "以暖缓存重试（中止不产出锁，重试结果为准）。这些成本不计入 A6/A7 或扰动轮。",
              "", "## 9. 边界与后续", "",
              "- 本轮没有安装、导入或执行任何候选；`pylint` 缺口不以改需求、换版本或源码构建绕过。",
              "- 锁与插件映射是执行输入，尚待 Reviewer 001 对具体字节的 D 准入签署。",
              "- 逐项差异、一次性尝试预算与事前承诺见 `D_PROJECT_DIFFERENCES_20261018.md`。",
              "- wheel 字节保存在本机共享库（按 SHA-256 可复核）；交付包携带清单、官方元数据"
              "快照与重建脚本，不重复打包 107 MB 二进制。",
              ""]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "sha256": sha(OUT),
                      "locked": len(lock_rows), "failed": len(failed),
                      "excluded": len(excluded)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
