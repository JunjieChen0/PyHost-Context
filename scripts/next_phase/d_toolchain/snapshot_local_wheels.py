"""ClawsGO Science Agent：解析前封存本地 wheel 清单。

只读扫描指定的 wheelhouse 根目录，记录文件名、名称、版本、标签、SHA-256、
METADATA 摘要与来源。不安装、不联网、不导入候选代码。
"""
import hashlib
import json
import os
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[3]  # pyhost-context
OUT = ROOT / "extension" / "next_phase" / "state" / "d_toolchain" / "D_LOCAL_WHEEL_INVENTORY.json"

ROOTS = [
    ROOT / "extension" / "wheelhouse",
    ROOT / "extension" / "next_phase" / "state" / "wheelhouse",
    ROOT / "extension" / "artifact_identity_repair",
    ROOT / "admission_artifacts",
    ROOT.parent / "pycompat-env-select" / "stage2",
]


def sha_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def wheel_metadata(path):
    with zipfile.ZipFile(path) as archive:
        metadata = next((n for n in archive.namelist() if n.endswith(".dist-info/METADATA")), None)
        if not metadata:
            return None
        raw = archive.read(metadata)
        text = raw.decode("utf-8", "replace")
        info = {}
        for line in text.splitlines():
            if line.startswith(("Name:", "Version:", "Requires-Python:")):
                key, _, value = line.partition(":")
                info[key.strip()] = value.strip()
            elif line.startswith("Requires-Dist:"):
                info.setdefault("Requires-Dist", []).append(
                    line.split(":", 1)[1].strip())
        entry_points = next((n for n in archive.namelist()
                             if n.endswith(".dist-info/entry_points.txt")), None)
        pytest11 = {}
        if entry_points:
            import configparser
            parser = configparser.ConfigParser()
            parser.read_string(archive.read(entry_points).decode("utf-8", "replace"))
            if parser.has_section("pytest11"):
                pytest11 = dict(parser.items("pytest11"))
        info["pytest11"] = pytest11
        info["metadata_member"] = metadata
        info["metadata_sha256"] = sha_bytes(raw)
        return info


def parse_filename(name):
    stem = name[:-4]
    parts = stem.split("-")
    if len(parts) < 5:
        return None
    return {"name": parts[0], "version": parts[1],
            "python_tag": parts[-3], "abi_tag": parts[-2], "platform_tag": parts[-1]}


def main():
    entries = []
    seen = {}
    duplicates = []
    for root in ROOTS:
        if not root.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            if "venv" in pathlib.Path(dirpath).parts:
                continue
            for filename in sorted(filenames):
                if not filename.endswith(".whl"):
                    continue
                path = pathlib.Path(dirpath) / filename
                parsed = parse_filename(filename)
                if not parsed:
                    continue
                raw = path.read_bytes()
                digest = sha_bytes(raw)
                try:
                    meta = wheel_metadata(path)
                except Exception as exc:  # noqa: BLE001
                    meta = {"error": f"{type(exc).__name__}: {exc}"}
                key = (filename, digest)
                if key in seen:
                    duplicates.append({"filename": filename, "sha256": digest,
                                       "first": seen[key], "duplicate": str(path)})
                    continue
                seen[key] = str(path)
                entries.append({
                    "filename": filename,
                    "wheel_name": parsed["name"],
                    "version": parsed["version"],
                    "python_tag": parsed["python_tag"],
                    "abi_tag": parsed["abi_tag"],
                    "platform_tag": parsed["platform_tag"],
                    "sha256": digest,
                    "bytes": len(raw),
                    "path": str(path),
                    "source_root": str(root),
                    "metadata": meta,
                })
    entries.sort(key=lambda item: (item["wheel_name"].lower(), item["version"], item["filename"]))
    document = {
        "schema_version": "pyhost-context/d-local-wheel-inventory/1.0",
        "sealed_before_resolution": True,
        "roots": [str(root) for root in ROOTS],
        "count": len(entries),
        "unique_name_versions": len({(e["wheel_name"].lower(), e["version"]) for e in entries}),
        "duplicate_payloads": duplicates,
        "entries": entries,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT), "bytes": OUT.stat().st_size,
                      "count": len(entries), "duplicates": len(duplicates),
                      "sha256": hashlib.sha256(OUT.read_bytes()).hexdigest()},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
