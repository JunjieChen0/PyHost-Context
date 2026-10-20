"""ClawsGO Science Agent：实际 D 入口的合成接线测试。

假安装器、假子进程，但调用真实 `run_extension.admit_one`、`run_matrix.run_one`
签名与真实 D 入口；不安装、不导入候选。
"""
import hashlib
import json
import pathlib
import sys
import types
import zipfile

import pytest

HERE = pathlib.Path(__file__).resolve().parent
PYHOST = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PYHOST / "extension" / "tooling"))
sys.path.insert(0, str(PYHOST / "matrix" / "tooling"))

import d_adapter  # noqa: E402
import run_d_admission as rd  # noqa: E402

if not hasattr(rd.rx, "admit_one"):  # 包内只带 stub 时跳过；源树中运行全部 6 项
    pytest.skip("full run_extension instrument tree required", allow_module_level=True)


def make_wheel(directory, name, version, entries=None):
    path = pathlib.Path(directory) / f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    with zipfile.ZipFile(path, "w") as archive:
        base = f"{name.replace('-', '_')}-{version}.dist-info/"
        archive.writestr(base + "METADATA", metadata)
        if entries:
            archive.writestr(base + "entry_points.txt",
                             "[pytest11]\n" + "".join(f"{k} = {v}\n"
                                                      for k, v in entries.items()))
    return {"name": name, "version": version, "filename": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "source": "local", "dependencies": []}


def build(tmp_path, candidate_entries=None):
    root = pathlib.Path(tmp_path)
    source = root / "source"
    source.mkdir()
    candidate = make_wheel(source, "demo", "1.0", candidate_entries)
    rows = [make_wheel(source, "pytest", "9.1.1"),
            make_wheel(source, "attrs", "26.1.0")]
    store = root / "store"
    for row in rows + [candidate]:
        target = store / row["sha256"] / row["filename"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source / row["filename"]).read_bytes())
    document = {"projects": [{
        "project": "demo", "version": "1.0", "status": "locked",
        "pytest_version": "9.1.1", "candidate_wheel": candidate,
        "lock": {"stage": "R1", "rows": rows},
    }]}
    return document, store


class FakeProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class Harness:
    def __init__(self, tmp_path, document, store, *, collection_stdout="100 tests collected",
                 collect_rc=0):
        self.tmp = pathlib.Path(tmp_path)
        self.document = document
        self.locks = self.tmp / "locks.json"
        self.locks.write_text(json.dumps(document), encoding="utf-8")
        self.roots = self.tmp / "roots.json"
        self.roots.write_text(json.dumps({"projects": [{
            "project": "demo", "pytest_config_as_data": {"addopts": []}}]}),
            encoding="utf-8")
        self.store = pathlib.Path(store)
        self.state = self.tmp / "state"
        self.calls = []
        self.run_one_calls = []
        self.collection_stdout = collection_stdout
        self.collect_rc = collect_rc
        self.phase = self.tmp / "phase"

    def fake_run(self, argv, *args, **kwargs):
        argv = [str(item) for item in argv]
        self.calls.append(argv)
        if "-c" in argv:
            return FakeProcess(0, "pytest==9.1.1\ndemo==1.0\nattrs==26.1.0\n"
                                  "pyhost-observer==1.0\n")
        if "--collect-only" in argv:
            return FakeProcess(self.collect_rc, self.collection_stdout + "\n")
        return FakeProcess(0, "", "")

    def prepare_context(self, context, venvpy=None):
        for key in ("run_root", "logs", "home", "tmp"):
            pathlib.Path(context[key]).mkdir(parents=True, exist_ok=True)
        pathlib.Path(context["source_path"]).mkdir(parents=True, exist_ok=True)
        return {}

    def fake_run_one(self, **kwargs):
        self.run_one_calls.append(kwargs)
        return {"returncode": 0, "timed_out": False, "duration_seconds": 1.0,
                "session_exitstatus": 0, "collected": 100,
                "malformed_jsonl_lines": 0,
                "perturbation": {"perturbation_valid": True, "failures": []},
                "launch_env_sha256": "x" * 64, "network": {}}

    def run(self, monkeypatch, authorization=None, extra_args=()):
        monkeypatch.setattr(rd.subprocess, "run", self.fake_run)
        monkeypatch.setattr(rd.rm, "BASE_PY", "/fake/python")
        monkeypatch.setattr(rd.rm, "install_matrix_observer",
                            lambda venvpy: {"installed": True})
        monkeypatch.setattr(rd.rm, "prepare_context", self.prepare_context)
        monkeypatch.setattr(rd.rm, "run_one", self.fake_run_one)
        monkeypatch.setattr(rd.rx, "b0_launch_environment",
                            lambda b0, context: ({}, {"PATH": "/usr/bin:/bin"}, {}))
        monkeypatch.setattr(rd.rx, "source_tree", types.SimpleNamespace(
            restore=lambda *a, **k: {"restored_count": 0, "deleted_count": 0,
                                     "cache_files_removed_count": 0},
            verify=lambda *a, **k: {"identical_to_artifact": True}))
        if authorization is None:
            authorization = {"D_admission": True}
        return rd.admit_one_d(
            "demo", None, None, {}, self.phase, locks_path=self.locks,
            wheel_root=self.store, roots_path=self.roots, state_dir=self.state,
            authorization=authorization, extra_pytest_args=extra_args)


