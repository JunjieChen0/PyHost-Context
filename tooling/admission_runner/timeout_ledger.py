"""ClawsGO Science Agent：新进程执行与240秒超时账本。

复用 runner_v021 的 os.wait4 轮询 + 进程组 SIGKILL 思路，但不修改旧 runner。
"""
import datetime as dt
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

OUTER_TIMEOUT_SECONDS = 240


class TimeoutLedger:
    """逐命令独立超时的执行账本；超时以 SIGKILL 终止进程组并保留原始证据。"""

    def __init__(self, boundary=OUTER_TIMEOUT_SECONDS, log_dir=None):
        self.boundary = boundary
        self.log_dir = Path(log_dir) if log_dir is not None else None
        self.entries = []

    def run(self, argv, cwd, env, stage, timeout=None):
        limit = self.boundary if timeout is None else timeout
        argv = [str(item) for item in argv]
        cwd = str(cwd)
        if self.log_dir is None:
            self.log_dir = Path(tempfile.mkdtemp(prefix="pyhost-ctx-ledger-"))
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = self.log_dir / (stage + ".stdout.txt")
        stderr_path = self.log_dir / (stage + ".stderr.txt")
        started = dt.datetime.now(dt.timezone.utc).isoformat()
        clock = time.monotonic()
        with stdout_path.open("w") as out, stderr_path.open("w") as err:
            proc = subprocess.Popen(
                argv, cwd=cwd, env=env, stdout=out, stderr=err, start_new_session=True)
            pgid = os.getpgid(proc.pid)
            status, usage, timed_out, cleanup = self._wait(proc.pid, pgid, limit, clock)
        elapsed = time.monotonic() - clock
        exitcode = os.waitstatus_to_exitcode(status)
        proc.returncode = exitcode
        record = {
            "stage": stage,
            "argv": argv,
            "cwd": cwd,
            "started_at_utc": started,
            "timeout_seconds": limit,
            "duration_seconds": elapsed,
            "timed_out": timed_out,
            "returncode": exitcode,
            "signal": -exitcode if exitcode is not None and exitcode < 0 else None,
            "child_pid": proc.pid,
            "child_pgid": pgid,
            "process_group_cleanup": cleanup,
            "child_peak_rss_kib": usage.ru_maxrss,
            "child_user_cpu_seconds": usage.ru_utime,
            "child_system_cpu_seconds": usage.ru_stime,
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
            "resource_method": "Linux os.wait4：测量该命令子进程，不能解释为整个流水线的累计峰值",
        }
        self.entries.append(record)
        return record

    def run_jsonl(self, argv, cwd, env, stage, jsonl_path, timeout=None):
        """执行产出 native JSONL 的命令，返回 ``(artifact, record)``。

        ``artifact`` 为 ``admission_gate`` 所需的 ``{jsonl, returncode, timed_out}`` 结构；
        JSONL 由被测进程（producer 插件）独占写入 ``jsonl_path``。文件缺失时 ``jsonl`` 记为空串，
        仍以 ``returncode``/``timed_out`` 供 gate 判定，绝不伪造或补写工件。
        """
        record = self.run(argv, cwd=cwd, env=env, stage=stage, timeout=timeout)
        path = Path(jsonl_path)
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        record["jsonl_path"] = str(path)
        record["jsonl_present"] = path.exists()
        artifact = {
            "jsonl": text,
            "returncode": record["returncode"],
            "timed_out": record["timed_out"],
        }
        return artifact, record

    @staticmethod
    def _group_alive(pgid):
        """进程组内是否仍有成员；``os.killpg(pgid, 0)`` 无权限/无成员时抛 OSError。"""
        try:
            os.killpg(pgid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:  # pragma: no cover - 本机同一用户，不应发生
            return True

    @classmethod
    def _wait(cls, pid, pgid, limit, clock):
        """轮询等待；超时则对整个进程组发 SIGKILL 并记录清理结果。

        返回 ``(status, usage, timed_out, cleanup)``。**两条路径都做组内成员检查**：

        - 超时路径：对进程组 ``SIGKILL``，记录目标 PGID、信号、``killpg_ok`` 与清理后
          进程组是否仍有成员；
        - 正常退出路径：父进程退出 0 **不等于**组内无残留后代，故同样检查组内成员；
          若有残留则一并 ``SIGKILL`` 并留证，避免跨轮污染。

        ``group_alive_after_cleanup`` / ``group_alive_at_child_exit`` 都是**瞬时观测**：
        为 ``true`` 表示该时刻组内仍存在成员（可能尚未被回收），不等于它们仍在活动。
        """
        while True:
            waited, status, usage = os.wait4(pid, os.WNOHANG)
            if waited:
                return status, usage, False, cls._cleanup_after_normal_exit(pid, pgid)
            if time.monotonic() - clock >= limit:
                cleanup = {"performed": True, "target_pgid": pgid,
                           "signal": "SIGKILL", "killpg_ok": False,
                           "trigger": "outer_timeout"}
                try:
                    os.killpg(pgid, signal.SIGKILL)
                    cleanup["killpg_ok"] = True
                except ProcessLookupError:
                    cleanup["killpg_ok"] = False
                    cleanup["note"] = "进程组已不存在"
                _, status, usage = os.wait4(pid, 0)
                time.sleep(0.05)  # 给同组后代留出被 SIGKILL 回收的时间
                cleanup["group_alive_after_cleanup"] = cls._group_alive(pgid)
                return status, usage, True, cleanup
            time.sleep(0.02)

    @classmethod
    def _cleanup_after_normal_exit(cls, pid, pgid):
        """父进程正常退出后的组内检查：有残留后代就清理，并如实记录。"""
        time.sleep(0.05)
        if not cls._group_alive(pgid):
            return {"performed": False,
                    "reason": "子进程自行退出，且组内无残留成员",
                    "group_alive_at_child_exit": False,
                    "target_pgid": pgid}
        cleanup = {"performed": True, "target_pgid": pgid, "signal": "SIGKILL",
                   "killpg_ok": False, "trigger": "residual_group_members_after_normal_exit",
                   "group_alive_at_child_exit": True}
        try:
            os.killpg(pgid, signal.SIGKILL)
            cleanup["killpg_ok"] = True
        except ProcessLookupError:
            cleanup["note"] = "进程组在检查与发送信号之间消失"
        time.sleep(0.05)
        cleanup["group_alive_after_cleanup"] = cls._group_alive(pgid)
        return cleanup
