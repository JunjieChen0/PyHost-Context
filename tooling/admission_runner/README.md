# PyHost-Context 准入阶段 B0 最小运行器

**状态：** 未授权、未冻结、未运行任何候选项目  
**日期：** 2026-10-14  
**撰写：** ClawsGO Science Agent

## 1. 当前授权状态与用途

本目录实现 PyHost-Context 研究 **A 阶段准入** 的 **仅 B0 基线** 最小运行器与合成故障注入测试。

当前机器状态保持：

```text
admission_rules_frozen  = false
admission_phase_authorized = false
admission_started = false
```

因此：

- 本目录**不下载、不安装、不运行任何真实候选项目测试**；
- 只读 B0 入口在 `admission_phase_authorized` 或 `admission_rules_frozen` 非严格布尔 `True` 时**拒绝真实执行**；
- 不修改候选项目源码、测试、fixture 或配置；
- 全部测试均为**合成 fixture**，不涉及任何项目。

对照规则见 [../../准入阶段规则草案_v0.1.md](../../准入阶段规则草案_v0.1.md) 与
[../../admission_rules_v0.1.json](../../admission_rules_v0.1.json)。

## 2. 模块结构

| 文件 | 职责 |
|---|---|
| `env_state.py` | 显式环境三态（`unset`/`empty`/`set(value)`）、空白环境加白名单构造、`launch_env_diff`、文件状态与环境快照分离、直接环境路径键 `runtime_effect=not_applicable` |
| `timeout_ledger.py` | 新进程执行账本；默认 240 秒超时，超时对进程组 `SIGKILL`，保留原始 stdout/stderr、返回码/信号；`run_jsonl` 额外读取 producer 落盘的 native JSONL，返回 `{jsonl, returncode, timed_out}` 结构 |
| `pytest_jsonl.py` | 解析 native pytest 原始 JSONL；原生 `CollectReport` 与选中项 `CollectionItem`/`CollectionFinished` 分离；阶段状态 `reported`/`not_run`/`report_missing` 与逐阶段 raw 明细；`skip`/`xfail`/`XPASS`/严格 `XPASS` 派生标签（只依据 native `wasxfail` 与 `[XPASS(strict)]`）；保守失败签名；`stage_integrity`/`raw_complete`；`root_cause` |
| `admission_gate.py` | 可测试的准入通过函数 `admission_gate`：5 轮完整工件、严格整数 returncode 0、collection 完整、至少一个 call 真正执行、无未预期 fail/error/strictXPASS、身份与结果向量一致；skip/xfail/xpass 须明示 policy |
| `b0_runner.py` | 只读 B0 入口 `run_b0`；授权门 `check_authorized` 最先判（未授权不启动任何进程）；快照 `build_b0_snapshot`（文件状态与启动环境分开，probe/test 入口 argv 分开）；已授权非 dry-run 时经 `timeout_ledger.run_jsonl` 逐轮挂 producer 产出 native JSONL，每轮得到 `{jsonl, returncode, timed_out}` 后调用 `admission_gate`，结果含 `admission_gate` 判定；`resolve_producer` 解析可配置的 producer 插件路径与 `-p` 参数（默认 `integration/jsonl_producer.py`） |
| `run_b0.py` | 只读 B0 入口 CLI；未授权时输出拒绝结果并以非零码退出 |
| `tests/test_admission_runner.py` | 合成故障注入测试（直接构造 JSONL 记录，不经真实 pytest） |
| `integration/jsonl_producer.py` | **producer**：挂到真实 pytest 子进程的插件，如实翻译 native 报告；写 `CollectReport`/`CollectionItem`/`CollectionFinished`/`DeselectedReport`/`TestReport`/`RuntimeSnapshot`/`SessionFinished`；JSONL 独占创建 |
| `integration/pipeline.py` | **observer/parser/summary**：子进程跑真实 pytest、读取落盘 JSONL、复用 parser 并汇总（保留 runtime/session 元信息与 data/collection 错误） |
| `integration/synthetic_suite.py` | 合成套件 fixture 生成器（仅写入临时目录） |
| `integration/test_pipeline_integration.py` | producer→observer→JSONL→parser→summary 端到端集成测试 |
| `integration/test_admission_gate.py` | `admission_gate` 的 5 轮正例/反例端到端测试 |
| `integration/test_b0_runner_gate.py` | `run_b0` 接入 `admission_gate` 的合成测试：未授权不启动任何进程（含已规定 producer 亦拒绝）、授权 + 合成套件走真实链路并返回 gate 判定、gate 通过/不通过两类、producer 路径与 `-p` 可配置 |
| `config_examples/b0_gate.example.json` | 示例配置（授权标志恒为 false） |

