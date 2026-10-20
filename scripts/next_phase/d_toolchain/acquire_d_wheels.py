"""ClawsGO Science Agent：按 D 档精确锁获取并核验 wheel。

只下载锁中登记的官方 PyPI 发行地址；逐件核对 SHA-256、字节数与 METADATA。
本地来源的 wheel 逐件复核既有字节。不安装、不导入候选、不执行候选测试。

用法：python3 acquire_d_wheels.py
"""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import time
import urllib.request
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
STATE = NEXT / "state" / "d_toolchain"
LOCKS = STATE / "D_RESOLUTION_LOCKS.json"
STORE = STATE / "wheels"
OUT = STATE / "D_WHEEL_MANIFEST.json"
LOG = STATE / "D_ACQUISITION_LOG.jsonl"
UA = "pyhost-context-dprep/1.0 (research; contact: clawsgo)"
PIP = str(NEXT.parents[2] / "pycompat-env-select" / "runtime" / "interpreters"
          / "cpython-3.11.9-linux-x86_64-gnu" / "bin" / "python3.11")


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def log(event):
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at": now(), **event}, ensure_ascii=False) + "\n")


def download(url, expected_sha, expected_bytes, retries=3):
    last = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(request, timeout=180) as response:
                raw = response.read()
                status = response.status
            if sha_bytes(raw) != expected_sha:
                raise ValueError(f"sha256_mismatch:{sha_bytes(raw)}")
            if expected_bytes is not None and len(raw) != expected_bytes:
                raise ValueError(f"size_mismatch:{len(raw)}")
            return raw, status
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"download_failed:{url}:{last}")


def pip_download(url, expected_sha, expected_bytes):
    """经 pip 的 HTTP 缓存取同一 URL 的字节；仍逐件核对哈希与大小。"""
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        argv = [PIP, "-m", "pip", "--isolated", "download", "--no-deps",
                "--disable-pip-version-check", "-d", directory, url]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(f"pip_download_failed:{proc.stderr[-300:]}")
        files = [pathlib.Path(directory) / name for name in os.listdir(directory)]
        if len(files) != 1:
            raise RuntimeError(f"pip_download_ambiguous:{len(files)}")
        raw = files[0].read_bytes()
    if sha_bytes(raw) != expected_sha:
        raise ValueError(f"sha256_mismatch:{sha_bytes(raw)}")
    if expected_bytes is not None and len(raw) != expected_bytes:
        raise ValueError(f"size_mismatch:{len(raw)}")
    return raw, "pip_cache"


