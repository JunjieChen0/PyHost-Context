"""ClawsGO Science Agent：用 pip（仅 wheel）做 D 档联合解析。

- 目标 CPython 3.11.9 / manylinux2014 x86_64；
- `--only-binary=:all:`，禁止 sdist 构建与动态元数据；
- `--isolated` + 官方 PyPI 索引；
- 先 R0（pytest==7.2.0），失败或按声明排除后 R1 解除共同钉；
- 输出每项精确锁或具名失败，附每条根需求的满足性与来源 URL/哈希。

不安装（`--dry-run`）、不执行候选。
"""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import subprocess
import tempfile

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
ROOT = NEXT.parent
STATE = NEXT / "state" / "d_toolchain"
ROOTS_FILE = HERE / "D_ACQUISITION_ROOTS.json"
LOCAL = STATE / "D_LOCAL_WHEEL_INVENTORY.json"
OUT = STATE / "D_RESOLUTION_LOCKS.json"
LOG = STATE / "D_RESOLUTION_LOG.jsonl"
PER = STATE / "per_project_pip"
PIP = str(NEXT.parents[2] / "pycompat-env-select" / "runtime" / "interpreters"
          / "cpython-3.11.9-linux-x86_64-gnu" / "bin" / "python3.11")
PYTEST_PIN = "7.2.0"
TARGET_ARGS = ["--only-binary=:all:", "--python-version", "3.11",
               "--implementation", "cp", "--abi", "cp311",
               "--platform", "manylinux2014_x86_64",
               "--index-url", "https://pypi.org/simple"]
POLICY = {"resolver": "pip 24.1.2 (isolated, wheel-only, manylinux2014_x86_64/cp311)",
          "version_order": "pip default: newest satisfying versions",
          "file_tiebreak": "pip wheel-tag preference",
          "local_preference": "exact-version local wheel bytes used when present",
          "pytest_stage_cap": 2, "r0_pin": f"pytest=={PYTEST_PIN}",
          "r1": "drop the common pytest pin only when R0 is unsatisfiable"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def wheel_compatible(filename):
    """文件名标签是否匹配 CPython 3.11 / manylinux2014（glibc 2.17）/ x86_64。"""
    if not str(filename).endswith(".whl"):
        return False
    parts = str(filename)[:-4].split("-")
    if len(parts) < 5:
        return False
    python_tag, abi_tag, platform_tag = parts[-3].lower(), parts[-2].lower(), parts[-1].lower()

    def platform_ok(tag):
        if tag in ("any", "linux_x86_64", "manylinux1_x86_64",
                   "manylinux2010_x86_64", "manylinux2014_x86_64"):
            return True
        match = re.fullmatch(r"manylinux_2_(\d+)_x86_64", tag)
        return bool(match and int(match.group(1)) <= 17)

    if not any(platform_ok(tag) for tag in platform_tag.split(".")):
        return False
    if python_tag in ("py3", "py2.py3"):
        return abi_tag in ("none", "abi3")
    if python_tag.startswith("cp") and python_tag[2:].isdigit():
        major_minor = int(python_tag[2:])
        if major_minor == 311:
            return abi_tag in ("cp311", "abi3", "none")
        if abi_tag == "abi3" and major_minor <= 311:
            return True
    return False


def log(event):
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at": now(), **event}, ensure_ascii=False) + "\n")


def load_local():
    document = json.loads(LOCAL.read_text(encoding="utf-8"))
    index = {}
    for entry in document["entries"]:
        key = (canonicalize_name(entry["wheel_name"]), entry["version"])
        index.setdefault(key, []).append(entry)
    return index


def run_pip(project, version, requirements, stage):
    with tempfile.TemporaryDirectory(dir="/tmp") as target:
        report = pathlib.Path(target) / "report.json"
        argv = [PIP, "-m", "pip", "--isolated", "install", "--dry-run",
                "--ignore-installed", "--no-deps"] + []  # placeholder
        # 需要依赖解析，不能 --no-deps。
        argv = [PIP, "-m", "pip", "--isolated", "install", "--dry-run",
                "--ignore-installed"] + TARGET_ARGS + ["--report", str(report),
                f"{project}=={version}"] + requirements
        env = dict(os.environ)
        env["PIP_CONFIG_FILE"] = os.devnull
        env["PIP_NO_INPUT"] = "1"
        started = now()
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=2400, env=env)
        except subprocess.TimeoutExpired as expired:
            log({"event": "pip_timeout", "project": project, "stage": stage,
                 "started_at": started, "timeout_seconds": 2400})
            return {"ok": False, "stage": stage, "returncode": "timeout",
                    "stderr_tail": str(expired)[-2000:], "argv": argv,
                    "timeout_seconds": 2400}
        log({"event": "pip_resolution", "project": project, "stage": stage,
             "returncode": proc.returncode, "started_at": started,
             "argv": argv[:8] + ["..."], "stderr_tail": proc.stderr[-800:]})
        if proc.returncode != 0 or not report.is_file():
            return {"ok": False, "stage": stage, "returncode": proc.returncode,
                    "stderr_tail": proc.stderr[-2000:], "argv": argv}
        return {"ok": True, "stage": stage,
                "report": json.loads(report.read_text(encoding="utf-8"))}


