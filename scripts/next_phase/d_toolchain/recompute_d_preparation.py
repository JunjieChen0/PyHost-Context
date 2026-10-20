"""ClawsGO Science Agent：D 档准备包的独立复算与自检。

只读包内文件；不联网、不安装、不执行候选。
"""
import hashlib
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK_FILE = HERE / "locks" / "D_RESOLUTION_LOCKS_WITH_CANDIDATE.json"
MANIFEST = HERE / "artifacts" / "D_WHEEL_MANIFEST.json"
OFFICIAL = HERE / "artifacts" / "D_OFFICIAL_METADATA_INDEX.json"
PLUGINS = HERE / "artifacts" / "D_PLUGIN_MAP.json"
VALIDATION = HERE / "artifacts" / "D_LOCK_VALIDATION.json"
PACKAGE = HERE / "PACKAGE_MANIFEST.json"


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def check_package():
    document = json.loads(PACKAGE.read_text(encoding="utf-8"))
    problems = []
    listed = set()
    for member in document["members"]:
        path = HERE / member["path"]
        listed.add(member["path"])
        if not path.is_file():
            problems.append(f"missing:{member['path']}")
            continue
        if sha(path) != member["sha256"]:
            problems.append(f"sha256:{member['path']}")
    actual = {str(path.relative_to(HERE)) for path in HERE.rglob("*") if path.is_file()}
    actual = {name for name in actual
              if "__pycache__" not in name and ".pytest_cache" not in name}
    extra = sorted(actual - listed - {"PACKAGE_MANIFEST.json"})
    if extra:
        problems.append(f"unlisted:{extra[:10]}")
    return problems, document


def check_locks():
    document = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    problems = []
    counts = {}
    for project in document["projects"]:
        if project.get("status") != "locked":
            counts[project["project"]] = project.get("status")
            continue
        lock = project["lock"]
        names = {}
        for row in lock["rows"]:
            key = row["name"].lower().replace("_", "-")
            names.setdefault(key, set()).add(row["version"])
        duplicates = {name: sorted(versions) for name, versions in names.items()
                      if len(versions) > 1}
        if duplicates:
            problems.append(f"duplicate_names:{project['project']}:{duplicates}")
        if "pytest" not in names:
            problems.append(f"pytest_missing:{project['project']}")
        elif names["pytest"] != {str(project["pytest_version"])}:
            problems.append(f"pytest_version:{project['project']}")
        if not lock.get("roots_satisfied"):
            problems.append(f"roots_unsatisfied:{project['project']}")
        if not lock.get("dependency_edges_satisfied"):
            problems.append(f"edges_unsatisfied:{project['project']}")
        candidate = project.get("candidate_wheel") or {}
        official = candidate.get("official") or {}
        if not candidate or official.get("sha256") != candidate.get("sha256"):
            problems.append(f"candidate_unverified:{project['project']}")
        counts[project["project"]] = len(lock["rows"])
    return problems, counts


def check_official():
    index = json.loads(OFFICIAL.read_text(encoding="utf-8"))
    problems = list(index.get("problems") or [])
    seen = set()
    for entry in index["entries"]:
        name = entry["name"].lower().replace("_", "-")
        path = HERE / "artifacts" / "official" / name / f"{entry['version']}.json"
        if not path.is_file():
            problems.append(f"metadata_missing:{entry['name']}:{entry['version']}")
            continue
        if sha(path) != entry["metadata_sha256"]:
            problems.append(f"metadata_sha:{entry['name']}:{entry['version']}")
        seen.add((entry["name"].lower().replace("_", "-"), entry["version"],
                  entry["filename"], entry["file_sha256"]))
    locks = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    for project in locks["projects"]:
        if project.get("status") != "locked":
            continue
        rows = list(project["lock"]["rows"]) + [project["candidate_wheel"]]
        for row in rows:
            key = (row["name"].lower().replace("_", "-"), row["version"],
                   row["filename"], row["sha256"])
            if key not in seen:
                problems.append(f"lock_not_in_official:{project['project']}:{row['filename']}")
    return problems, len(index["entries"])


def check_plugins():
    document = json.loads(PLUGINS.read_text(encoding="utf-8"))
    locks = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    lock_index = {row["project"]: row for row in locks["projects"]}
    problems = list(document.get("problems") or [])
    summaries = {}
    for project in document["projects"]:
        owners = {}
        for entry in project["entries"]:
            owners.setdefault(entry["entry_name"], set()).add(entry["distribution"])
            row = lock_index.get(project["project"], {})
            known = {r["sha256"] for r in (row.get("lock") or {}).get("rows") or []}
            if entry.get("candidate"):
                known.add((row.get("candidate_wheel") or {}).get("sha256"))
            if entry["sha256"] not in known:
                problems.append(f"entry_unknown_sha:{project['project']}:{entry['filename']}")
        conflicts = {name: sorted(dist) for name, dist in owners.items() if len(dist) > 1}
        if conflicts:
            problems.append(f"conflict:{project['project']}:{conflicts}")
        if sorted(owners) != project["loads"]:
            problems.append(f"loads_mismatch:{project['project']}")
        summaries[project["project"]] = project["loads"]
    return problems, summaries


def check_validation():
    document = json.loads(VALIDATION.read_text(encoding="utf-8"))
    return ([] if document.get("ok") else ["validation_failed"]), document.get("ok")


def run_tests():
    import os
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           str(HERE / "tools" / "test_d_adapter.py"),
                           str(HERE / "tools" / "test_d_entry.py")],
                          capture_output=True, text=True, timeout=300,
                          cwd=str(HERE / "tools"), env=environment)
    tail = (proc.stdout or proc.stderr).strip().splitlines()[-1:]
    return proc.returncode == 0, tail[0] if tail else ""


def main():
    problems = {}
    package_problems, package = check_package()
    problems["package"] = package_problems
    lock_problems, lock_counts = check_locks()
    problems["locks"] = lock_problems
    official_problems, official_count = check_official()
    problems["official"] = official_problems
    plugin_problems, plugin_loads = check_plugins()
    problems["plugins"] = plugin_problems
    validation_problems, validation_ok = check_validation()
    problems["validation"] = validation_problems
    tests_ok, tests_tail = run_tests()
    problems["adapter_tests"] = [] if tests_ok else [tests_tail]
    ok = all(not value for value in problems.values())
    print(json.dumps({
        "ok": ok,
        "projects": lock_counts,
        "official_entries": official_count,
        "plugin_loads": plugin_loads,
        "validation_ok": validation_ok,
        "adapter_tests": tests_tail,
        "package_members": len(package["members"]),
        "problems": problems,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
