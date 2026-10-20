"""ClawsGO Science Agent：把准入阶段的原始证据打成可独立核验的证据包。

原则（对应独立审计 F03/F04 的要求）：

- **运行前承诺**（``admission_freeze/pre_run_commitment.json``）与**运行后归档清单**
  （``ARCHIVE_MANIFEST.json``）分开保存，前者只覆盖规范文件，后者覆盖包内全部成员；
- 归档清单给出逐成员字节数与 SHA-256，且**同时**给出组合指纹与算法，便于独立重算；
- 运行器源码与 observer 源码随包提供（审计 F03 指出上一版缺 10 份运行器源码）；
- 包内 ``SCOPE.md`` 明写哪些结论**不能**由本包支持（工件字节、授权记录、网络强制隔离）。

用法：``python3 tooling/build_admission_bundle.py <输出 zip 路径>``
"""
import hashlib
import json
import pathlib
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent  # pyhost-context/
TREE_DIRS = ("admission_freeze",)

TOOLING_FILES = (
    "tooling/run_admission_baseline.py",
    "tooling/acquire_artifacts.py",
    "tooling/verify_source_tree.py",
    "tooling/restore_source_tree.py",
    "tooling/verify_bundle.py",
    "tooling/build_admission_bundle.py",
    "tooling/check_protocol_consistency.py",
    "tooling/admission_runner/__init__.py",
    "tooling/admission_runner/env_state.py",
    "tooling/admission_runner/timeout_ledger.py",
    "tooling/admission_runner/pytest_jsonl.py",
    "tooling/admission_runner/admission_gate.py",
    "tooling/admission_runner/b0_runner.py",
    "tooling/admission_runner/run_b0.py",
    "tooling/admission_runner/observer_install.py",
    "tooling/admission_runner/netwatch_sitecustomize.py",
    "tooling/admission_runner/commitment.py",
    "tooling/admission_runner/reconstruct_accounting.py",
    "tooling/admission_runner/integration/jsonl_producer.py",
    "tooling/admission_runner/integration/pipeline.py",
)

# 最终矩阵协议（草案/历史版本），随包交付以便与准入证据一并审阅
PROTOCOL_FILES = (
    "最终矩阵协议冻结草案_v0.3待校正.md",
    "protocol_freeze_v0.3待校正.json",
    "冻结协议草案_v0.2待校正.md",
    "protocol_freeze_v0.2待校正.json",
)

RECORD_FILES = (
    "admission_rules_v0.1.json",
    "admission_status.json",
    "admission_candidate_metadata_probe_v0.2.json",
    "准入阶段规则草案_v0.1.md",
)

RUN_FILES = ("candidate_result.json", "pre_run_file_state.json", "post_run_file_state.json")
LOG_PATTERNS = ("round*.jsonl", "round*.launch_env.json", "round*.boot_env.json",
                "round*.meta.json", "round*.file_state.json", "round*.net.jsonl",
                "round*.stdout.txt", "round*.stderr.txt")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def collect():
    """收集待归档成员：``{包内路径: 绝对路径 或 内联字节}``。"""
    members = {}

    def add(archive_path, source_path):
        source_path = pathlib.Path(source_path)
        if source_path.is_file():
            members[archive_path] = source_path

    for tree in TREE_DIRS:
        for path in sorted((ROOT / tree).rglob("*")):
            if path.is_file():
                add(f"freeze/{path.relative_to(ROOT / tree).as_posix()}", path)
    for rel in TOOLING_FILES:
        add(f"tooling/{pathlib.Path(rel).name if 'admission_runner' not in rel else rel.split('admission_runner/')[1]}", ROOT / rel)
    for rel in RECORD_FILES:
        add(f"records/{pathlib.Path(rel).name}", ROOT / rel)

    for version in ("v3", "v4"):
        base = ROOT / f"admission_freeze/admission_result_{version}.json"
        if base.is_file():
            add(f"records/admission_result_{version}.json", base)

    for rel in PROTOCOL_FILES:
        add(f"protocol/{pathlib.Path(rel).name}", ROOT / rel)

    historical = ROOT / "historical_source_at_run"
    if historical.is_dir():
        for path in sorted(historical.rglob("*")):
            if path.is_file():
                add(f"historical_source_at_run/{path.relative_to(historical).as_posix()}", path)

    archive = ROOT / "admission_runs_archive"
    if archive.is_dir():
        for path in sorted(archive.rglob("*")):
            if path.is_file():
                add(f"archived_runs/{path.relative_to(archive).as_posix()}", path)

    runs = ROOT / "admission_runs"
    if runs.is_dir():
        for candidate in sorted(runs.iterdir()):
            if not candidate.is_dir():
                continue
            for rel in RUN_FILES:
                add(f"runs/{candidate.name}/{rel}", candidate / rel)
            for pattern in LOG_PATTERNS:
                for path in sorted((candidate / "logs").glob(pattern)):
                    add(f"runs/{candidate.name}/logs/{path.name}", path)
    return members


