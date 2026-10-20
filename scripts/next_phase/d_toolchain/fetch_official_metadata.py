"""ClawsGO Science Agent：从官方 PyPI 逐版本元数据核验 D 锁内每一件工件。

只访问 `https://pypi.org/pypi/<name>/<version>/json`；原始 JSON 字节按项目/版本
落盘并记哈希。对锁内每一行核对文件名、sha256 与大小，缺失或不等即具名缺口。
不安装、不执行候选。
"""
import argparse
import concurrent.futures
import datetime
import hashlib
import json
import pathlib
import urllib.request

from packaging.utils import canonicalize_name

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
STATE = NEXT / "state" / "d_toolchain"
LOCKS = STATE / "D_RESOLUTION_LOCKS.json"
CACHE = STATE / "official"
INDEX = STATE / "D_OFFICIAL_METADATA_INDEX.json"
BASE = "https://pypi.org/pypi/{name}/{version}/json"
HEADERS = {"User-Agent": "ClawsGO-Science-Agent/1.0 (pyhost-context D preparation)"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def wheel_compatible(filename):
    """文件名标签是否匹配 CPython 3.11 / manylinux x86_64。"""
    if not filename.endswith(".whl"):
        return False
    parts = filename[:-4].split("-")
    if len(parts) < 5:
        return False
    python_tag, abi_tag, platform_tag = parts[-3].lower(), parts[-2].lower(), parts[-1].lower()
    platform_ok = platform_tag == "any" or platform_tag == "linux_x86_64" or (
        platform_tag.startswith("manylinux") and platform_tag.endswith("_x86_64"))
    if not platform_ok:
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


def fetch_one(name, version):
    cache = CACHE / canonicalize_name(name) / f"{version}.json"
    if cache.is_file():
        raw = cache.read_bytes()
        try:
            return name, version, raw, "cache"
        except Exception:  # noqa: BLE001
            pass
    last = None
    for attempt in range(4):
        try:
            request = urllib.request.Request(BASE.format(name=name, version=version),
                                             headers=HEADERS)
            with urllib.request.urlopen(request, timeout=90) as response:
                raw = response.read()
            document = json.loads(raw)
            if canonicalize_name(document["info"]["name"]) != canonicalize_name(name) \
                    or str(document["info"]["version"]) != str(version):
                raise ValueError("identity_mismatch")
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(raw)
            return name, version, raw, "network"
        except Exception as error:  # noqa: BLE001
            last = f"{type(error).__name__}:{error}"
            import time
            time.sleep(2 * (attempt + 1))
    return name, version, None, last


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--locks", type=pathlib.Path, default=LOCKS)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--out", type=pathlib.Path, default=INDEX)
    args = parser.parse_args()
    document = json.loads(args.locks.read_text(encoding="utf-8"))
    wanted = []
    for project in document["projects"]:
        if project.get("status") != "locked":
            continue
        for row in project["lock"]["rows"]:
            wanted.append((row["name"], row["version"]))
        if project.get("candidate_wheel") or project["lock"].get("candidate"):
            wanted.append((project["project"], project["version"]))
    wanted = sorted(set(wanted))
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for name, version, raw, origin in pool.map(lambda pair: fetch_one(*pair), wanted):
            results[(canonicalize_name(name), version)] = {
                "raw": raw, "origin": origin}
    index = {"schema_version": "pyhost-context/d-official-metadata/1.0",
             "generated_at": now(), "entries": []}
    problems = []
    for project in document["projects"]:
        if project.get("status") != "locked":
            continue
        candidate = project.get("candidate_wheel") or project["lock"].get("candidate")
        target_rows = list(project["lock"]["rows"]) + ([candidate] if candidate else [])
        for row in target_rows:
            key = (canonicalize_name(row["name"]), row["version"])
            entry = results.get(key) or {}
            raw = entry.get("raw")
            if raw is None:
                problems.append(f"metadata_unavailable:{row['name']}:{row['version']}:{entry.get('origin')}")
                continue
            official = json.loads(raw)
            files = {item["filename"]: item for item in official.get("urls") or []}
            if row.get("source") == "pypi":
                compatible_files = [item for item in official.get("urls") or []
                                    if item.get("packagetype") == "bdist_wheel"
                                    and not item.get("yanked")
                                    and wheel_compatible(item["filename"])]
                if not compatible_files:
                    problems.append(f"no_compatible_wheel:{row['name']}:{row['version']}")
                    continue
                selected = min(compatible_files, key=lambda item: item["filename"])
                if selected["filename"] != row["filename"]:
                    row["file_reselection"] = {
                        "rule": "compatible wheel filename lexicographic ascending",
                        "resolver_filename": row["filename"],
                        "selected_filename": selected["filename"]}
                    row["filename"] = selected["filename"]
                    row["sha256"] = (selected.get("digests") or {}).get("sha256")
                    row["url"] = selected.get("url")
            selected = files.get(row["filename"])
            if selected is None:
                problems.append(f"filename_not_registered:{row['name']}:{row['filename']}")
                continue
            digest = (selected.get("digests") or {}).get("sha256")
            if digest != row["sha256"]:
                problems.append(f"sha256_differs:{row['filename']}:{digest}!={row['sha256']}")
            row["official"] = {
                "url": selected.get("url"), "sha256": digest,
                "bytes": selected.get("size"),
                "upload_time_iso_8601": selected.get("upload_time_iso_8601"),
                "packagetype": selected.get("packagetype"),
                "yanked": bool(selected.get("yanked")),
                "requires_python": official["info"].get("requires_python"),
                "requires_dist": official["info"].get("requires_dist") or [],
            }
            if row.get("source") == "pypi":
                row["url"] = selected.get("url")
                row["sha256"] = digest
                row["bytes"] = selected.get("size")
            index["entries"].append({
                "name": row["name"], "version": row["version"],
                "filename": row["filename"],
                "metadata_sha256": hashlib.sha256(raw).hexdigest(),
                "metadata_origin": entry["origin"],
                "metadata_path": str(CACHE / canonicalize_name(row["name"]) / f"{row['version']}.json"),
                "file_sha256": digest, "file_bytes": selected.get("size"),
            })
    index["problems"] = problems
    args.out.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    args.locks.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
    print(json.dumps({"entries": len(index["entries"]), "problems": problems,
                      "index_sha256": hashlib.sha256(args.out.read_bytes()).hexdigest()},
                     ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
