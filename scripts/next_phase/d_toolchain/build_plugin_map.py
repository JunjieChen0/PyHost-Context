"""ClawsGO Science Agent：从 D 锁与已核验 wheel 生成显式 pytest11 映射。

读取锁中**全部** wheel 的 entry_points.txt，按分发/版本/哈希记录，
并给出每项目最终 `-p` 列表、冲突与项目自带插件。不安装、不执行候选。
"""
import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
STATE = NEXT / "state" / "d_toolchain"
LOCKS = STATE / "D_RESOLUTION_LOCKS_WITH_CANDIDATE.json"
MANIFEST = STATE / "D_WHEEL_MANIFEST.json"
OUT = STATE / "D_PLUGIN_MAP.json"
sys.path.insert(0, str(HERE))

import d_adapter  # noqa: E402


def project_local_plugins(roots_file, project):
    document = json.loads(roots_file.read_text(encoding="utf-8"))
    for row in document["projects"]:
        if row["project"] == project:
            config = row.get("pytest_config_as_data") or {}
            out = []
            addopts = config.get("addopts") or []
            if isinstance(addopts, str):
                addopts = addopts.split()
            for arg in addopts:
                if str(arg).startswith("-p") and len(str(arg)) > 2:
                    out.append(str(arg)[2:])
            return out
    return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--locks", type=pathlib.Path, default=LOCKS)
    parser.add_argument("--manifest", type=pathlib.Path, default=MANIFEST)
    parser.add_argument("--wheel-root", type=pathlib.Path, default=STATE / "wheels")
    parser.add_argument("--roots", type=pathlib.Path, default=HERE / "D_ACQUISITION_ROOTS.json")
    parser.add_argument("--out", type=pathlib.Path, default=OUT)
    args = parser.parse_args()
    locks = json.loads(args.locks.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    manifest_index = {}
    for row in manifest["rows"]:
        manifest_index[(row["project"], row["filename"])] = row
    projects = []
    problems = []
    for project in locks["projects"]:
        if project.get("status") != "locked":
            continue
        name = project["project"]
        rows = []
        for row in project["lock"]["rows"]:
            entry = manifest_index.get((name, row["filename"]))
            if not entry:
                problems.append(f"manifest_missing:{name}:{row['filename']}")
                continue
            path = pathlib.Path(entry["stored_path"])
            if not path.is_file():
                problems.append(f"wheel_missing:{name}:{row['filename']}")
                continue
            rows.append({"name": row["name"], "version": row["version"],
                         "filename": row["filename"], "sha256": row["sha256"],
                         "path": str(path),
                         "entries": d_adapter.wheel_entries(path)})
        if project.get("candidate_wheel"):
            candidate = project["candidate_wheel"]
            entry = None
            for m in manifest["rows"]:
                if m["project"] == name and m["filename"] == candidate["filename"]:
                    entry = m
                    break
            if entry and pathlib.Path(entry["stored_path"]).is_file():
                path = pathlib.Path(entry["stored_path"])
                rows.append({"name": name, "version": project["version"],
                             "filename": candidate["filename"],
                             "sha256": candidate["sha256"], "path": str(path),
                             "entries": d_adapter.wheel_entries(path),
                             "candidate": True})
        owners = {}
        entries = []
        for row in rows:
            for entry_name, module in (row["entries"] or {}).items():
                entries.append({"distribution": row["name"], "version": row["version"],
                                "filename": row["filename"], "sha256": row["sha256"],
                                "entry_name": entry_name, "module": module,
                                "candidate": row.get("candidate", False)})
                owners.setdefault(entry_name, set()).add(row["name"])
        conflicts = [{"entry_name": entry, "distributions": sorted(dist)}
                     for entry, dist in owners.items() if len(dist) > 1]
        local_plugins = project_local_plugins(args.roots, name)
        projects.append({
            "project": name, "version": project["version"],
            "pytest_version": project.get("pytest_version"),
            "candidate_wheel": project.get("candidate_wheel"),
            "entries": entries,
            "loads": sorted(owners),
            "conflicts": conflicts,
            "project_local_plugins": local_plugins,
            "final_pytest_plugin_args": [arg for entry in sorted(owners)
                                         for arg in ("-p", entry)]
            + [arg for plugin in local_plugins for arg in ("-p", plugin)],
        })
        if conflicts:
            problems.append(f"plugin_conflict:{name}:{conflicts}")
    document = {"schema_version": "pyhost-context/d-plugin-map/1.0",
                "locks_sha256": __import__("hashlib").sha256(
                    args.locks.read_bytes()).hexdigest(),
                "manifest_sha256": __import__("hashlib").sha256(
                    args.manifest.read_bytes()).hexdigest(),
                "problems": problems, "projects": projects}
    args.out.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(json.dumps({"out": str(args.out), "sha256": __import__("hashlib").sha256(
        args.out.read_bytes()).hexdigest(), "problems": problems,
        "projects": [{"project": p["project"], "loads": p["loads"]} for p in projects]},
        ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