def wheel_metadata(path):
    with zipfile.ZipFile(path) as archive:
        candidates = [n for n in archive.namelist()
                      if n.endswith(".dist-info/METADATA")]
        stem = pathlib.Path(path).name[:-4].split("-")[0].replace("_", "-").lower()

        def own(member):
            if member.count("/") != 1:
                return False
            directory = member.split("/")[0]
            if not directory.endswith(".dist-info"):
                return False
            prefix = directory[:-len(".dist-info")].rsplit("-", 1)[0]
            return prefix.replace("_", "-").lower() == stem

        member = next((n for n in candidates if own(n)),
                      candidates[0] if candidates else None)
        if member is None:
            raise ValueError(f"metadata_missing:{path}")
        raw = archive.read(member)
        text = raw.decode("utf-8", "replace")
        header = text.split("\n\n", 1)[0]
        info = {"metadata_sha256": sha_bytes(raw),
                "requires_dist": [l.split(":", 1)[1].strip() for l in header.splitlines()
                                  if l.startswith("Requires-Dist:")]}
        for line in header.splitlines():
            if line.startswith(("Name:", "Version:", "Requires-Python:")):
                key, _, value = line.partition(":")
                info[key.strip().lower()] = value.strip()
        return info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--locks", type=pathlib.Path, default=LOCKS)
    parser.add_argument("--store", type=pathlib.Path, default=STORE)
    parser.add_argument("--out", type=pathlib.Path, default=OUT)
    args = parser.parse_args()
    document = json.loads(args.locks.read_text(encoding="utf-8"))
    args.store.mkdir(parents=True, exist_ok=True)
    LOG.touch()
    rows = []
    problems = []
    seen = {}
    seen_meta = {}
    for project in document["projects"]:
        if project["status"] != "locked":
            continue
        project_rows = list(project["lock"]["rows"])
        candidate = project.get("candidate_wheel") or project["lock"].get("candidate")
        if candidate:
            candidate = dict(candidate)
            candidate.setdefault("name", project["project"])
            candidate.setdefault("version", project["version"])
            candidate["candidate"] = True
            project_rows.append(candidate)
        for row in project_rows:
            key = (row["filename"], row.get("sha256"))
            if key in seen:
                row["stored_path"] = seen[key]
                rows.append({**row, "project": project["project"],
                             "reused": True, "metadata": seen_meta[key],
                             "metadata_check": "reused_verified"})
                continue
            if row["source"] == "local":
                local_source = row.get("local_path")
                if not local_source:
                    stored = args.store / row["sha256"] / row["filename"]
                    if stored.is_file() and sha_bytes(stored.read_bytes()) == row["sha256"]:
                        local_source = str(stored)
                if not local_source:
                    problems.append(f"local_missing:{project['project']}:{row['filename']}")
                    continue
                path = pathlib.Path(local_source)
                if not path.is_file():
                    problems.append(f"local_missing:{project['project']}:{row['filename']}")
                    continue
                raw = path.read_bytes()
                if sha_bytes(raw) != row["sha256"]:
                    problems.append(f"local_sha:{project['project']}:{row['filename']}")
                    continue
                target = args.store / row["sha256"] / row["filename"]
                if not (target.is_file() and sha_bytes(target.read_bytes()) == row["sha256"]):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(raw)
                meta = wheel_metadata(path)
                stored = str(target)
                log({"event": "wheel_copied_local", "project": project["project"],
                     "filename": row["filename"], "sha256": row["sha256"],
                     "bytes": len(raw), "source_path": str(path)})
            else:
                if not row.get("url"):
                    problems.append(f"url_missing:{project['project']}:{row['filename']}")
                    continue
                target = args.store / row["sha256"] / row["filename"]
                if target.is_file() and sha_bytes(target.read_bytes()) == row["sha256"]:
                    raw = target.read_bytes()
                    status = "cached"
                else:
                    try:
                        raw, status = pip_download(row["url"], row["sha256"],
                                                   row.get("bytes"))
                    except Exception as error:  # noqa: BLE001
                        log({"event": "pip_download_fallback",
                             "project": project["project"],
                             "filename": row["filename"], "error": str(error)[-300:]})
                        raw, status = download(row["url"], row["sha256"], row.get("bytes"))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(raw)
                stored = str(target)
                meta = wheel_metadata(target)
                log({"event": "wheel_acquired", "project": project["project"],
                     "filename": row["filename"], "sha256": row["sha256"],
                     "bytes": len(raw), "url": row["url"], "status": status,
                     "metadata_sha256": meta["metadata_sha256"]})
            from packaging.utils import canonicalize_name
            name_ok = (canonicalize_name(meta.get("name") or "")
                       == canonicalize_name(row["name"]))
            version_ok = str(meta.get("version")) == str(row["version"])
            if not (name_ok and version_ok):
                problems.append(f"metadata_identity:{project['project']}:{row['filename']}")
            snapshot = None
            if row.get("official"):
                snapshot = {"requires_dist": row["official"].get("requires_dist") or []}
            meta_path = STATE / "meta" / row["name"] / f"{row['version']}.json"
            if snapshot is None and meta_path.is_file():
                snapshot = json.loads(meta_path.read_text(encoding="utf-8")).get("info", {})
            expected = [r for r in (snapshot or {}).get("requires_dist") or []]
            discrepancy = None
            official_absent = False
            metadata_check = "match"
            if snapshot is not None:
                def normalize(items):
                    from packaging.requirements import Requirement
                    from packaging.utils import canonicalize_name as canon
                    out = []
                    for raw in items:
                        try:
                            req = Requirement(raw)
                            out.append((canon(req.name), str(req.specifier),
                                        tuple(sorted(req.extras)),
                                        str(req.marker) if req.marker else ""))
                        except Exception:  # noqa: BLE001
                            out.append(("raw", raw))
                    return sorted(out)
                if normalize(expected) != normalize(meta["requires_dist"]):
                    if not expected and meta["requires_dist"]:
                        official_absent = True
                        metadata_check = "official_requires_dist_absent"
                    else:
                        discrepancy = {"snapshot": expected, "wheel": meta["requires_dist"]}
                        metadata_check = "discrepancy"
                        log({"event": "metadata_discrepancy", "project": project["project"],
                             "filename": row["filename"], "detail": discrepancy})
            else:
                metadata_check = "snapshot_absent"
            seen[key] = stored
            seen_meta[key] = meta
            rows.append({
                **row, "project": project["project"], "reused": False,
                "stored_path": stored, "stored_bytes": len(raw),
                "stored_sha256": sha_bytes(raw),
                "metadata": meta,
                "metadata_discrepancy": discrepancy,
                "official_requires_dist_absent": official_absent,
                "metadata_check": metadata_check,
            })
    # 同一项目内重复 name 且版本不同是错误；同 name/version 不同字节也是错误。
    by_project = {}
    for row in rows:
        by_project.setdefault(row["project"], {}).setdefault(row["name"], set()).add(
            (row["version"], row.get("stored_sha256")))
    for project, names in by_project.items():
        for name, values in names.items():
            if len(values) > 1:
                problems.append(f"duplicate_package:{project}:{name}:{sorted(values)}")
    out = {
        "schema_version": "pyhost-context/d-wheel-manifest/1.0",
        "generated_at": now(),
        "locks_sha256": sha_bytes(args.locks.read_bytes()),
        "wheel_count": len({r["sha256"] for r in rows}),
        "row_count": len(rows),
        "projects": sorted({r["project"] for r in rows}),
        "problems": problems,
        "rows": rows,
    }
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(json.dumps({"out": str(args.out), "sha256": sha_bytes(args.out.read_bytes()),
                      "wheels": out["wheel_count"], "rows": out["row_count"],
                      "problems": problems}, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
