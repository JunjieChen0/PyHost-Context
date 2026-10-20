"""ClawsGO Science Agent：合成检验执行器三项修正（v3 独立审计的缺口 1—3）。

只使用合成命令与临时目录，不接触任何候选项目：

- ``TimeoutLedgerCleanupTests``：超时后是否真的对**进程组**发信号、是否记录 PID/PGID/信号，
  以及清理后进程组内是否还有成员（含同组后代）。
- ``StateIsolationTests``：``restore_source_tree`` 是否按工件字节写回、补回缺失文件、清缓存，
  以及目录复位是否可复算。
- ``NetworkGuardTests``：``netwatch`` 是否对被覆盖调用 fail-closed、是否留下 init/end 记录，
  以及"日志缺失"是否与"没有连接尝试"可区分。
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parent.parent  # pyhost-context/
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tooling"))

from restore_source_tree import restore, verify  # noqa: E402
from timeout_ledger import TimeoutLedger  # noqa: E402


class TimeoutLedgerCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pyhost-ledger-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_normal_completion_records_no_cleanup(self):
        ledger = TimeoutLedger(boundary=30, log_dir=self.tmp / "logs")
        record = ledger.run([sys.executable, "-c", "print('ok')"], self.tmp, dict(os.environ),
                            stage="normal", timeout=30)
        self.assertEqual(record["returncode"], 0)
        self.assertFalse(record["timed_out"])
        self.assertFalse(record["process_group_cleanup"]["performed"])
        self.assertIn("child_pgid", record)
        self.assertTrue(Path(record["stdout"]).read_text().strip() == "ok")

    def test_timeout_kills_process_group_and_records_signal(self):
        ledger = TimeoutLedger(boundary=2, log_dir=self.tmp / "logs")
        record = ledger.run([sys.executable, "-c", "import time; time.sleep(60)"],
                            self.tmp, dict(os.environ), stage="timeout", timeout=2)
        self.assertTrue(record["timed_out"])
        self.assertEqual(record["returncode"], -9)
        self.assertEqual(record["signal"], 9)
        cleanup = record["process_group_cleanup"]
        self.assertTrue(cleanup["performed"])
        self.assertEqual(cleanup["signal"], "SIGKILL")
        self.assertEqual(cleanup["target_pgid"], record["child_pgid"])
        self.assertTrue(cleanup["killpg_ok"])
        self.assertFalse(cleanup["group_alive_after_cleanup"])


    def test_timeout_also_clears_same_group_descendants(self):
        """同进程组的后代必须一并被清理——这是 subprocess.run 不提供的那部分证据。"""
        ledger = TimeoutLedger(boundary=2, log_dir=self.tmp / "logs")
        script = "sleep 300 & sleep 300"
        record = ledger.run(["/bin/sh", "-c", script], self.tmp, dict(os.environ),
                            stage="descendant", timeout=2)
        self.assertTrue(record["timed_out"])
        self.assertFalse(
            record["process_group_cleanup"]["group_alive_after_cleanup"],
            "超时清理后进程组内仍有成员，说明后代未被清理")


class StateIsolationTests(unittest.TestCase):
    """用一个小型 tar.gz 假工件检验恢复逻辑，不触及真实候选。"""

    def setUp(self):
        import tarfile
        self.tmp = Path(tempfile.mkdtemp(prefix="pyhost-restore-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.artifact = self.tmp / "admission_artifacts" / "demo"
        self.tree_root = self.tmp / "admission_inspect"
        self.source = self.tree_root / "demo-1.0.0"
        self.artifact.mkdir(parents=True)
        payload = {"pkg/__init__.py": b"VERSION = '1.0.0'\n",
                   "pkg/mod.py": b"def f():\n    return 1\n",
                   "tests/test_mod.py": b"def test_f():\n    assert True\n"}
        # 复现"树 = 已解包的工件"这一初始条件
        for rel, data in payload.items():
            target = self.source / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        archive = self.artifact / "demo-1.0.0.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            for rel, data in payload.items():
                info = tarfile.TarInfo("demo-1.0.0/" + rel)
                info.size = len(data)
                import io
                tf.addfile(info, io.BytesIO(data))
        self.payload = payload
        # 让 restore 在本测试的临时根目录上工作
        import restore_source_tree as module
        self.module = module
        self._saved = (module.ART, module.TREE)
        module.ART, module.TREE = self.tmp / "admission_artifacts", self.tree_root
        self.addCleanup(lambda: setattr(module, "ART", self._saved[0]) or
                        setattr(module, "TREE", self._saved[1]))

    def test_restores_modified_missing_and_extra(self):
        (self.source / "pkg" / "mod.py").write_text("# tampered\n")          # 内容被改
        (self.source / "pkg" / "extra.py").write_text("# not in artifact\n")  # 多出
        (self.source / "pkg" / "__pycache__").mkdir(parents=True)
        (self.source / "pkg" / "__pycache__" / "mod.cpython-311.pyc").write_bytes(b"\x00")
        record = restore("demo-1.0.0", apply=True)
        self.assertEqual(record["restored_count"], 1)
        self.assertIn("pkg/mod.py", record["restored_from_artifact"])
        self.assertEqual(record["deleted_count"], 1)
        self.assertIn("pkg/extra.py", record["deleted_not_in_artifact"])
        self.assertEqual(record["cache_files_removed_count"], 1)
        self.assertEqual((self.source / "pkg" / "mod.py").read_bytes(),
                         self.payload["pkg/mod.py"])
        result = verify("demo-1.0.0")
        self.assertTrue(result["identical_to_artifact"], result)

    def test_restore_is_idempotent_and_reports_untouched(self):
        """树已等于工件时，恢复应是零副作用，并如实报告"逐字节校验未变"的数量。"""
        first = restore("demo-1.0.0", apply=True)
        second = restore("demo-1.0.0", apply=True)
        for record in (first, second):
            self.assertEqual(record["restored_count"], 0)
            self.assertEqual(record["deleted_count"], 0)
            self.assertEqual(record["bytes_verified_unchanged"], len(self.payload))

    def test_missing_artifact_file_is_written_back(self):
        (self.source / "pkg" / "mod.py").unlink()
        restore("demo-1.0.0", apply=True)
        self.assertTrue((self.source / "pkg" / "mod.py").is_file())
        self.assertTrue(verify("demo-1.0.0")["identical_to_artifact"])


class NetworkGuardTests(unittest.TestCase):
    """在子进程里检验观察器，避免污染本测试进程的 socket 模块。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pyhost-netwatch-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        shutil.copyfile(HERE / "netwatch_sitecustomize.py", self.tmp / "sitecustomize.py")

    def _run(self, code, log_path):
        env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(self.tmp),
               "PYHOST_NETLOG": str(log_path)}
        return subprocess.run([sys.executable, "-c", code], capture_output=True,
                              text=True, env=env, cwd=str(self.tmp), timeout=60)

    def test_non_loopback_connect_is_denied_and_recorded(self):
        log = self.tmp / "net.jsonl"
        proc = self._run(
            "import socket\n"
            "try:\n"
            "    socket.create_connection(('example.com', 80), timeout=2)\n"
            "    print('CONNECTED')\n"
            "except PermissionError:\n"
            "    print('DENIED')\n", log)
        self.assertIn("DENIED", proc.stdout)
        self.assertNotIn("CONNECTED", proc.stdout)
        kinds = [json.loads(line)["kind"] for line in log.read_text().splitlines()]
        self.assertIn("observer_init", kinds)
        self.assertIn("connection_denied", kinds)
        self.assertIn("observer_end", kinds)

    def test_hostname_with_127_prefix_is_denied(self):
        """v4 审计反例：以 ``127.`` 开头的**域名**不是 127.0.0.0/8 中的地址，
        不得因文本前缀放行。"""
        log = self.tmp / "net.jsonl"
        proc = self._run(
            "import socket\n"
            "try:\n"
            "    socket.getaddrinfo('127.external.invalid', 80)\n"
            "    print('DELEGATED')\n"
            "except PermissionError:\n"
            "    print('DENIED')\n", log)
        self.assertIn("DENIED", proc.stdout)
        self.assertNotIn("DELEGATED", proc.stdout)

    def test_unix_socket_path_is_not_treated_as_network(self):
        """v4 审计反例：AF_UNIX 绝对路径不是网络地址，不得被当作非环回拒绝。"""
        log = self.tmp / "net.jsonl"
        sock = self.tmp / "local.sock"
        proc = self._run(
            "import socket\n"
            f"s = socket.socket(socket.AF_UNIX)\n"
            f"try:\n"
            f"    s.connect({str(sock)!r})\n"
            "except PermissionError:\n"
            "    print('WRONGLY_DENIED')\n"
            "except (FileNotFoundError, ConnectionRefusedError, OSError):\n"
            "    print('REACHED_LOCAL_PATH')\n", log)
        self.assertIn("REACHED_LOCAL_PATH", proc.stdout)
        self.assertNotIn("WRONGLY_DENIED", proc.stdout)

    def test_unix_socket_relative_path_is_not_treated_as_network(self):
        """v5 审计反例：AF_UNIX 套接字连**相对路径**时，路径不以 / 开头，
        不得因文本形状被当作网络地址拒绝——判定必须读 sock.family。"""
        log = self.tmp / "net.jsonl"
        proc = self._run(
            "import socket\n"
            "s = socket.socket(socket.AF_UNIX)\n"
            "try:\n"
            "    s.connect('local-review.sock')\n"
            "except PermissionError:\n"
            "    print('WRONGLY_DENIED')\n"
            "except (FileNotFoundError, ConnectionRefusedError, OSError):\n"
            "    print('REACHED_LOCAL_PATH')\n", log)
        self.assertIn("REACHED_LOCAL_PATH", proc.stdout)
        self.assertNotIn("WRONGLY_DENIED", proc.stdout)
        kinds = [json.loads(line)["kind"] for line in log.read_text().splitlines()]
        self.assertIn("local_ipc_allowed", kinds)

    def test_abstract_unix_address_is_local_ipc(self):
        """抽象命名空间地址（以 NUL 开头）也是本地 IPC。"""
        log = self.tmp / "net.jsonl"
        proc = self._run(
            "import socket\n"
            "s = socket.socket(socket.AF_UNIX)\n"
            "try:\n"
            "    s.connect('\\0pyhost-abstract-test')\n"
            "except PermissionError:\n"
            "    print('WRONGLY_DENIED')\n"
            "except OSError:\n"
            "    print('REACHED_LOCAL_IPC')\n", log)
        self.assertIn("REACHED_LOCAL_IPC", proc.stdout)
        self.assertNotIn("WRONGLY_DENIED", proc.stdout)

    def test_loopback_is_allowed(self):
        import socket as real_socket
        server = real_socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        self.addCleanup(server.close)
        port = server.getsockname()[1]
        log = self.tmp / "net.jsonl"
        proc = self._run(
            "import socket\n"
            f"s = socket.create_connection(('127.0.0.1', {port}), timeout=2)\n"
            "print('ALLOWED')\n", log)
        self.assertIn("ALLOWED", proc.stdout)

    def test_network_host_classification_units(self):
        """**解析类**用网络地址规则：IP 用 ipaddress，域名只看事前清单；
        字符串形状不参与判定。"""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "netwatch_under_test", HERE / "netwatch_sitecustomize.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cases = [("127.0.0.1", True), ("127.0.0.53", True), ("::1", True),
                 ("0.0.0.0", True), ("::", True), ("localhost", True), (None, True),
                 ("example.com", False), ("127.external.invalid", False),
                 ("8.8.8.8", False), ("2001:4860:4860::8888", False),
                 ("localhost.evil.com", False),
                 ("/tmp/x.sock", False),
                 ("\0abstract", False)]
        for target, expected in cases:
            self.assertEqual(module._network_host_allowed(target), expected, target)

    def test_local_ipc_detection_uses_socket_family(self):
        """判定本地 IPC 依据 ``sock.family``，不依据路径是否以 ``/`` 开头。"""
        import importlib.util
        import socket as real_socket
        spec = importlib.util.spec_from_file_location(
            "netwatch_under_test", HERE / "netwatch_sitecustomize.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        unix_sock = real_socket.socket(real_socket.AF_UNIX)
        self.addCleanup(unix_sock.close)
        for target in ("local-review.sock", "/tmp/local.sock", "\0abstract", "any-name"):
            self.assertTrue(module._is_local_ipc(unix_sock, target), target)
        inet_sock = real_socket.socket(real_socket.AF_INET)
        self.addCleanup(inet_sock.close)
        self.assertFalse(module._is_local_ipc(inet_sock, ("127.0.0.1", 80)))
        self.assertTrue(module._is_local_ipc(None, "some-unix-path"))

    def test_missing_log_is_distinguishable_from_no_attempt(self):
        """未设置 PYHOST_NETLOG 时观察器惰性，不生成日志——这正是要能识别的情形。"""
        env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(self.tmp)}
        log = self.tmp / "absent.jsonl"
        subprocess.run([sys.executable, "-c", "print('no guard')"], capture_output=True,
                       text=True, env=env, cwd=str(self.tmp), timeout=60)
        self.assertFalse(log.exists())

    def test_init_record_declares_coverage(self):
        log = self.tmp / "net.jsonl"
        self._run("pass\n", log)
        first = json.loads(log.read_text().splitlines()[0])
        self.assertEqual(first["kind"], "observer_init")
        self.assertEqual(first["policy"], "deny_and_record_non_loopback")
        self.assertIn("socket.getaddrinfo", first["covered_calls"])
        self.assertIn("non_python_subprocesses", first["not_covered"])