关键分离设计：

- **环境三态**：区分 `unset`（键不存在）、`empty`（空串）与 `set(value)`；
- **文件状态与环境快照分离**：`file_state` 只含项目文件哈希，`launch_env_diff` 单独保存，不并入 `file_state_hash`；
- **入口 argv 分离**：`probe_entry_argv` 与 `test_entry_argv` 独立保存，仅比较解释器/启动选项/环境/cwd/FD-TTY 等可比维度；
- **阶段状态**：`setup` 失败或跳过时可确定 `call` 未进入，记为 `not_run`；无法确认是否执行记为 `report_missing`；
- **`identity_changed` 为叠加字段**：可与失败、错误、跳过并存，不覆盖原始结果；
- **严格 XPASS 保留 native 语义**：不改写 `call outcome=failed`、不删除原 `longrepr`、不伪造 `wasxfail`；`xpassed_strict` 仅由 native `[XPASS(strict)]` 派生；`xfailed`/`xpassed` 只依据 native `wasxfail`，故 `xfail(condition=False)` 与 `--runxfail` 不会被误标；
- **失败签名保守**：只替换显式对象 repr 地址（`at 0x....`）与已登记运行目录前缀，其余（含可能是 expected/actual/hash/时长信号的 `0x` 值与 `d.ddds`）一律原样保留；
- **原始报告完整性**：`stage_integrity`/`raw_complete` 标记 stage 重复/不合法/缺失；
- **`root_cause`**：默认 `unresolved`，不自动把超时归类为基础设施故障。

## 3. 运行合成测试

仅运行本目录新建的合成测试，不调用任何候选项目：

```bash
cd pyhost-context/tooling/admission_runner
python3 -m unittest discover -s tests -v
# 或（如环境提供 pytest）
python3 -m pytest tests -v
```

集成测试（真实 pytest 子进程驱动合成套件，不调用任何候选项目）：

```bash
cd pyhost-context/tooling/admission_runner
python3 -m unittest discover -s integration -p 'test_pipeline_integration.py' -v
python3 -m unittest discover -s integration -p 'test_admission_gate.py' -v
# 或
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest integration -q -o addopts=
```

集成测试链路为 producer→observer→JSONL→parser→summary：`jsonl_producer` 插件挂到真实
pytest 子进程产出 native JSONL，`pipeline` 读取并交 `pytest_jsonl.py` 解析、汇总，覆盖
setup 失败/`not_run`、skip/xfail/XPASS（含严格 XPASS）、`xfail(condition=False)`、
`xfail(run=False)`、`--runxfail`、全局 `xfail_strict`、同一测试 call+teardown 同时失败、
collection 错误、deselection、identity 变更与保守失败签名。运行环境自动设置
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`：这既是冻结基线的固定控制项，也隔离与本流水线无关的
第三方自动加载插件。

`admission_gate` 用 5 轮真实工件的路径做准入判定测试，含稳定失败、全 skip、returncode 非 0、
缺 `SessionFinished`、身份漂移等反例。

**版本与范围声明：** 上述结果均在当前机器 **Python 3.12.3 / pytest 9.1.1** 上、只对**合成
套件**运行得到，**不是**任何候选项目的准入验收；冻结基线的 Python 3.11.9 / pytest 7.2 等
目标版本尚未覆盖验证。

### 3.1 独立复跑记录（2026-10-14）

由撰写方在系统 Python 3.12.3 / pytest 9.1.1 下独立复跑，未涉及任何候选项目：

```text
python3 -m unittest discover -s tests           → Ran 34 tests, OK
python3 -m unittest discover -s integration -p 'test_pipeline_integration.py'
                                                → Ran 20 tests, OK
