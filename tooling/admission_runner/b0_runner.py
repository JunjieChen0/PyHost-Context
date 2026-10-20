"""ClawsGO Science Agent：只读 B0 运行入口。未授权时拒绝真实执行，不修改候选项目。

授权门**最先判**：``admission_phase_authorized`` 与 ``admission_rules_frozen`` 非严格
布尔 ``True`` 时一律拒绝，且**不启动任何进程**。已授权且非 dry-run 时，逐轮用
``timeout_ledger`` 执行 ``test_entry_argv``（挂 ``producer`` 插件产出 native JSONL），
每轮得到 ``{jsonl, returncode, timed_out}`` 后交给 ``admission_gate`` 判定。
"""
import os
import tempfile
from pathlib import Path

from admission_gate import admission_gate
from env_state import build_launch_env, env_state, file_state, file_state_hash, launch_env_diff
from timeout_ledger import OUTER_TIMEOUT_SECONDS, TimeoutLedger

REQUIRED_AUTHORIZATION_FLAGS = ("admission_phase_authorized", "admission_rules_frozen")

_ADMISSION_DIR = Path(__file__).resolve().parent
# producer 插件路径与 pytest ``-p`` 参数默认指向本目录下的 integration/jsonl_producer.py。
DEFAULT_PRODUCER_PLUGIN = "integration/jsonl_producer.py"
# 与 integration/jsonl_producer.JSONL_ENV_VAR 同名字符串；此处字面量以免导入 pytest。
JSONL_ENV_VAR = "PYHOST_JSONL_PATH"


class AdmissionNotAuthorized(RuntimeError):
    """准入阶段未获授权，拒绝真实执行。"""


def authorization_errors(config):
    """返回非 True 的授权标志；只有严格布尔 True 才算授权。"""
    return [flag for flag in REQUIRED_AUTHORIZATION_FLAGS if config.get(flag) is not True]


def resolve_producer(config):
    """解析 producer 插件路径与 pytest ``-p`` 参数。

    ``producer_plugin``：插件文件路径，可绝对或相对 ``admission_runner``；默认
    ``integration/jsonl_producer.py``。``producer_pytest_args``：显式覆盖 ``-p`` 参数；
    缺省时按插件模块名派生 ``["-p", <plugin stem>]``。
    """
    plugin = config.get("producer_plugin", DEFAULT_PRODUCER_PLUGIN)
    plugin_path = Path(plugin)
    if not plugin_path.is_absolute():
        plugin_path = (_ADMISSION_DIR / plugin_path).resolve()
    args = config.get("producer_pytest_args")
    if args is None:
        args = ["-p", plugin_path.stem]
    return plugin_path, [str(item) for item in args]


def _round_env(launch_env, plugin_path, jsonl_path):
    """在冻结启动环境之上叠加 observer 所需键：producer 可导入路径与 JSONL 输出路径。"""
    env = dict(launch_env)
    parts = [str(plugin_path.parent), str(_ADMISSION_DIR)]
    existing = env.get("PYTHONPATH")
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env[JSONL_ENV_VAR] = str(jsonl_path)
    return env


def build_b0_snapshot(config):
    """构造只读 B0 快照：文件状态与启动环境分开；probe/test 入口 argv 分开保存。"""
    whitelist = list(config["launch_whitelist"])
    spec = dict(config["baseline_condition"])
    launch_env = build_launch_env(whitelist, spec, config.get("base_values", {}))
    state = file_state(config["source"])
    return {
        "file_state": state,
        "file_state_hash": file_state_hash(state),
        "launch_environment": dict(sorted(launch_env.items())),
        "launch_env_three_state": env_state(launch_env, whitelist),
        # 差异只针对三态规格键；base_values 是白名单内固定控制项，不属扰动规格。
        "launch_env_diff": launch_env_diff(spec, launch_env, sorted(spec)),
        "probe_entry_argv": list(config["probe_entry_argv"]),
        "test_entry_argv": list(config["test_entry_argv"]),
    }


def check_authorized(config):
    missing = authorization_errors(config)
    if missing:
        raise AdmissionNotAuthorized(
            "准入阶段未授权，拒绝真实执行；缺少授权标志：" + ", ".join(missing))
    return True


def run_b0(config, dry_run=False, ledger=None):
    """只读 B0 运行入口。

    授权门**最先判**：未授权（``admission_phase_authorized`` 或 ``admission_rules_frozen``
    非 True）时一律拒绝，返回 refused 结果且**不启动任何进程**。已授权且非 dry-run 时，
    逐轮经 ``timeout_ledger`` 执行 ``test_entry_argv``（挂 producer 插件产出 native JSONL），
    每轮产出 ``{jsonl, returncode, timed_out}`` 结构，再交给 ``admission_gate`` 判定；结果中
    的 ``admission_gate`` 字段即该判定。已授权路径也只读取候选项目，不修改源码。
    """
    missing = authorization_errors(config)
    if missing:
        return {
            "executed": False,
            "status": "refused_not_authorized",
            "missing_authorization": missing,
            "note": "admission_phase_authorized 与 admission_rules_frozen 必须为 True；未授权时不启动任何进程、不修改候选项目。",
        }
    snapshot = build_b0_snapshot(config)
    if dry_run:
        return {"executed": False, "status": "dry_run_authorized", "snapshot": snapshot}

    boundary = config.get("outer_timeout_seconds", OUTER_TIMEOUT_SECONDS)
    run_dir = (Path(config["log_dir"]) if config.get("log_dir")
               else Path(tempfile.mkdtemp(prefix="pyhost-ctx-ledger-")))
    ledger = ledger or TimeoutLedger(boundary=boundary, log_dir=run_dir)
    launch_env = build_launch_env(
        config["launch_whitelist"], config["baseline_condition"], config.get("base_values", {}))
    plugin_path, plugin_args = resolve_producer(config)
    probe_argv = [str(item) for item in config["probe_entry_argv"]]
    test_argv = [str(item) for item in config["test_entry_argv"]] + plugin_args
    policy = config.get("policy", {})
    repeats = config.get("admission_baseline_repeats", 5)

    result = {"executed": True, "status": "b0_completed", "snapshot": snapshot, "rounds": []}
    gate_rounds = []
    for repeat in range(1, repeats + 1):
        probe = ledger.run(probe_argv, cwd=config["cwd"], env=launch_env,
                           stage=f"probe_r{repeat}", timeout=config.get("test_timeout_seconds"))
        jsonl_path = run_dir / f"gate_round_{repeat}.jsonl"
        env = _round_env(launch_env, plugin_path, jsonl_path)
        artifact, test = ledger.run_jsonl(
            test_argv, cwd=config["cwd"], env=env, stage=f"full_suite_r{repeat}",
            jsonl_path=jsonl_path, timeout=config.get("test_timeout_seconds"))
        gate_rounds.append(artifact)
        result["rounds"].append({"repeat": repeat, "probe": probe, "test": test})
    result["gate_rounds"] = gate_rounds
    result["admission_gate"] = admission_gate(gate_rounds, policy=policy, expected_rounds=repeats)
    result["ledger"] = ledger.entries
    return result
