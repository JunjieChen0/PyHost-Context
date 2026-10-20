"""ClawsGO Science Agent：PyHost-Context A 阶段准入 B0 基线驱动（v3）。

每个候选：用冻结 CPython 3.11.9 建独立 venv，离线安装候选 wheel 与声明的测试依赖，
把 observer 作为**已声明工件**装进 venv 的 site-packages，在 B0 显式环境与 240 秒超时下
执行 5 轮真实 pytest，把每轮 {jsonl, returncode, timed_out} 交给 admission_gate 判定。

v3 相对 v2 的修正（依据 v3 独立审计的三项执行缺口）：

- **超时后进程组清理**：执行改走 ``timeout_ledger.TimeoutLedger``（``os.wait4`` 轮询 +
  超时对进程组 ``SIGKILL``），并记录 PID/PGID、发送的信号、killpg 结果与清理后进程组是否
  仍有成员。v2 用 ``subprocess.run(timeout=...)``，超时后只记 ``returncode=None``，既没有
  进程组清理证据，也不能说明后代是否残留。
- **完整初始状态**：每轮开始前不只恢复源码树，还**清空并重建** ``<run-root>/home`` 与
  ``<run-root>/tmp``，并把三者（源码树、home、tmp）的逐轮状态哈希一并记录。
  源码树恢复也从"只删多出的文件"升级为**按工件字节写回缺失/内容不同的文件**并清理字节码缓存，
  恢复后立即复核与 sdist 逐字节一致。
- **网络规则明确化**：由"只记录不阻断"改为**对被覆盖调用拒绝并记录**（fail-closed），
  覆盖范围与未覆盖范围写入每轮元数据；观察器的初始化/结束/自身错误都落盘，使"日志缺失"
  与"确实没有连接尝试"可区分。

只运行候选项目自身的测试套件（准入基线），不执行任何 K01—K08 扰动条件。
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BASE_PY = (ROOT.parent / "pycompat-env-select/runtime/interpreters/"
           "cpython-3.11.9-linux-x86_64-gnu/bin/python3.11")
INTEG = ROOT / "tooling/admission_runner"
ART = ROOT / "admission_artifacts"
CONDITIONS = json.loads((ROOT / "admission_freeze/per_candidate_conditions.json").read_text())
B0 = json.loads((ROOT / "admission_freeze/b0_environment.json").read_text())
ROUNDS = 5
TIMEOUT = 240
FROZEN_PYTEST_PINS = ["pytest==7.2.0", "pluggy==1.0.0", "iniconfig==1.1.1",
                      "packaging==22.0", "attrs==21.4.0",
                      "tzdata==2025.2"]
TOOL_WHEELHOUSE = ROOT.parent / "pycompat-env-select/stage2/wheelhouse"

DECLARED_EXTRA_KEYS = {
    "PYHOST_JSONL_PATH": "本轮 observer 输出路径",
    "PYHOST_NETLOG": "本轮网络拒绝/记录日志路径",
    "PYHOST_ROUND_META": "本轮绑定元数据（运行前承诺、文件状态哈希、解释器/observer 指纹）",
}

# 状态控制模型：明确写出哪些对象受控、哪些不受控，避免把"记录的源码哈希一致"写成"全部状态一致"。
STATE_CONTROL_MODEL = {
    "controlled_and_recorded": [
        "候选源码树：每轮按冻结 sdist 逐字节恢复，恢复后复核一致；字节码缓存每轮清除",
        "<run-root>/home：每轮清空重建",
        "<run-root>/tmp（TMPDIR 指向此处）：每轮清空重建",
    ],
    "not_controlled": [
        "内核页缓存与文件系统层缓存",
        "候选 venv 内 site-packages 的 __pycache__（安装后不再变化，但本协议不逐轮清理）",
        "宿主机全局 /tmp 中不经 TMPDIR 的路径",
        "解释器自身的启动开销与宿主机负载",
    ],
    "scope_statement": ("可声称的是：记录的源码树、home、tmp 三项状态在每轮起始时逐轮相同。"
                        "不可声称全部文件/缓存/临时状态一致。"),
}

sys.path.insert(0, str(INTEG))
from admission_gate import admission_gate  # noqa: E402
from commitment import compute_commitment  # noqa: E402
from env_state import build_launch_env, file_state_all, file_state_hash, launch_env_diff  # noqa: E402
from observer_install import install_observer, sha256_of  # noqa: E402
from restore_source_tree import restore as restore_artifact, verify as verify_artifact  # noqa: E402
from timeout_ledger import TimeoutLedger  # noqa: E402


# ---------------------------------------------------------------- 启动环境（严格三态）

def resolve_spec(bindings):
    """把 B0 的两张三态表原样取出，只对 ``state == "set"`` 的取值做占位符绑定。"""
    spec = {}
    for key, entry in {**B0["variable_spec"], **B0["control_spec"]}.items():
        state = entry["state"]
        if state == "set":
            value = entry["value"]
            for placeholder, replacement in bindings.items():
                value = value.replace(placeholder, str(replacement))
            spec[key] = {"state": "set", "value": value}
        else:
            spec[key] = {"state": state}
    return spec


def build_round_env(run_root, cwd, jsonl, netlog, meta):
    spec = resolve_spec({"<frozen-run-root>": run_root, "<project-cwd>": cwd})
    spec["PYHOST_JSONL_PATH"] = {"state": "set", "value": str(jsonl)}
    spec["PYHOST_NETLOG"] = {"state": "set", "value": str(netlog)}
    spec["PYHOST_ROUND_META"] = {"state": "set", "value": str(meta)}
    whitelist = sorted(spec)
    env = build_launch_env(whitelist, spec, {})
    drift = launch_env_diff(spec, env, whitelist)
    if drift:
        raise AssertionError(f"启动环境与声明不一致：{drift}")
    return spec, env


BOOT_PROBE = (
    "import json,os,sys;"
    "print(json.dumps({'post_init_env':dict(sorted(os.environ.items())),"
    "'executable':sys.executable,'realpath':os.path.realpath(sys.executable),"
    "'version':sys.version.split()[0],'flags_utf8':sys.flags.utf8_mode,"
    "'stdin_isatty':sys.stdin.isatty() if sys.stdin else None,"
    "'stdout_isatty':sys.stdout.isatty() if sys.stdout else None,"
    "'stderr_isatty':sys.stderr.isatty() if sys.stderr else None,"
    "'open_fds':sorted(os.listdir('/proc/self/fd')) if os.path.isdir('/proc/self/fd') else []}))"
)


def reset_directory(path):
    """清空并重建目录，返回被删除的条目数（用于 home/tmp 的逐轮复位）。"""
    path = Path(path)
    removed = 0
    if path.is_dir():
        for child in path.iterdir():
            removed += 1
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
    path.mkdir(parents=True, exist_ok=True)
    return removed


def directory_state(path):
    """目录内全部常规文件的 ``{相对路径: sha256}``；空目录返回空表。"""
    import hashlib
    path = Path(path)
    state = {}
    if not path.is_dir():
        return state
    for item in sorted(path.rglob("*")):
        if item.is_file():
            state[item.relative_to(path).as_posix()] = hashlib.sha256(item.read_bytes()).hexdigest()
    return state


def state_of(state_map):
    """目录状态的紧凑表示。

    ``hash`` 一律用 ``file_state_hash``，空集合得到的是**空载荷的标准 SHA-256**
    （``e3b0c442…``），而不是 ``null``——这样"空目录"是"检测到的空集合"，而不是"没有检测"。
    旧版对空目录写 ``hash: null``，含义需要靠读源码推断，已由 v4 审计指出。
    """
    return {"file_count": len(state_map), "hash": file_state_hash(state_map),
            "empty_collection_hash": not state_map}


def net_log_summary(netlog_path, expect_pid=None):
    """汇总本轮网络观察器的记录，并按进程分开。

    同一轮里 boot probe 与 pytest 是两个进程，各自写 init/end；必须按 pid 分组，
    否则无法判断**被测进程本身**是否装上了观察器。``expect_pid`` 为 pytest 子进程 pid。
    """
    path = Path(netlog_path)
    summary = {"log_present": path.exists(), "observer_init_seen": False,
               "observer_end_seen": False, "denied": 0, "allowed": 0,
               "observer_errors": [], "lines": 0, "processes": {}}
    if not path.exists():
        return summary
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        summary["lines"] += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            summary["observer_errors"].append("unparsable_log_line")
            continue
        pid = str(record.get("pid"))
        entry = summary["processes"].setdefault(
            pid, {"init_seen": False, "end_seen": False, "denied": 0, "allowed": 0, "errors": []})
        kind = record.get("kind")
        if kind == "observer_init":
            summary["observer_init_seen"] = True
            entry["init_seen"] = True
            entry["covered_calls"] = record.get("covered_calls")
            entry["not_covered"] = record.get("not_covered")
            entry["policy"] = record.get("policy")
        elif kind == "observer_end":
            summary["observer_end_seen"] = True
            entry["end_seen"] = True
        elif kind == "connection_denied":
            summary["denied"] += 1
            entry["denied"] += 1
        elif kind in ("connection_allowed", "resolution_allowed"):
            summary["allowed"] += 1
            entry["allowed"] += 1
        elif kind == "observer_error":
            summary["observer_errors"].append(record.get("error"))
            entry["errors"].append(record.get("error"))
    summary["pytest_process_pid"] = str(expect_pid) if expect_pid is not None else None
    pytest_entry = summary["processes"].get(str(expect_pid))
    summary["pytest_process_covered"] = bool(pytest_entry and pytest_entry["init_seen"])
    summary["process_count"] = len(summary["processes"])
    return summary


def snapshot_env_of(jsonl_text):
    for line in jsonl_text.splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        if record.get("$report_type") == "RuntimeSnapshot":
            return record.get("environment")
    return None


def main():
    only = sys.argv[1:] or None

    commitment = compute_commitment(ROOT)
    commitment.update({
        "schema_version": "pyhost-context/pre-run-commitment/1.0",
        "study": "PyHost-Context", "phase": "admission",
        "computed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "computed_before_any_round": True, "excludes_result_files": True,
    })
    (ROOT / "admission_freeze/pre_run_commitment.json").write_text(
        json.dumps(commitment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"pre-run commitment: {commitment['pre_run_commitment_hash']} "
          f"({commitment['file_count']} files)", flush=True)

    interpreter = {"base_interpreter": str(BASE_PY),
                   "base_interpreter_sha256": sha256_of(BASE_PY),
                   "base_interpreter_realpath": os.path.realpath(BASE_PY)}
    results = []
    for cand in CONDITIONS["candidates"]:
        name = cand["name"]
        if only and name not in only:
            continue
        entry = {"name": name, "package_version": cand["package_version"], "status": "pending"}
        entry["plugin_loads"] = cand.get("plugin_loads", [])
        entry["extra_test_dependencies"] = cand["extra_test_dependencies"]
        run_root = ROOT / "admission_runs" / name
        if run_root.exists():
            shutil.rmtree(run_root)
        logs = run_root / "logs"
        for sub in ("home", "tmp", "bin", "logs"):
            (run_root / sub).mkdir(parents=True, exist_ok=True)
        source = ROOT / "admission_inspect" / cand["source_dir"]
        venv = run_root / "venv"
        found = "FAILED"
        try:
            # ---- 运行前：把源码树恢复为冻结工件并复核
            initial_restore = restore_artifact(cand["source_dir"], apply=True)
            initial_verify = verify_artifact(cand["source_dir"])
            if not initial_verify["identical_to_artifact"]:
                raise AssertionError(f"源码树未与冻结工件一致：{initial_verify}")
            pre_state = file_state_all(source)
            pre_hash = file_state_hash(pre_state)
            (run_root / "pre_run_file_state.json").write_text(json.dumps(
                {"file_count": len(pre_state), "hash": pre_hash, "files": pre_state,
                 "restore": initial_restore, "verify": initial_verify},
                ensure_ascii=False, indent=2) + "\n")
            entry["initial_state_restore"] = {
                "artifact_file_count": initial_restore["artifact_file_count"],
                "bytes_verified_unchanged": initial_restore["bytes_verified_unchanged"],
                "restored_count": initial_restore["restored_count"],
                "deleted_count": initial_restore["deleted_count"],
                "cache_files_removed_count": initial_restore["cache_files_removed_count"],
                "identical_to_artifact": initial_verify["identical_to_artifact"]}
            # ---- venv 与安装
            subprocess.run([str(BASE_PY), "-m", "venv", "--without-pip", str(venv)],
                           check=True, capture_output=True, timeout=180)
            venvpy = venv / "bin/python"
            links = [str(ART / name), str(ART / "_test_dependencies"), str(TOOL_WHEELHOUSE)]
            args = ["uv", "pip", "install", "--python", str(venvpy), "--offline", "--no-index"]
            for link in links:
                args += ["--find-links", link]
            args += ["--link-mode", "copy"]
            args.append(str(next((ART / name).glob("*.whl"))))
            args += FROZEN_PYTEST_PINS + cand["extra_test_dependencies"]
            inst = subprocess.run(args, capture_output=True, text=True, timeout=300)
            entry["install"] = {"returncode": inst.returncode, "stdout_tail": inst.stdout[-800:],
                                "stderr_tail": inst.stderr[-800:]}
            check = subprocess.run(["uv", "pip", "check", "--python", str(venvpy)],
                                   capture_output=True, text=True, timeout=180)
            entry["pip_check"] = {"returncode": check.returncode, "stdout_tail": check.stdout[-800:]}
            inv = subprocess.run([str(venvpy), "-m", "pip", "list", "--format=json"],
                                 capture_output=True, text=True, timeout=180)
            try:
                entry["installed"] = sorted((p["name"].lower(), p["version"])
                                            for p in json.loads(inv.stdout))
            except Exception:
                entry["installed"] = None

            observer = install_observer(venvpy)
            entry["observer_install"] = observer

            for alias in ("python", "python3"):
                link = run_root / "bin" / alias
                if link.exists() or link.is_symlink():
                    link.unlink()
                link.symlink_to(venvpy)

            interpreter_entry = dict(interpreter)
            interpreter_entry["venv_python"] = str(venvpy)
            interpreter_entry["venv_python_realpath"] = os.path.realpath(venvpy)
            interpreter_entry["venv_python_sha256"] = sha256_of(os.path.realpath(venvpy))
            entry["interpreter"] = interpreter_entry
            entry["pre_run_file_state"] = {"file_count": len(pre_state), "hash": pre_hash}

            ledger = TimeoutLedger(boundary=TIMEOUT, log_dir=logs)
            rounds, logs_entries = [], []
            for r in range(1, ROUNDS + 1):
                # ---- 状态隔离：源码树按工件字节恢复；home/tmp 清空重建
                restore_record = restore_artifact(cand["source_dir"], apply=True)
                verify_record = verify_artifact(cand["source_dir"])
                if not verify_record["identical_to_artifact"]:
                    raise AssertionError(f"round{r} 源码树恢复后与工件不一致：{verify_record}")
                home_reset = reset_directory(run_root / "home")
                tmp_reset = reset_directory(run_root / "tmp")
                source_state = file_state_all(source)
                home_state = directory_state(run_root / "home")
                tmp_state = directory_state(run_root / "tmp")
                round_hash = file_state_hash(source_state)
                (logs / f"round{r}.file_state.json").write_text(json.dumps({
                    "source_tree": {"hash": round_hash, "file_count": len(source_state),
                                    "restore": restore_record, "verify": verify_record},
                    "home": {**state_of(home_state), "reset_removed_entries": home_reset},
                    "tmp": {**state_of(tmp_state), "reset_removed_entries": tmp_reset},
                    "scope": "受控对象为源码树/home/tmp；其余缓存与宿主机状态不在本记录范围",
                }, ensure_ascii=False, indent=2) + "\n")

                jsonl = logs / f"round{r}.jsonl"
                netlog = logs / f"round{r}.net.jsonl"
                meta_path = logs / f"round{r}.meta.json"
                meta = {
                    "candidate": name, "package_version": cand["package_version"],
                    "round": r, "expected_rounds": ROUNDS,
                    "outer_timeout_seconds": TIMEOUT,
                    "pre_run_commitment_hash": commitment["pre_run_commitment_hash"],
                    "pre_run_commitment_file_count": commitment["file_count"],
                    "b0_environment_sha256": commitment["files"]["admission_freeze/b0_environment.json"],
                    "launcher_sha256": commitment["files"]["tooling/run_admission_baseline.py"],
                    "per_candidate_conditions_sha256":
                        commitment["files"]["admission_freeze/per_candidate_conditions.json"],
                    "pre_run_file_state_hash": pre_hash,
                    "round_start_source_tree_hash": round_hash,
                    "round_start_home_hash": state_of(home_state)["hash"],
                    "round_start_tmp_hash": state_of(tmp_state)["hash"],
                    "round_start_source_restore": {
                        "restored_count": restore_record["restored_count"],
                        "deleted_count": restore_record["deleted_count"],
                        "cache_files_removed_count": restore_record["cache_files_removed_count"],
                        "deleted_not_in_artifact": restore_record["deleted_not_in_artifact"]},
                    "state_control_model": STATE_CONTROL_MODEL,
                    "observer_files": observer["files"],
                    "interpreter": interpreter_entry,
                    "declared_extra_keys": DECLARED_EXTRA_KEYS,
                    "network_policy": {
                        "mode": "deny_and_record_non_loopback_for_covered_python_calls",
                        "enforcement": "fail_closed_within_coverage",
                        "coverage": ["socket.socket.connect", "socket.socket.connect_ex",
                                     "socket.create_connection", "socket.getaddrinfo",
                                     "socket.gethostbyname"],
                        "not_covered": ["non_python_subprocesses", "native_library_direct_syscalls",
                                        "established_connection_io", "kernel_level_paths"],
                        "kernel_isolation_available": False,
                        "kernel_isolation_reason": "unshare -n 在本机返回 Operation not permitted",
                        "observer_effect": "sitecustomize 替换上述 socket 方法，属固定运行时包装；A/B 条件相同",
                    },
                }
                meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

                spec, env = build_round_env(run_root, source, jsonl, netlog, meta_path)
                (logs / f"round{r}.launch_env.json").write_text(json.dumps(
                    {"spec": spec, "env": dict(sorted(env.items())),
                     "declared_extra_keys": DECLARED_EXTRA_KEYS, "whitelist": sorted(spec)},
                    ensure_ascii=False, indent=2) + "\n")

                boot_record = ledger.run([str(venvpy), "-c", BOOT_PROBE], source, env,
                                         stage=f"round{r}_boot", timeout=60)
                boot_stdout = Path(boot_record["stdout"]).read_text(encoding="utf-8")
                try:
                    post_init_env = json.loads(boot_stdout.strip().splitlines()[-1])
                except Exception:
                    post_init_env = {"error": boot_stdout[-500:]}
                (logs / f"round{r}.boot_env.json").write_text(json.dumps(
                    {"probe_result": post_init_env, "returncode": boot_record["returncode"],
                     "timed_out": boot_record["timed_out"],
                     "duration_seconds": boot_record["duration_seconds"],
                     "process_group_cleanup": boot_record["process_group_cleanup"],
                     "stderr_tail": Path(boot_record["stderr"]).read_text(encoding="utf-8")[-500:]},
                    ensure_ascii=False, indent=2) + "\n")
                post_env = post_init_env.get("post_init_env") if isinstance(post_init_env, dict) else None
                launch_vs_post = (launch_env_diff(spec, post_env, sorted(spec))
                                  if isinstance(post_env, dict)
                                  else [{"key": "*", "error": "post_init_env 不可用"}])

                entry_argv = list(cand["test_entry_argv"])
                head, tail = entry_argv[:2], entry_argv[2:]
                argv = [str(venvpy)] + head + ["-p", "jsonl_producer", "-p", "no:cacheprovider"]
                for plugin in cand.get("plugin_loads", []):
                    argv += ["-p", plugin]
                argv += tail
                artifact, rec = ledger.run_jsonl(argv, source, env, stage=f"round{r}",
                                                 jsonl_path=jsonl, timeout=TIMEOUT)
                text = artifact["jsonl"]
                net_summary = net_log_summary(netlog, expect_pid=rec["child_pid"])
                session_env = snapshot_env_of(text)
                launch_vs_session = (launch_env_diff(spec, session_env, sorted(spec))
                                     if isinstance(session_env, dict)
                                     else [{"key": "*", "error": "RuntimeSnapshot 环境不可用"}])
                rounds.append({"jsonl": text, "returncode": rec["returncode"],
                               "timed_out": rec["timed_out"]})
                logs_entries.append({
                    "round": r, "argv": argv,
                    "returncode": rec["returncode"], "timed_out": rec["timed_out"],
                    "duration_seconds": rec["duration_seconds"],
                    "jsonl_bytes": len(text),
                    "stdout_bytes": Path(rec["stdout"]).stat().st_size,
                    "stderr_bytes": Path(rec["stderr"]).stat().st_size,
                    "child_pid": rec["child_pid"], "child_pgid": rec["child_pgid"],
                    "process_group_cleanup": rec["process_group_cleanup"],
                    "child_peak_rss_kib": rec["child_peak_rss_kib"],
                    "round_start_source_tree_hash": round_hash,
                    "round_start_source_restore": {
                        "restored_count": restore_record["restored_count"],
                        "deleted_count": restore_record["deleted_count"],
                        "cache_files_removed_count": restore_record["cache_files_removed_count"],
                        "deleted_not_in_artifact": restore_record["deleted_not_in_artifact"]},
                    "launch_env_matches_declaration": not launch_vs_post,
                    "launch_vs_post_init_env_diff": launch_vs_post,
                    "launch_vs_pytest_snapshot_env_diff": launch_vs_session,
                    "network": net_summary,
                    "stdout_tail": Path(rec["stdout"]).read_text(encoding="utf-8")[-2000:],
                    "stderr_tail": Path(rec["stderr"]).read_text(encoding="utf-8")[-2000:],
                })
                print(f"  {name} r{r}: rc={rec['returncode']} timed_out={rec['timed_out']} "
                      f"{rec['duration_seconds']}s jsonl={len(text)}B "
                      f"envdiff={len(launch_vs_post)}/{len(launch_vs_session)} "
                      f"denied={net_summary['denied']} restored={restore_record['restored_count']} "
                      f"deleted={restore_record['deleted_count']}", flush=True)

            post_state = file_state_all(source)
            post_hash = file_state_hash(post_state)
            changed = sorted(name for name in set(pre_state) | set(post_state)
                             if pre_state.get(name) != post_state.get(name))
            (run_root / "post_run_file_state.json").write_text(json.dumps(
                {"file_count": len(post_state), "hash": post_hash,
                 "equal_to_pre_run": post_hash == pre_hash,
                 "changed_or_added_or_removed": changed, "files": post_state},
                ensure_ascii=False, indent=2) + "\n")
            entry["post_run_file_state"] = {"file_count": len(post_state), "hash": post_hash,
                                            "equal_to_pre_run": post_hash == pre_hash,
                                            "changed_or_added_or_removed": changed}
            entry["rounds_log"] = logs_entries
            decision = admission_gate(rounds, policy={"allow_skips": True, "allow_xfails": True,
                                                     "allow_xpasses": True}, expected_rounds=ROUNDS)
            entry["admission_gate"] = {"admitted": decision["admitted"],
                                       "reasons": decision["reasons"], "rounds": decision["rounds"]}
            entry["status"] = "admission_pass" if decision["admitted"] else "admission_fail"
            found = entry["status"]
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            entry["status"] = "admission_fail"
            found = "ERROR"
        finally:
            (run_root / "candidate_result.json").write_text(
                json.dumps(entry, ensure_ascii=False, indent=2) + "\n")
        results.append(entry)
        print(f"{name}: {found}", flush=True)

    summary = {
        "schema_version": "pyhost-context/admission-baseline/3.0",
        "run_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "phase": "admission", "rounds_per_candidate": ROUNDS, "outer_timeout_seconds": TIMEOUT,
        "policy": {"allow_skips": True, "allow_xfails": True, "allow_xpasses": True},
        "disturbance_conditions_executed": False,
        "launch_environment_model": "strict_three_state_from_b0_environment_json",
        "observer_bootstrap": "installed_into_candidate_venv_site_packages_pythonpath_stays_unset",
        "timeout_handling": "timeout_ledger_os_wait4_with_process_group_sigkill_and_cleanup_record",
        "state_control_model": STATE_CONTROL_MODEL,
        "network_policy": "deny_and_record_non_loopback_for_covered_python_calls",
        "pre_run_commitment_hash": commitment["pre_run_commitment_hash"],
        "interpreter": interpreter,
        "results": [{k: v for k, v in r.items() if k not in ("rounds_log",)} for r in results],
        "pass": sum(1 for r in results if r["status"] == "admission_pass"),
        "fail": sum(1 for r in results if r["status"] != "admission_pass"),
    }
    out = ROOT / "admission_freeze/admission_baseline_results.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(f"pass={summary['pass']} fail={summary['fail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