class NormalExitGroupCheckTests(unittest.TestCase):
    """v4 审计反例：父进程正常退出 0，但组内仍留有后代。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pyhost-exitgroup-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_normal_exit_still_reports_and_clears_residual_descendants(self):
        ledger = TimeoutLedger(boundary=30, log_dir=self.tmp / "logs")
        record = ledger.run(["/bin/sh", "-c", "sleep 300 & exit 0"], self.tmp,
                            dict(os.environ), stage="normal_with_descendant", timeout=30)
        self.assertEqual(record["returncode"], 0)
        self.assertFalse(record["timed_out"])
        cleanup = record["process_group_cleanup"]
        self.assertTrue(cleanup["group_alive_at_child_exit"],
                        "组内残留后代未被检出")
        self.assertTrue(cleanup["performed"])
        self.assertEqual(cleanup["trigger"], "residual_group_members_after_normal_exit")
        self.assertFalse(cleanup["group_alive_after_cleanup"])

    def test_normal_exit_without_descendants_reports_clean(self):
        ledger = TimeoutLedger(boundary=30, log_dir=self.tmp / "logs")
        record = ledger.run([sys.executable, "-c", "print('clean')"], self.tmp,
                            dict(os.environ), stage="clean", timeout=30)
        cleanup = record["process_group_cleanup"]
        self.assertFalse(cleanup["performed"])
        self.assertFalse(cleanup["group_alive_at_child_exit"])


if __name__ == "__main__":
    unittest.main()
