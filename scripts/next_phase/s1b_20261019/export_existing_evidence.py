"""ClawsGO Science Agent：按 Reviewer 001 的既有字节请求只读导出（不重跑、不重建）。

- 只复制请求清单中**已存在**的文件；缺件即记录，不重建、不补跑；
- 必选件逐件核对请求中的 SHA-256 与字节数；可选辅助件记录首次导出哈希；
- 不联网、不安装、不导入、不执行任何候选。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil

HERE = pathlib.Path(__file__).resolve().parent
WS = HERE.parents[3]            # project-20261013-1
REQUEST = pathlib.Path("/tmp/s1brev/EXISTING_BYTES_REQUEST.json")
OUT = HERE / "export" / "s1b-existing-evidence-20261019"


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def main():
    request = json.loads(REQUEST.read_text(encoding="utf-8"))
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "raw").mkdir(parents=True)
    rows, optional_rows, missing = [], [], []
    for item in request["required_prebound_files"]:
        source = WS / item["source_relative_to_workspace"]
        target = OUT / item["export_path"]
        if not source.is_file():
            missing.append({"export_path": item["export_path"], "reason": "source_missing"})
            continue
        digest = sha(source)
        if digest != item["sha256"] or source.stat().st_size != item["bytes"]:
            missing.append({"export_path": item["export_path"], "reason": "hash_or_size_mismatch"})
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        rows.append({"export_path": item["export_path"], "kind": item["kind"],
                     "run_id": item.get("run_id"), "bytes": target.stat().st_size,
                     "sha256": digest,
                     "source_relative_to_workspace": item["source_relative_to_workspace"],
                     "provenance": item.get("provenance")})
    for item in request["optional_existing_auxiliary_files"]:
        source = WS / item["source_relative_to_workspace"]
        if not source.is_file():
            missing.append({"export_path": item["export_path"], "reason": "optional_source_missing"})
            continue
        target = OUT / item["export_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        optional_rows.append({"export_path": item["export_path"],
                              "run_id": item.get("run_id"),
                              "bytes": target.stat().st_size,
                              "sha256": sha(target),
                              "hash_provenance": item.get("hash_provenance"),
                              "source_relative_to_workspace":
                                  item["source_relative_to_workspace"]})
    manifest = {
        "schema_version": "pyhost-context/S1b-existing-export/1.0",
        "reviewer_request_sha256": sha(REQUEST),
        "input_results_zip_sha256": request["input_results_zip_sha256"],
        "scope": "read_only_export_of_existing_archives",
        "candidate_execution": 0,
        "reruns": 0,
        "reconstructed_files": 0,
        "required": {"requested": len(request["required_prebound_files"]),
                     "exported": len(rows),
                     "bytes": sum(row["bytes"] for row in rows)},
        "optional": {"requested": len(request["optional_existing_auxiliary_files"]),
                     "exported": len(optional_rows),
                     "bytes": sum(row["bytes"] for row in optional_rows)},
        "missing": missing,
        "rounds_by_project": _counts(rows),
        "required_files": rows,
        "optional_files": optional_rows,
    }
    (OUT / "EXPORT_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(json.dumps({"out": str(OUT), "required": manifest["required"],
                      "optional": manifest["optional"], "missing": len(missing),
                      "sha256": sha(OUT / "EXPORT_MANIFEST.json")},
                     ensure_ascii=False, indent=2))
    return 0


def _counts(rows):
    counts = {}
    for row in rows:
        run = row.get("run_id")
        if run:
            counts[run.split("-")[2]] = counts.get(run.split("-")[2], 0) + 1
    return counts


if __name__ == "__main__":
    raise SystemExit(main())
