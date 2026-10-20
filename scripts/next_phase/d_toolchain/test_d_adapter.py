"""ClawsGO Science Agent：D 适配器的合成正反例（不安装、不执行候选）。"""
import json
import pathlib
import sys
import zipfile

import pytest

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
ROOT = NEXT.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(NEXT.parent / "tooling"))
sys.path.insert(0, str(NEXT.parent / "tooling" / "admission_runner"))
sys.path.insert(0, str(NEXT.parent / "matrix" / "tooling"))
sys.path.append(str(HERE / "stubs"))

import d_adapter  # noqa: E402


def make_wheel(directory, name, version, entry_points=None, requires=()):
    filename = f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
    path = pathlib.Path(directory) / filename
    metadata = [f"Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
    metadata += [f"Requires-Dist: {item}" for item in requires]
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{name.replace('-', '_')}-{version}.dist-info/METADATA",
                         "\n".join(metadata) + "\n")
        if entry_points:
            body = "[pytest11]\n" + "\n".join(f"{k} = {v}" for k, v in entry_points.items())
            archive.writestr(f"{name.replace('-', '_')}-{version}.dist-info/entry_points.txt",
                             body + "\n")
    return {"filename": filename, "name": name, "version": version,
            "sha256": d_adapter.sha(path), "source": "local"}


def lock_for(rows, project="demo", pytest_version="9.1.1"):
    return {"project": project, "status": "locked", "pytest_version": pytest_version,
            "lock": {"stage": "R1", "rows": rows}}


def candidate():
    return {"name": "demo", "version": "1.0", "filename": "demo-1.0-py3-none-any.whl",
            "sha256": "0" * 64}


def candidate_in(directory):
    row = make_wheel(directory, "demo", "1.0")
    row["sha256"] = d_adapter.sha(pathlib.Path(directory) / row["filename"])
    return row


def test_preflight_positive(tmp_path):
    rows = [make_wheel(tmp_path, "pytest", "9.1.1"),
            make_wheel(tmp_path, "pytest-asyncio", "1.4.0", {"asyncio": "pytest_asyncio.plugin"})]
    result = d_adapter.preflight(lock_for(rows), tmp_path)
    assert result["ok"], result
    assert len(result["receipts"]) == 2


def test_preflight_hash_mismatch(tmp_path):
    rows = [make_wheel(tmp_path, "pytest", "9.1.1")]
    rows[0]["sha256"] = "f" * 64
    result = d_adapter.preflight(lock_for(rows), tmp_path)
    assert not result["ok"]
    assert any("wheel_sha256" in problem for problem in result["problems"])


def test_plugin_map_and_conflict(tmp_path):
    rows = [make_wheel(tmp_path, "pytest-asyncio", "1.4.0", {"asyncio": "pytest_asyncio.plugin"}),
            make_wheel(tmp_path, "pytest-cov", "7.1.0", {"pytest_cov": "pytest_cov.plugin"})]
    result = d_adapter.plugin_map(lock_for(rows), tmp_path)
    assert result["ok"], result
    assert result["loads"] == ["asyncio", "pytest_cov"]
    rows.append(make_wheel(tmp_path, "other", "1.0", {"asyncio": "other.plugin"}))
    conflicted = d_adapter.plugin_map(lock_for(rows), tmp_path)
    assert not conflicted["ok"]
    assert conflicted["conflicts"][0]["entry_name"] == "asyncio"


def test_install_plan_pins_all_and_candidate(tmp_path):
    rows = [make_wheel(tmp_path, "pytest", "9.1.1"),
            make_wheel(tmp_path, "pytest-asyncio", "1.4.0")]
    plan = d_adapter.install_plan(lock_for(rows), tmp_path, candidate_in(tmp_path))
    assert "pytest==9.1.1" in plan["pins"]
    assert "pytest-asyncio==1.4.0" in plan["pins"]
    assert "demo==1.0" in plan["pins"]
    assert plan["no_deps"] is True
    argv = d_adapter.install_argv("venvpy", tmp_path, plan)
    for item in plan["files"]:
        assert item["path"] in argv
    assert "--find-links" not in argv
    assert not any(arg.endswith("==9.1.1") for arg in argv)