def evaluate(roots, selected):
    """根需求与全锁约束的满足性。selected: canonical name -> version."""
    rows = []
    ok = True
    for raw in roots:
        req = Requirement(raw)
        name = canonicalize_name(req.name)
        version = selected.get(name)
        satisfied = bool(version and req.specifier.contains(Version(version)))
        rows.append({"requirement": raw, "name": name, "selected": version,
                     "satisfied": satisfied})
        ok = ok and satisfied
    return ok, rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    roots_doc = json.loads(ROOTS_FILE.read_text(encoding="utf-8"))
    local = load_local()
    projects = roots_doc["projects"]
    if args.project:
        projects = [p for p in projects if p["project"] == args.project]
    results = []
    roots_hash = hashlib.sha256(ROOTS_FILE.read_bytes()).hexdigest()
    for project in projects:
        name = project["project"]
        if str(project.get("status", "")).startswith("static_excluded"):
            entry = {"project": name, "version": project["version"],
                     "status": "static_excluded", "reason": project["status"],
                     "project_source_sha256": project["source_sha256"],
                     "root_count": 0, "pytest_version": None,
                     "roots_sha256": roots_hash}
            results.append(entry)
            PER.mkdir(parents=True, exist_ok=True)
            (PER / f"{name}.json").write_text(json.dumps(
                entry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
            continue
        cached = PER / f"{name}.json"
        if cached.is_file() and not args.force:
            entry = json.loads(cached.read_text(encoding="utf-8"))
            if entry.get("roots_sha256") == roots_hash \
                    and entry.get("version") == project["version"]:
                results.append(entry)
                log({"event": "project_reused", "project": name,
                     "status": entry.get("status")})
                continue
        roots, rejected = [], []
        for item in project["requirements"]:
            req = Requirement(item["raw"])
            marker = req.marker
            if marker:
                env = {**roots_doc["target"], "extra": ""}
                try:
                    if not marker.evaluate(env):
                        rejected.append({"raw": item["raw"], "reason": "marker_false"})
                        continue
                except Exception:  # noqa: BLE001
                    pass
            if req.url:
                rejected.append({"raw": item["raw"], "reason": "url_not_authorized"})
                continue
            roots.append(item["raw"])
        direct_pytest = [Requirement(r) for r in roots
                         if canonicalize_name(Requirement(r).name) == "pytest"]
        blocked_urls = [item for item in rejected if item["reason"] == "url_not_authorized"]
        if blocked_urls:
            entry = {"project": name, "version": project["version"],
                     "status": "resolution_blocked", "reason": "url_root_not_authorized",
                     "blocked_roots": blocked_urls, "rejected_roots": rejected,
                     "project_source_sha256": project["source_sha256"],
                     "pytest_version": None, "roots_sha256": roots_hash}
            results.append(entry)
            PER.mkdir(parents=True, exist_ok=True)
            (PER / f"{name}.json").write_text(json.dumps(
                entry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
            log({"event": "project_blocked", "project": name,
                 "reason": "url_root_not_authorized"})
            continue
        minversion = project.get("pytest_minversion")
        r0_excluded = any(r.specifier and not r.specifier.contains(PYTEST_PIN)
                          for r in direct_pytest)
        if minversion:
            try:
                if not SpecifierSet(f">={minversion}").contains(PYTEST_PIN):
                    r0_excluded = True
            except Exception:  # noqa: BLE001
                pass
        stages = []
        outcome = None
        if not r0_excluded:
            stages.append("R0")
            outcome = run_pip(name, project["version"], roots + [f"pytest=={PYTEST_PIN}"], "R0")
        if outcome is None or not outcome["ok"]:
            stages.append("R1")
            r0_error = None if outcome is None else {
                "returncode": outcome.get("returncode"),
                "stderr_tail": outcome.get("stderr_tail")}
            outcome = run_pip(name, project["version"], roots, "R1")
            outcome["r0_error"] = r0_error
            outcome["r0_excluded"] = r0_excluded
        if not outcome["ok"]:
            results.append({"project": name, "version": project["version"],
                            "status": ("resolution_timeout"
                                       if outcome.get("returncode") == "timeout"
                                       else "resolution_failed"),
                            "stages": stages,
                            "outcome": outcome, "rejected_roots": rejected,
                            "project_source_sha256": project["source_sha256"],
                            "pytest_version": None,
                            "root_count": len(roots),
                            "r0_excluded": r0_excluded,
                            "roots_sha256": roots_hash})
            PER.mkdir(parents=True, exist_ok=True)
            (PER / f"{name}.json").write_text(json.dumps(
                results[-1], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
            continue
        install = outcome["report"].get("install") or []
        rows = []
        candidate_row = None
        selected = {}
        for item in install:
            meta = item.get("metadata") or {}
            pkg = canonicalize_name(meta.get("name") or "")
            ver = str(meta.get("version"))
            download = item.get("download_info") or {}
            archive = download.get("archive_info") or {}
            hashes = archive.get("hashes") or {}
            digest = hashes.get("sha256")
            url = download.get("url")
            filename = url.rsplit("/", 1)[-1] if url else None
            row = {"name": pkg, "version": ver, "filename": filename,
                   "sha256": digest, "url": url,
                   "requires_python": meta.get("requires_python"),
                   "dependencies": meta.get("requires_dist") or [],
                   "requested_extras": sorted(item.get("requested_extras") or []),
                   "source": "pypi"}
            local_rows = [item for item in (local.get((pkg, ver)) or [])
                          if wheel_compatible(item["filename"])]
            if local_rows:
                chosen = sorted(local_rows, key=lambda r: r["filename"])[0]
                row.update({"source": "local", "local_path": chosen["path"],
                            "filename": chosen["filename"], "sha256": chosen["sha256"],
                            "bytes": chosen["bytes"]})
            selected[pkg] = ver
            if pkg == canonicalize_name(name):
                candidate_row = row
            else:
                rows.append(row)
        roots_ok, root_rows = evaluate(roots, selected)
        # 全锁依赖一致性：每条 Requires-Dist 必须在锁内有满足的版本。
        edges = []
        for row in rows + ([candidate_row] if candidate_row else []):
            for raw in row["dependencies"]:
                dep = Requirement(raw)
                dep_name = canonicalize_name(dep.name)
                dep_version = selected.get(dep_name)
                applies = True
                if dep.marker:
                    env = {**roots_doc["target"], "extra": ""}
                    try:
                        applies = dep.marker.evaluate(env)
                    except Exception:  # noqa: BLE001
                        applies = True
                if not applies:
                    continue
                satisfied = bool(dep_version and dep.specifier.contains(Version(dep_version)))
                edges.append({"from": row["name"], "requirement": raw,
                              "to": dep_name, "selected": dep_version,
                              "satisfied": satisfied})
        edges_ok = all(edge["satisfied"] for edge in edges)
        pytest_row = next((r for r in rows if r["name"] == "pytest"), None)
        results.append({
            "project": name, "version": project["version"],
            "project_source_sha256": project["source_sha256"],
            "status": "locked" if roots_ok and edges_ok else "lock_invalid",
            "stages": stages,
            "r0_excluded": r0_excluded,
            "pytest_version": pytest_row["version"] if pytest_row else None,
            "lock": {"stage": outcome["stage"], "rows": rows,
                     "candidate": candidate_row,
                     "roots_satisfied": roots_ok, "root_checks": root_rows,
                     "dependency_edges_satisfied": edges_ok,
                     "dependency_edges": edges},
            "rejected_roots": rejected, "root_count": len(roots),
            "roots_sha256": roots_hash,
        })
        PER.mkdir(parents=True, exist_ok=True)
        (PER / f"{name}.json").write_text(json.dumps(
            results[-1], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        log({"event": "project_locked", "project": name, "stage": outcome["stage"],
             "packages": len(rows), "roots_ok": roots_ok, "edges_ok": edges_ok,
             "pytest": pytest_row["version"] if pytest_row else None})
    # 由逐项结果文件合成总表，支持中断续跑与单项目重解。
    if PER.is_dir():
        merged = []
        for project in roots_doc["projects"]:
            cached = PER / f"{project['project']}.json"
            if cached.is_file():
                merged.append(json.loads(cached.read_text(encoding="utf-8")))
            else:
                merged.append({"project": project["project"], "status": "not_resolved"})
        results = merged
    document = {"schema_version": "pyhost-context/d-resolution-locks/2.0",
                "generated_at": now(), "resolver_version": POLICY["resolver"],
                "policy": POLICY, "target": roots_doc["target"],
                "roots_sha256": hashlib.sha256(ROOTS_FILE.read_bytes()).hexdigest(),
                "local_inventory_sha256": hashlib.sha256(LOCAL.read_bytes()).hexdigest(),
                "projects": results}
    OUT.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(json.dumps({"out": str(OUT),
                      "sha256": hashlib.sha256(OUT.read_bytes()).hexdigest(),
                      "projects": [{"project": r["project"], "status": r["status"],
                                    "stage": (r.get("lock") or {}).get("stage") or None,
                                    "packages": len((r.get("lock") or {}).get("rows") or []),
                                    "pytest": r.get("pytest_version")}
                                   for r in results]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
