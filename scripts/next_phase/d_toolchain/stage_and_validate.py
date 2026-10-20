"""ClawsGO Science Agent：从轮子清单重建逐项目离线 wheel 目录并校验 D 锁。

- 共享库按 sha256 存放，本脚本以硬链接（跨设备时复制）为每个项目铺出平铺目录；
- 逐项运行 d_adapter.preflight / validate_lock（含候选自身）；
- 只读校验，不安装、不导入、不执行候选。
"""
import argparse
import hashlib
import json
import os
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
STATE = NEXT / "state" / "d_toolchain"
MANIFEST = STATE / "D_WHEEL_MANIFEST.json"
LOCKS = STATE / "D_RESOLUTION_LOCKS_WITH_CANDIDATE.json"
OUT = STATE / "D_LOCK_VALIDATION.json"
sys.path.insert(0, str(HERE))

import d_adapter  # noqa: E402


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=pathlib.Path, default=MANIFEST)
    parser.add_argument("--locks", type=pathlib.Path, default=LOCKS)
    parser.add_argument("--stage", type=pathlib.Path, default=STATE / "stage")
    parser.add_argument("--out", type=pathlib.Path, default=OUT)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    locks = json.loads(args.locks.read_text(encoding="utf-8"))
    by_project = {}
    for row in manifest["rows"]:
        by_project.setdefault(row["project"], []).append(row)
    results = []
    problems = []
    for project in locks["projects"]:
        if project.get("status") != "locked":
            continue
        name = project["project"]
        target = args.stage / name
        target.mkdir(parents=True, exist_ok=True)
        rows = by_project.get(name) or []
        if not rows:
            problems.append(f"manifest_rows_missing:{name}")
            continue
        for row in rows:
            source = pathlib.Path(row["stored_path"])
            destination = target / row["filename"]
            if not destination.is_file() or sha(destination) != row["sha256"]:
                if destination.exists():
                    destination.unlink()
                try:
                    os.link(source, destination)
                except OSError:
                    shutil.copyfile(source, destination)
            if sha(destination) != row["sha256"]:
                problems.append(f"staged_sha:{name}:{row['filename']}")
        candidate = project.get("candidate_wheel")
        if not candidate:
            problems.append(f"candidate_missing:{name}")
            continue
        validation = d_adapter.validate_lock(project, target, candidate)
        results.append({"project": name, "ok": validation["ok"],
                        "problems": validation["problems"],
                        "plugin_loads": validation["plugins"]["loads"],
                        "plugin_conflicts": validation["plugins"]["conflicts"],
                        "wheel_count": len(rows), "stage_dir": str(target)})
        problems.extend(f"{name}:{problem}" for problem in validation["problems"])
    document = {"schema_version": "pyhost-context/d-lock-validation/1.0",
                "manifest_sha256": sha(args.manifest),
                "locks_sha256": sha(args.locks),
                "ok": not problems, "problems": problems, "projects": results}
    args.out.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(json.dumps({"out": str(args.out), "sha256": sha(args.out), "ok": document["ok"],
                      "problems": problems,
                      "projects": [{"project": r["project"], "ok": r["ok"],
                                    "wheels": r["wheel_count"],
                                    "plugins": len(r["plugin_loads"])} for r in results]},
                     ensure_ascii=False, indent=2))
    return 0 if document["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