def test_inject_plugins_respects_explicit_disable():
    argv = ["python", "-m", "pytest", "-p", "no:randomly"]
    d_adapter.inject_plugins(argv, ["randomly", "asyncio"])
    assert "randomly" not in argv
    assert argv[-2:] == ["-p", "asyncio"]
    joined = ["python", "-m", "pytest", "-pno:randomly"]
    d_adapter.inject_plugins(joined, ["randomly"])
    assert "randomly" not in joined


def test_inject_plugins_idempotent():
    argv = ["python", "-m", "pytest", "--collect-only"]
    d_adapter.inject_plugins(argv, ["asyncio", "pytest_cov"])
    assert argv[-4:] == ["-p", "asyncio", "-p", "pytest_cov"]
    d_adapter.inject_plugins(argv, ["asyncio"])
    assert argv.count("asyncio") == 1
    other = ["pip", "download"]
    d_adapter.inject_plugins(other, ["asyncio"])
    assert other == ["pip", "download"]


def test_instrument_scope_injects_and_restores(monkeypatch):
    import run_extension as rx
    import run_matrix as rm
    seen = {"run": [], "run_one": []}

    def fake_run(argv, *args, **kwargs):
        seen["run"].append(list(argv))
        return None

    def fake_run_one(*args, **kwargs):
        seen["run_one"].append(kwargs.get("plugin_loads"))
        return None

    monkeypatch.setattr(d_adapter, "pytest_version", lambda venvpy: "9.1.1")
    monkeypatch.setattr(rx, "subprocess", type("S", (), {"run": staticmethod(fake_run)}))
    monkeypatch.setattr(rm, "run_one", fake_run_one)
    with d_adapter.instrument_scope(lock_for([]), None, "venv", ["asyncio"]):
        rx.subprocess.run(["python", "-m", "pytest", "--collect-only"])
        rm.run_one(plugin_loads=())
    assert seen["run"][0][-2:] == ["-p", "asyncio"]
    assert seen["run_one"] == [("asyncio",)]
    assert rx.subprocess.run is fake_run


def test_instrument_scope_rejects_pytest_mismatch(monkeypatch):
    monkeypatch.setattr(d_adapter, "pytest_version", lambda venvpy: "8.4.0")
    with pytest.raises(d_adapter.LockError):
        with d_adapter.instrument_scope(lock_for([]), None, "venv", []):
            pass


def test_verify_environment(monkeypatch):
    rows = [{"name": "pytest", "version": "9.1.1",
             "filename": "pytest-9.1.1-py3-none-any.whl", "sha256": "0" * 64}]
    class Proc:
        returncode = 0
        stdout = "pytest==9.1.1\ndemo==1.0\n"
    monkeypatch.setattr("subprocess.run", lambda *a, **k: Proc())
    result = d_adapter.verify_environment("venv", lock_for(rows), candidate())
    assert result["ok"], result


def test_candidate_artifact_missing():
    with pytest.raises(d_adapter.LockError):
        d_adapter.candidate_artifact(lock_for([]), "/tmp")


def test_prepare_consumes_one_lock(tmp_path):
    rows = [make_wheel(tmp_path, "pytest", "9.1.1"),
            make_wheel(tmp_path, "pytest-asyncio", "1.4.0", {"asyncio": "pytest_asyncio.plugin"})]
    document = {"projects": [lock_for(rows)]}
    document["projects"][0]["candidate_wheel"] = candidate_in(tmp_path)
    prepared = d_adapter.prepare(document, "demo", tmp_path)
    assert prepared["loads"] == ["asyncio"]
    assert "pytest==9.1.1" in prepared["plan"]["pins"]
    assert "demo==1.0" in prepared["plan"]["pins"]
    assert prepared["pytest_version"] == "9.1.1"