python3 -m unittest discover -s integration -p 'test_admission_gate.py'
                                                → Ran 14 tests, OK
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m unittest integration.test_b0_runner_gate
                                                → Ran 8 tests, OK
```

合计 76 项通过。这些数量是本地复跑结果，不是提交方报告；但仍在上述系统版本下取得，
不能替代冻结目标版本（CPython 3.11.9 / 指定 pytest）下的复验。

示例：只读 B0 入口在未授权时的行为（不会启动进程）：

```bash
python3 run_b0.py --config config_examples/b0_gate.example.json
```

预期：输出 `"status": "refused_not_authorized"`，并以退出码 2 结束。

## 4. 旧 runner 未复用的缺口说明

本运行器**复用**了 `pycompat-env-select/tooling/runner_v021` 的 `subprocess`/`os.wait4` 轮询与进程组 `SIGKILL`
超时思路，但**没有直接修改旧 runner**。旧 runner 不足以直接满足 PyHost-Context 准入规则，主要缺口：

1. **无显式三态环境**：旧 runner 以 `dict(os.environ)` 为基底增删键，无法区分 `unset`/`empty`/`set`，也没有空白环境加白名单构造；
2. **无 `launch_env_diff`**：旧 runner 只记录覆盖/移除键，不做期望规格与实际启动环境的逐键差异比较；
3. **文件状态与启动环境未分离**：旧 runner 的项目哈希与命令记录并列，但没有“启动环境差异不得并入文件状态哈希”的显式结构；
4. **入口 argv 未分离**：旧 runner 以单条 JUnit/命令为单位，未区分 `probe_entry_argv` 与 `test_entry_argv`；
5. **无原始 JSONL 与阶段状态**：旧 runner 只解析 JUnit XML，没有 `reported`/`not_run`/`report_missing` 三阶段状态；
6. **无 `identity_changed` 叠加**：旧 runner 以“跳过集合 + 结果向量”判稳，没有可与失败并存的逐测试身份叠加字段；
7. **无独立派生标签字段**：旧 runner 的 `error` 与 JUnit 状态耦合，没有 `xfailed`/`xpassed`/`xpassed_strict`/`incomplete`/`xfail_summary_unresolved` 独立标签；
8. **失败签名与 `root_cause` 缺失**：旧 runner 不做失败签名归一化，也没有 `root_cause` 枚举；
9. **授权门缺失**：旧 runner 无 `admission_phase_authorized`/`admission_rules_frozen` 授权门。

## 5. 本实现未覆盖的事项

- 未实现工件获取、许可证与 SHA-256 核验、离线安装与依赖一致性核验（属授权后步骤，当前不执行）；
- 未对**候选项目**接入真实 pytest JSONL 链路：`integration/` 中的 `jsonl_producer` 已是真实
  pytest 插件并驱动端到端集成测试，但只作用于合成套件；`tests/` 仍直接构造记录，候选项目
  的 JSONL 产出、插件白名单与版本固定仍未落地；
- 未固定最终白名单、pytest 版本、插件集合与 `admission_rules_hash`（须在签署时写入机器文件）；
- **版本范围**：全部集成/gate 验证仅在当前机器的 Python 3.12.3 / pytest 9.1.1 上、对合成套件
  完成，**不构成候选项目准入验收**；冻结基线的 Python 3.11.9 / pytest 7.2 等目标版本，
  以及 `admission_gate` 接入未来真实 runner（不启动候选）的路径，均未验证；
- `admission_gate` 已接入 `b0_runner.run_b0`：授权门最先判（未授权不启动任何进程），已授权
  非 dry-run 时经 `timeout_ledger.run_jsonl` 逐轮挂 producer 产出 native JSONL，每轮得到
  `{jsonl, returncode, timed_out}` 后调用 gate，结果含 `admission_gate` 判定；但该路径仍只对
  合成套件验证，候选项目的真实 JSONL 产出与冻结目标版本（CPython 3.11.9 / 指定 pytest）尚未落地；
- 未执行候选项目、B0 基线、K01—K08、A1→B→A2、FOW 或标准化实验；
- 已授权路径（`run_b0` 非 dry-run）未在真实环境验证，仅通过授权门与快照合成测试覆盖。