def readme(payloads, bundle_name=""):
    """包内一页式摘要：结论、执行修正、样本、目录导航、复算方法、核验边界。

    整段一次性生成，避免用字符串替换拼接导致旧段落残留（v6 审计发现 README 出现重复段落）。
    """
    result = json.loads(payloads["records/admission_result_v4.json"])
    ok = result["outcome"]
    rows = []
    for name, f in result["per_candidate_run_facts"].items():
        rows.append(f"| {name} | {f['status']} | {f['round_duration_seconds_range']} | "
                    f"{f['cache_files_removed_total']} | "
                    f"{f['network_pytest_process_covered_rounds']}/5 | "
                    f"{f['non_loopback_connection_denied_total']} |")
    accounting = json.loads(payloads["freeze/round1_accounting.json"])
    total_collected = accounting["total_collected_first_round"]
    tq = result["tqdm_reproducibility_observation"]
    sel = result["sample_selection_open_decision"]
    hist = chr(10).join(
        f"| {h['run']} | {h['executor']} | {h['outcome']} | {h['mechanism']} |"
        for h in tq["history_of_the_day"])
    version = bundle_name or "（未命名）"
    return f"""# PyHost-Context 准入证据包 —— {version}

研究：固定软件栈下 Python 测试的进程环境敏感性（受控实证研究）。
阶段：**A 阶段准入基线**。未执行任何 K01—K08 扰动条件；最终矩阵未冻结；矩阵授权未取得。

## 一句话结论

在准入执行器下，8 个候选各跑 5 轮 B0 基线：**{ok['candidates_passed']} 个通过、{ok['candidates_failed']} 个未通过**，
全部 8 个候选 5 轮逐测试结果向量完全一致。首轮收集合计
{total_collected} 个测试项。

## 逐候选运行事实

| 候选 | 门判定 | 单轮耗时(s) | 清字节码缓存 | 被测进程网络观察器覆盖 | 非环回拒绝 |
|---|---|---|---|---|---|
{chr(10).join(rows)}

全部 40 轮：`launch_env` / `post_init_env` / `pytest 进程内快照` 三视图对全部 29 个声明键一致（差异 0）；
每轮起始源码树哈希逐轮相同；**本次运行未发生超时**。

（注意：不能由此推出「当时组内无残留后代」。运行当时的正常退出路径**不检查**组内残留，
该检查是事后修订才加入的机制；旧记录没有该字段。见 `freeze/executor_revision_after_run.json`。）

## 样本选择（按 v4 独立审计裁决）

按运行前冻结规则套用，**P=6 为**：{', '.join(sel['adopted_sample']['selected'])}。
通过但未使用：{', '.join(sel['adopted_sample']['passed_but_unused'])}。

作者曾提出新增「至少两次独立运行均 5/5 通过」的门槛以保留上一版名单，**现已撤回**：
该门槛是看到结果之后提出的，且当日四次运行分属三个不同执行器版本，不能算同一配置的重复——
严格套用它本就推不出上一版名单。若将来确要改变重复要求，应作为协议修订对全部候选一致适用，
并重新收集同一配置的证据。

`tqdm` 基线在本机的可重复性未建立，其当日四次运行记录如下（仅作背景，**不据此改变规则**）：

| 运行 | 执行器 | 结果 | 机制 |
|---|---|---|---|
{hist}

正式矩阵中 `tqdm` 若再出现不一致，应按预注册语义分类并保留数据，不得临时换入 `colorama`。
本次五轮没有不一致，只说明**本次筛查未观察到**不一致。

## 事后修订（发生在本次运行**之后**）

两轮独立审计的合成检查复现出四个问题并已修正：

| 问题 | 修正 |
|---|---|
| 地址判定用文本前缀 `127.` 放行域名（`127.external.invalid`） | IP 用 `ipaddress` 判定；域名只认事前本地名清单 |
| 连接类调用未读 `sock.family`，AF_UNIX **相对路径**被误拒 | `connect`/`connect_ex` 按 `sock.family` 判本地 IPC；解析类只用网络地址规则 |
| 正常退出路径不检查组内残留后代 | 两条路径都检查；有残留即清理并记录 |
| 空目录 `hash=null`、若干范围表述过宽 | 改用空集合标准哈希；按当前执行控制模型重写 SCOPE |

**本次运行的原始文件一律未追改。** 关于修订的影响，只能说：

- 在**所覆盖且已成功记录**的调用中，本次 40 轮没有观察到任何地址判定事件（每轮 net 日志只有
  init/end，各判定计数全为 0）；这**不**等于所有进程的所有相关路径都从未执行，
  辅助进程的 init/end 并不全部成对。
- 原稿曾写「修订不可能改变任何观察」，**该说法过强、已撤回**。关键理由：本次运行当时
  **不检查组内残留**，而新增的检查与清理可能影响跨轮初始状态、文件写入与调度。
  旧记录里「未触发清理」只表示**旧版没有检查**，不能推出组内当时没有残留。
- 结论：历史 B0 结果可以保留并用作样本依据；**不得**追认为修订后的执行器已被验证。
  修订后的执行器需要独立批次记录——核心矩阵自身含 6 个项目各 5 轮 B0，其基线即新机制下的首批真实观察。

运行**当时**实际执行的运行器源码随包保存在 `historical_source_at_run/`（含 PROVENANCE 与逐文件哈希），
因此**在 v6 单包内**即可核验全部 26 项原承诺：以历史文件替代已修订的对应项、其余采用当前未变更的文件即可。
原 `pre_run_commitment` 保持不变。

## 附带的最终矩阵协议草案

`protocol/最终矩阵协议冻结草案_v0.3待校正.md` 与 `protocol/protocol_freeze_v0.3待校正.json`
把准入阶段的实际产物填入最终矩阵协议。它们是**待校正草案**，其中八项（D1—D8）尚未决定，
矩阵仍为未授权状态。两份表述的一致性可由 `tooling/check_protocol_consistency.py` 复算。

## 目录导航

| 路径 | 内容 |
|---|---|
| `README.md` | 本文件 |
| `SCOPE.md` | 本包能支持与**不能**支持的结论（请先读） |
| `ARCHIVE_MANIFEST.json` | 运行后归档清单：逐成员字节数与 SHA-256，附组合算法 |
| `freeze/` | 冻结记录：运行前承诺、执行控制模型、事后修订记录、B0 环境、逐候选条件、记账、跳过原因、历版结果 |
| `records/` | 规范文件与状态记录（规则、候选元数据、授权声明、v3/v4 结果） |
| `runs/<候选>/` | 本次运行的 `candidate_result.json`、运行前后文件状态、`logs/` |
| `runs/<候选>/logs/roundN.*` | 原始 JSONL、启动环境三视图、每轮元数据、初始状态、完整 stdout/stderr、网络判定日志 |
| `archived_runs/` | 历史运行的保留记录（非规范启动环境版本，自早期证据包恢复） |
| `historical_source_at_run/` | 本次运行**当时**实际执行的运行器源码与执行控制模型，附来源说明与逐文件哈希 |
| `protocol/` | 最终矩阵协议冻结草案 v0.3（Markdown + 机器文件）与其前身 v0.2，供与准入证据一并审阅 |
| `tooling/` | 全部运行器、observer 与只读复算脚本 |

## 如何自行复算（只读，不安装任何东西）

```bash
python3 tooling/verify_bundle.py <本 zip>
```

只解压到内存、不执行任何候选项目代码，复算：

- 归档清单逐成员哈希与组合指纹；
- 每轮逐测试结果向量、身份指纹与准入判定（用包内 `admission_gate.py` 重判）；
- 启动环境三视图一致性、每轮起始状态（源码树 / home / tmp）；
- **事后修订记录的自洽性**：用包内当前文件复算修订后的规范指纹，
  并用 `historical_source_at_run/` 替代被修订成员后复算原运行前承诺指纹——
  两个指纹都能在**本单包内**核验。

本包构建时已自检：判定 `consistent`，无问题项。当前自检复算结果：
修订后指纹 `c432c0cf…`、原承诺指纹 `f1c3bc56…` 均匹配。

**该脚本的边界**：它不检测文档内部是否自相矛盾，也不能替代对工件字节、授权会话或
目标解释器版本的独立核验。不要把它的 `consistent` 当作超出上述范围的保证。

## 本包**不能**支持什么

工件字节（wheel/sdist 与源码树不在包内）、授权会话记录、内核级网络隔离、非 Python 子进程的
网络行为、venv 字节码缓存与宿主机缓存/负载——详见 `SCOPE.md`。本包不含 K01—K08 任何一轮。
"""