def test_prepare_refuses_invalid_lock(tmp_path):
    rows = [make_wheel(tmp_path, "pytest", "9.1.1")]
    document = {"projects": [lock_for(rows)]}
    candidate = candidate_in(tmp_path)
    candidate["sha256"] = "f" * 64
    document["projects"][0]["candidate_wheel"] = candidate
    with pytest.raises(d_adapter.LockError):
        d_adapter.prepare(document, "demo", tmp_path)


def test_prepare_refuses_wrong_candidate_identity(tmp_path):
    rows = [make_wheel(tmp_path, "pytest", "9.1.1")]
    document = {"projects": [lock_for(rows)]}
    candidate = make_wheel(tmp_path, "demo", "1.0")
    candidate["sha256"] = d_adapter.sha(tmp_path / candidate["filename"])
    # 文件身份写错：锁称 demo==1.0，wheel 内实际是 other==9.9
    wrong = pathlib.Path(tmp_path) / candidate["filename"]
    with zipfile.ZipFile(wrong, "w") as archive:
        archive.writestr("demo-1.0.dist-info/METADATA",
                         "Metadata-Version: 2.1\nName: other\nVersion: 9.9\n")
    candidate["sha256"] = d_adapter.sha(wrong)
    document["projects"][0]["candidate_wheel"] = candidate
    with pytest.raises(d_adapter.LockError):
        d_adapter.prepare(document, "demo", tmp_path)


def test_candidate_pytest11_is_loaded(tmp_path):
    rows = [make_wheel(tmp_path, "pytest", "9.1.1")]
    document = {"projects": [lock_for(rows)]}
    candidate = make_wheel(tmp_path, "demo", "1.0", {"demo_plugin": "demo.plugin"})
    candidate["sha256"] = d_adapter.sha(tmp_path / candidate["filename"])
    document["projects"][0]["candidate_wheel"] = candidate
    prepared = d_adapter.prepare(document, "demo", tmp_path)
    assert "demo_plugin" in prepared["loads"]
    assert any(entry["candidate"] and entry["entry_name"] == "demo_plugin"
               for entry in prepared["plugins"]["entries"])


def test_same_version_different_bytes_rejected(tmp_path):
    rows = [make_wheel(tmp_path, "pytest", "9.1.1")]
    first = make_wheel(tmp_path, "attrs", "26.1.0")
    second = dict(first)
    second["filename"] = "attrs-26.1.0-py3-none-any-2.whl"
    second["sha256"] = "f" * 64
    result = d_adapter.validate_lock(lock_for(rows + [first, second]), tmp_path,
                                     candidate_in(tmp_path))
    assert not result["ok"]
    assert any("duplicate_same_version:attrs" in problem for problem in result["problems"])


def test_validate_lock_positive(tmp_path):
    rows = [make_wheel(tmp_path, "pytest", "9.1.1"),
            make_wheel(tmp_path, "pytest-asyncio", "1.4.0", {"asyncio": "pytest_asyncio.plugin"})]
    result = d_adapter.validate_lock(lock_for(rows), tmp_path, candidate_in(tmp_path))
    assert result["ok"], result


def test_validate_lock_rejects_candidate_hash(tmp_path):
    rows = [make_wheel(tmp_path, "pytest", "9.1.1")]
    bad = candidate_in(tmp_path)
    bad["sha256"] = "f" * 64
    result = d_adapter.validate_lock(lock_for(rows), tmp_path, bad)
    assert not result["ok"]
    assert any("wheel_sha256:demo" in problem for problem in result["problems"])


def test_validate_lock_duplicate_and_missing_pytest(tmp_path):
    rows = [make_wheel(tmp_path, "pytest", "9.1.1"),
            make_wheel(tmp_path, "pytest", "8.4.0")]
    result = d_adapter.validate_lock(lock_for(rows), tmp_path, candidate_in(tmp_path))
    assert not result["ok"]
    assert any("duplicate_name:pytest" in problem for problem in result["problems"])
    rows = [make_wheel(tmp_path, "attrs", "26.1.0")]
    result = d_adapter.validate_lock(lock_for(rows), tmp_path, candidate_in(tmp_path))
    assert "pytest_missing" in result["problems"]