def test_unauthorized_never_dispatches(tmp_path, monkeypatch):
    document, store = build(tmp_path)
    harness = Harness(tmp_path, document, store)
    result = harness.run(monkeypatch, authorization={})
    assert result["dispatched"] is False
    assert result["reason"] == "D_admission_not_authorized"
    assert harness.calls == []
    assert harness.run_one_calls == []


def test_real_admit_one_uses_one_lock_throughout(tmp_path, monkeypatch):
    document, store = build(tmp_path, {"demo_plugin": "demo.plugin"})
    harness = Harness(tmp_path, document, store)
    result = harness.run(monkeypatch, extra_args=("--randomly-seed=0",))
    assert result["dispatched"] is True, result
    outcome = result["outcome"]
    assert outcome["admitted"] is True, outcome
    assert outcome["environment_accounting"]["accounted"] is True
    install = next(call for call in harness.calls if call[:3] == ["uv", "pip", "install"])
    assert "--find-links" not in install
    installed_names = " ".join(install)
    for item in result["record"]["venv"]["install_files"]:
        assert item["filename"] in installed_names
    assert not any("==" in item for item in install)
    collect = next(call for call in harness.calls if "--collect-only" in call)
    assert "demo_plugin" in collect
    assert "--randomly-seed=0" in collect
    assert harness.run_one_calls[0]["plugin_loads"] == ("demo_plugin",)
    assert harness.run_one_calls[0]["entry_argv"][-1] == "--randomly-seed=0"
    assert outcome["M1"] == {"applied": False}


def test_explicit_disable_is_respected(tmp_path, monkeypatch):
    document, store = build(tmp_path, {"demo_plugin": "demo.plugin"})
    harness = Harness(tmp_path, document, store)
    roots = json.loads(harness.roots.read_text())
    roots["projects"][0]["pytest_config_as_data"]["addopts"] = ["-p", "no:demo_plugin"]
    harness.roots.write_text(json.dumps(roots))
    result = harness.run(monkeypatch)
    collect = next(call for call in harness.calls if "--collect-only" in call)
    assert "demo_plugin" not in collect
    assert result["outcome"]["disabled"] == ["demo_plugin"]


def test_second_call_reuses_completed_attempt(tmp_path, monkeypatch):
    document, store = build(tmp_path)
    harness = Harness(tmp_path, document, store)
    harness.run(monkeypatch)
    calls = len(harness.calls)
    second = harness.run(monkeypatch)
    assert second["dispatched"] is False
    assert second["reason"] == "attempt_already_complete"
    assert len(harness.calls) == calls


def test_drift_refused_before_attempt(tmp_path, monkeypatch):
    document, store = build(tmp_path)
    harness = Harness(tmp_path, document, store)
    row = document["projects"][0]["lock"]["rows"][0]
    target = harness.store / row["sha256"] / row["filename"]
    target.write_bytes(target.read_bytes() + b"tamper")
    with pytest.raises(rd.DRefused):
        harness.run(monkeypatch)
    assert not (harness.state / "demo" / "attempt.json").exists()
    assert harness.run_one_calls == []


def test_wrong_candidate_identity_refused(tmp_path, monkeypatch):
    document, store = build(tmp_path)
    candidate = document["projects"][0]["candidate_wheel"]
    old = store / candidate["sha256"] / candidate["filename"]
    with zipfile.ZipFile(old, "w") as archive:
        archive.writestr("demo-1.0.dist-info/METADATA",
                         "Metadata-Version: 2.1\nName: other\nVersion: 9.9\n")
    new_sha = hashlib.sha256(old.read_bytes()).hexdigest()
    candidate["sha256"] = new_sha
    new_path = store / new_sha / candidate["filename"]
    new_path.parent.mkdir(parents=True, exist_ok=True)
    old.replace(new_path)
    harness = Harness(tmp_path, document, store)
    with pytest.raises(rd.DRefused):
        harness.run(monkeypatch)
    assert harness.run_one_calls == []
