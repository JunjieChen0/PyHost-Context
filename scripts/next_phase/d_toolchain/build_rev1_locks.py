"""ClawsGO Science Agent：生成 D 锁派生修订版（REV1）。

- structlog 换用一次性 R1 配置兼容锁，原 R0 锁原样保留；
- twine/hatch 的 cryptography 同版本文件改为 manylinux2014 目标文件；
- 其余五项锁逐字节保持不变；原件不覆盖。
"""
import hashlib
import json
import pathlib
import sys

from packaging.utils import canonicalize_name

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
STATE = NEXT / "state" / "d_toolchain"
ORIGINAL = STATE / "D_RESOLUTION_LOCKS_WITH_CANDIDATE.json"
STRUCTLOG_R1 = STATE / "per_project_pip" / "structlog_R1.json"
OUT = STATE / "D_RESOLUTION_LOCKS_REV1.json"
LOCKS_DIR = NEXT / "locks_rev1"
sys.path.insert(0, str(HERE))

import fetch_official_metadata as fetch  # noqa: E402

CRYPTO_SHA = "630ebfea3bf689d075f82316324ff7433dc447fe6bc1bfc76524b74b4a9567d2"
CRYPTO_FILE = ("cryptography-50.0.2-cp311-abi3-manylinux2014_x86_64"
               ".manylinux_2_17_x86_64.whl")
CRYPTO_OLD = "cryptography-50.0.2-cp311-abi3-manylinux_2_34_x86_64.whl"
CRYPTO_METADATA_SHA = "047cfc71226409de4318b360aef15c63a2adf609731e3dcf7fe932f14ea042ee"


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def tags(filename):
    parts = filename[:-4].split("-")
    return {"python_tag": parts[-3], "abi_tag": parts[-2], "platform_tag": parts[-1]}


def official_for(name, version, filename, row):
    raw = None
    key = (canonicalize_name(name), version)
    _, _, raw, origin = fetch.fetch_one(name, version)
    if raw is None:
        raise SystemExit(f"metadata_unavailable:{name}:{version}:{origin}")
    document = json.loads(raw)
    selected = next(item for item in document.get("urls") or []
                    if item["filename"] == filename)
    digest = (selected.get("digests") or {}).get("sha256")
    if digest != row["sha256"]:
        raise SystemExit(f"sha_mismatch:{filename}")
    row["official"] = {
        "url": selected.get("url"), "sha256": digest, "bytes": selected.get("size"),
        "upload_time_iso_8601": selected.get("upload_time_iso_8601"),
        "packagetype": selected.get("packagetype"),
        "yanked": bool(selected.get("yanked")),
        "requires_python": document["info"].get("requires_python"),
        "requires_dist": document["info"].get("requires_dist") or [],
    }
    row["url"] = selected.get("url")
    row["bytes"] = selected.get("size")
    return row


def finalize_structlog():
    entry = json.loads(STRUCTLOG_R1.read_text(encoding="utf-8"))
    candidate = entry["lock"].pop("candidate")
    rows = []
    for row in entry["lock"]["rows"]:
        row = official_for(row["name"], row["version"], row["filename"], row)
        rows.append(row)
    entry["lock"]["rows"] = rows
    candidate = official_for("structlog", entry["version"],
                             candidate["filename"], candidate)
    entry["candidate_wheel"] = {
        "name": candidate["name"], "version": candidate["version"],
        "filename": candidate["filename"], "sha256": candidate["sha256"],
        "bytes": candidate["bytes"], "url": candidate["url"],
        "source": "pypi", **tags(candidate["filename"]),
        "official": candidate["official"],
    }
    return entry


def main():
    document = json.loads(ORIGINAL.read_text(encoding="utf-8"))
    original_structlog = next(row for row in document["projects"]
                              if row["project"] == "structlog")
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    (LOCKS_DIR / "structlog_R0.json").write_text(
        json.dumps(original_structlog, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    structlog_r1 = finalize_structlog()
    # 保留 R0 的完整引用，避免派生件覆盖原件。
    structlog_r1["r0_preserved"] = {
        "path": "locks/structlog_R0.json",
        "sha256": sha(LOCKS_DIR / "structlog_R0.json"),
        "pytest_version": original_structlog["pytest_version"],
        "lock_rows": len(original_structlog["lock"]["rows"]),
    }
    for index, project in enumerate(document["projects"]):
        if project["project"] == "structlog":
            document["projects"][index] = structlog_r1
        elif project["project"] in ("twine", "hatch"):
            for row in project["lock"]["rows"]:
                if canonicalize_name(row["name"]) != "cryptography":
                    continue
                if row["filename"] != CRYPTO_OLD:
                    raise SystemExit(f"unexpected_cryptography_file:{row['filename']}")
                old = {key: row.get(key) for key in
                       ("filename", "sha256", "bytes", "url", "source", "local_path")}
                replacement = {
                    "name": "cryptography", "version": row["version"],
                    "filename": CRYPTO_FILE, "sha256": CRYPTO_SHA,
                    "source": "pypi", "dependencies": row.get("dependencies") or [],
                    "requested_extras": row.get("requested_extras") or [],
                    "requires_python": row.get("requires_python"),
                }
                replacement = official_for("cryptography", row["version"],
                                           CRYPTO_FILE, replacement)
                replacement["file_correction"] = {
                    "reason": "manylinux_2_34 does not meet declared manylinux2014/glibc 2.17",
                    "replaced": old,
                    "replacement_sha256": CRYPTO_SHA,
                    "replacement_bytes": 4719841,
                    "metadata_sha256": CRYPTO_METADATA_SHA,
                    "requires_dist_identical_to_replaced_wheel": True,
                }
                row.clear()
                row.update(replacement)
    document["revision"] = "REV1"
    document["rev1_corrections"] = {
        "structlog": "R1 configuration-compatible lock; R0 preserved",
        "twine": "cryptography same-version target wheel corrected",
        "hatch": "cryptography same-version target wheel corrected",
        "other_projects": "unchanged from accepted original bytes",
    }
    document["original_locks_sha256"] = sha(ORIGINAL)
    OUT.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    for project in document["projects"]:
        if project.get("status") == "locked":
            (LOCKS_DIR / f"{project['project']}.json").write_text(
                json.dumps(project, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
    print(json.dumps({"out": str(OUT), "sha256": sha(OUT),
                      "structlog_pytest": structlog_r1["pytest_version"],
                      "projects": [p["project"] for p in document["projects"]
                                   if p.get("status") == "locked"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