def main(argv):
    out = pathlib.Path(argv[0] if argv else ROOT.parent / "deliverables/PyHost_AdmissionEvidence_v3.zip")
    out.parent.mkdir(parents=True, exist_ok=True)
    members = collect()

    payloads = {}
    for archive_path, source in members.items():
        payloads[archive_path] = pathlib.Path(source).read_bytes()

    payloads["README.md"] = readme(payloads, out.name).encode("utf-8")

    scope = """# 本证据包的核验范围（请先读）

## 包内支持什么

- 40 轮（8 候选 × 5 轮）pytest 每一轮的**原始 JSONL**，含逐测试 setup/call/teardown 的
  原生 outcome / longrepr / wasxfail，以及 collection 身份与顺序。
- 每一轮的**启动环境三视图**：`roundN.launch_env.json`（交给子进程的精确字典）、
  `roundN.boot_env.json`（子进程初始化后自报的 `os.environ`）、以及 JSONL 里
  `RuntimeSnapshot.environment`（pytest 进程内视图）。
- 每一轮的**初始状态**：源码树（按要求逐字节恢复并复核与 sdist 一致）、`home`、`tmp`
  三项的起始哈希，以及本轮开始前从冻结 sdist 写回/补回/删除/清缓存的具体条目。
- 每轮**执行与绑定元数据** `roundN.meta.json`：运行前承诺指纹、初始状态哈希、解释器与
  observer 的 SHA-256、实际加载插件清单（含每个插件文件哈希）、capture/TTY/FD 明细、
  进程组清理记录（PID/PGID/信号/清理后组内是否仍有成员）、网络规则与覆盖范围。
- 每轮完整 stdout / stderr，以及**网络观察日志** `roundN.net.jsonl`（含每个进程的
  `observer_init` / `observer_end`）。
- **运行前承诺**（`freeze/pre_run_commitment.json`）与**运行后归档清单**
  （`ARCHIVE_MANIFEST.json`），两者分开，组合算法明示。
- **事后执行器修订记录**（`freeze/executor_revision_after_run.json`）：本轮修正了哪些文件、
  修订后的规范文件指纹，以及**修订影响的说明边界**——该记录明确撤回「不可能改变任何观察」的说法，
  只声明「v4 原始证据未追改；修订后的执行器未经真实候选验证，其影响尚未直接测量」。

## 包内**不**支持什么（不得据本包下结论）

- **工件字节**：16 份 wheel/sdist 与候选源码树不在包内。本包只含获取回执，可核验
  「回执记录的期望哈希与观测哈希相等」，**不能**独立验证 tag→sdist 对应关系、许可证或
  源码未被改动。
- **授权记录**：`records/admission_status.json` 中的授权字段是作者关于会话授权的声明，
  不含可独立对照的签署或会话时间戳。
- **网络**：不是内核级隔离（本机 `unshare -n` 不可用）。强制只覆盖所列的 Python 层调用；
  不覆盖非 Python 子进程、原生库直接系统调用、已建立连接上的读写与内核层其他路径。
  `roundN.net.jsonl` 是按进程记录的生命周期与判定日志；**缺少某条记录**不等于该路径已核验。
  主进程（pytest 子进程，可用 `child_pid` 对应）的 init/end 成对；辅助进程未必成对，
  不得把主进程的完整性扩写成「所有子进程观测完整」。
- **网络判定的语义**：连接类调用按 `sock.family` 判定地址族（AF_UNIX 一律放行，**包括相对路径名**）；
  解析类调用只用网络地址规则（IP 按 `ipaddress`，域名只认事前清单）。这是**分类规则**，
  不是「所有非本地访问都已被拒绝」的保证。本包 runs/ 里 40 轮没有 `local_ipc_allowed` 一类事件，
  说明本次筛查未观察到地址判定事件，不等于所有相关路径都从未执行。
- **状态范围**：受控并留证的只有源码树、`home`、`tmp` 三项。候选 venv 内的 `__pycache__`、
  内核缓存、宿主机全局 `/tmp`、宿主机负载均**不受控**。可声称三项逐轮起始状态相同，
  不可声称全部文件与缓存状态一致。
- **进程组**：超时与正常退出两条路径都做组内检查，但 `group_alive_*` 是**瞬时观测**。
  这是**修订后**的机制：本包 runs/ 里 40 轮的记录**没有**该检查字段，运行当时的正常退出路径
  不检查组内残留——「未超时」与「当时组内无残留」不能互相推出。
- **扰动条件**：本包不含 K01—K08 任何一轮；扰动条件从未执行。
"""
    payloads["SCOPE.md"] = scope.encode("utf-8")

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(payloads):
            info = zipfile.ZipInfo(path, date_time=(2026, 10, 14, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, payloads[path])

    manifest = {
        "schema_version": "pyhost-context/archive-manifest/1.0",
        "kind": "运行后归档清单（不是运行前承诺）",
        "bundle": out.name,
        "member_count": len(payloads),
        "total_bytes": sum(len(v) for v in payloads.values()),
        "members": [{"path": p, "bytes": len(payloads[p]), "sha256": sha256_bytes(payloads[p])}
                    for p in sorted(payloads)],
        "aggregate_algorithm": ("对每个成员取 \"{path}:{sha256}\\n\"，按 path 字节序升序拼接为 "
                                "UTF-8 字节串，再做一次 SHA-256"),
    }
    payload = "".join(f"{m['path']}:{m['sha256']}\n" for m in manifest["members"])
    manifest["archive_aggregate_hash"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    with zipfile.ZipFile(out, "a", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ARCHIVE_MANIFEST.json", manifest_bytes)
    out.with_suffix(".manifest.json").write_bytes(manifest_bytes)

    print(json.dumps({"bundle": str(out), "members": len(payloads),
                      "bytes": out.stat().st_size,
                      "sha256": sha256_bytes(out.read_bytes()),
                      "archive_aggregate_hash": manifest["archive_aggregate_hash"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
