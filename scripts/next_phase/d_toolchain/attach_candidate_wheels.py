"""ClawsGO Science Agent：为每个 D 锁附加候选自身的 wheel 条目。

候选版本冻结不变；只从官方 PyPI 的该版本文件列表中选择兼容 wheel。
无兼容 wheel 时登记缺口，不做源码构建。不安装、不执行候选。
"""
import argparse
import json
import pathlib

from packaging.utils import canonicalize_name

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
STATE = NEXT / "state" / "d_toolchain"
LOCKS = STATE / "D_RESOLUTION_LOCKS.json"
META = STATE / "meta"
OUT = STATE / "D_RESOLUTION_LOCKS_WITH_CANDIDATE.json"


def find_metadata(name, version):
    cache = META / canonicalize_name(name) / f"{version}.json"
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8"))
    return None


def compatible(tags):
    py = (tags.get("python_tag") or "").lower()
    abi = (tags.get("abi_tag") or "").lower()
    plat = (tags.get("platform_tag") or "").lower()
    platform_ok = plat == "any" or plat == "linux_x86_64" or (
        plat.startswith("manylinux") and plat.endswith("_x86_64"))
    if not platform_ok:
        return False
    if py in ("py3", "py2.py3"):
        return abi in ("none", "abi3")
    if py.startswith("cp"):
        digits = py[2:]
        try:
            minor = int(digits[1]) if len(digits) == 2 and digits[0] == "3" else (
                int(digits[1:]) if len(digits) == 3 and digits[0] == "3" else -1)
        except ValueError:
            return False
        if (3, minor) == (3, 11) and abi in ("cp311", "none"):
            return True
        if abi == "abi3" and (3, minor) <= (3, 11):
            return True
    return False


def parse_tags(filename):
    parts = filename[:-4].split("-")
    if len(parts) < 5:
        return None
    return {"python_tag": parts[-3], "abi_tag": parts[-2], "platform_tag": parts[-1]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--locks", type=pathlib.Path, default=LOCKS)
    args = parser.parse_args()
    document = json.loads(args.locks.read_text(encoding="utf-8"))
    problems = []
    for project in document["projects"]:
        if project.get("status") != "locked":
            continue
        name, version = project["project"], project["version"]
        meta = find_metadata(name, version)
        if meta is None:
            problems.append(f"metadata_missing:{name}:{version}")
            project["candidate_wheel"] = None
            continue
        rows = []
        for item in meta.get("urls") or []:
            if item.get("packagetype") != "bdist_wheel" or item.get("yanked"):
                continue
            tags = parse_tags(item["filename"])
            if not tags or not compatible(tags):
                continue
            rows.append({"filename": item["filename"], "sha256": (item.get("digests") or {}).get("sha256"),
                         "bytes": item.get("size"), "url": item.get("url"), **tags})
        if not rows:
            problems.append(f"candidate_wheel_missing:{name}:{version}")
            project["candidate_wheel"] = None
            continue
        rows.sort(key=lambda row: row["filename"])
        project["candidate_wheel"] = rows[0]
    document["candidate_attach_problems"] = problems
    OUT.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(json.dumps({"out": str(OUT), "sha256": __import__("hashlib").sha256(
        OUT.read_bytes()).hexdigest(), "problems": problems,
        "attached": [p["project"] for p in document["projects"]
                     if p.get("candidate_wheel")]}, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
