"""ClawsGO Science Agent：PyHost-Context 准入工件获取与 SHA-256 校验。

只做工件获取与校验：下载固定版本的 wheel/sdist，重算 SHA-256 与元数据记录比对，
写 receipt。不安装、不运行任何候选项目。
"""
import datetime
import hashlib
import json
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PLAN = json.loads((ROOT / "admission_freeze/artifact_acquisition_plan.json").read_text())
META = json.loads((ROOT / "admission_candidate_metadata_probe_v0.2.json").read_text())
ART = ROOT / "admission_artifacts"
USER_AGENT = "PyHost-Context-admission-acquisition/1.0"


def sha256_of(path):
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_url(meta_candidate, filename):
    for entry in meta_candidate.get("pypi_files", []):
        if entry["filename"] == filename:
            return entry.get("url")
    return None


def fetch_pypi_urls(pypi_project, version):
    """从 PyPI JSON 按精确版本查询发行文件 URL；元数据文件只保存文件名与哈希。"""
    url = f"https://pypi.org/pypi/{pypi_project}/{version}/json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.load(resp)
    return {f["filename"]: f.get("url") for f in payload.get("urls", [])}


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        status = resp.status
        data = resp.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return status, len(data)


def main():
    meta_by_name = {c["name"]: c for c in META["candidates"]}
    receipts = []
    for cand in PLAN["candidates"]:
        name = cand["name"]
        meta = meta_by_name.get(name, {})
        pypi_project = meta.get("pypi_project") or name
        entry = {
            "name": name,
            "package_version": cand["package_version"],
            "git_tag": cand["git_tag"],
            "git_commit": cand["git_commit"],
            "artifacts": [],
            "status": "pending",
        }
        try:
            url_map = fetch_pypi_urls(pypi_project, cand["package_version"])
            entry["pypi_json_url"] = f"https://pypi.org/pypi/{pypi_project}/{cand['package_version']}/json"
        except Exception as exc:
            url_map = {}
            entry["pypi_url_lookup_failure"] = f"{type(exc).__name__}: {exc}"
        for kind in ("wheel", "sdist"):
            filename = cand[kind]
            expected = cand[f"{kind}_sha256"]
            url = url_map.get(filename)
            target = ART / name / filename
            record = {"kind": kind, "filename": filename, "url": url,
                      "expected_sha256": expected, "path": str(target.relative_to(ROOT))}
            if url is None:
                record.update(observed_sha256=None, matches_expected=False,
                              failure=entry.get("pypi_url_lookup_failure", "url_not_found_in_metadata"))
                entry["artifacts"].append(record)
                continue
            # 可续跑：已存在且哈希匹配则跳过下载，不重复请求。
            if target.exists():
                observed = sha256_of(target)
                if observed == expected:
                    record.update(observed_sha256=observed, matches_expected=True,
                                  resumed_from_disk=True, bytes=target.stat().st_size)
                    entry["artifacts"].append(record)
                    continue
            try:
                status, size = download(url, target)
                observed = sha256_of(target)
                record.update(http_status=status, bytes=size, observed_sha256=observed,
                              matches_expected=observed == expected)
            except Exception as exc:  # 网络或写入失败都保留
                record.update(observed_sha256=None, matches_expected=False,
                              failure=f"{type(exc).__name__}: {exc}")
            entry["artifacts"].append(record)
        all_ok = all(a.get("matches_expected") for a in entry["artifacts"]) and entry["artifacts"]
        entry["status"] = "artifact_verified" if all_ok else "artifact_admission_failure"
        receipts.append(entry)
        print(f"{name}: {entry['status']} "
              + " ".join(f"{a['kind']}={'ok' if a.get('matches_expected') else 'FAIL'}" for a in entry["artifacts"]),
              flush=True)

    summary = {
        "schema_version": "pyhost-context/artifact-acquisition-receipt/1.0",
        "acquired_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "phase": "admission",
        "plan": "admission_freeze/artifact_acquisition_plan.json",
        "metadata": "admission_candidate_metadata_probe_v0.2.json",
        "installed": False,
        "candidate_projects_executed": False,
        "verified": sum(1 for r in receipts if r["status"] == "artifact_verified"),
        "failed": sum(1 for r in receipts if r["status"] != "artifact_verified"),
        "candidates": receipts,
    }
    out = ROOT / "admission_freeze/artifact_acquisition_receipt.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(f"verified={summary['verified']} failed={summary['failed']} -> {out.name}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
