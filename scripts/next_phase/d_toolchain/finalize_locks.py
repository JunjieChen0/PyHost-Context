"""ClawsGO Science Agent：把 pip 解析结果整理为可执行的 D 锁。

- 候选自身从 `lock.candidate` 提升为 `candidate_wheel`（文件名/哈希/URL/标签）；
- 保持候选版本冻结，不替换、不升级；
- 只在官方元数据核验已通过时写出；任何缺口显式登记。
不安装、不执行候选。
"""
import argparse
import hashlib
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
STATE = NEXT / "state" / "d_toolchain"
LOCKS = STATE / "D_RESOLUTION_LOCKS.json"
OUT = STATE / "D_RESOLUTION_LOCKS_WITH_CANDIDATE.json"


def parse_tags(filename):
    parts = filename[:-4].split("-")
    if len(parts) < 5:
        return None
    return {"python_tag": parts[-3], "abi_tag": parts[-2], "platform_tag": parts[-1]}


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--locks", type=pathlib.Path, default=LOCKS)
    args = parser.parse_args()
    document = json.loads(args.locks.read_text(encoding="utf-8"))
    problems = []
    for project in document["projects"]:
        if project.get("status") != "locked":
            continue
        name = project["project"]
        candidate = project["lock"].get("candidate")
        if not candidate:
            problems.append(f"candidate_missing:{name}")
            project["candidate_wheel"] = None
            continue
        tags = parse_tags(candidate["filename"] or "")
        if not tags:
            problems.append(f"candidate_filename_unparsable:{name}:{candidate['filename']}")
            project["candidate_wheel"] = None
            continue
        official = candidate.get("official") or {}
        if not official.get("sha256"):
            problems.append(f"candidate_not_officially_verified:{name}")
        if official.get("sha256") and official["sha256"] != candidate.get("sha256"):
            problems.append(f"candidate_sha_mismatch:{name}")
        project["candidate_wheel"] = {
            "name": candidate["name"], "version": candidate["version"],
            "filename": candidate["filename"], "sha256": candidate.get("sha256"),
            "bytes": candidate.get("bytes") or official.get("bytes"),
            "url": candidate.get("url") or official.get("url"),
            "source": candidate.get("source"), **tags,
            "official": official,
        }
        log = project["lock"].get("rows") or []
        if not log:
            problems.append(f"empty_lock:{name}")
        if not project.get("pytest_version"):
            problems.append(f"pytest_unresolved:{name}")
    document["candidate_finalize_problems"] = problems
    document["locks_sha256"] = sha(args.locks)
    OUT.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(json.dumps({"out": str(OUT), "sha256": sha(OUT), "problems": problems,
                      "projects": [{"project": p["project"], "status": p["status"],
                                    "candidate": (p.get("candidate_wheel") or {}).get("filename"),
                                    "rows": len((p.get("lock") or {}).get("rows") or [])}
                                   for p in document["projects"]]},
                     ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
