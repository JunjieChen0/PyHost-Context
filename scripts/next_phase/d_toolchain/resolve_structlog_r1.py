"""ClawsGO Science Agent：structlog 的一次 R1 配置兼容解析。

保留原 R0 锁；只新增一条来自冻结配置/插件 API 的选择约束
`pytest-asyncio>=0.24`，不回写项目原始声明。最多一个 R1 阶段。
"""
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
STATE = NEXT / "state" / "d_toolchain"
ROOTS = HERE / "D_ACQUISITION_ROOTS.json"
OUT = STATE / "per_project_pip" / "structlog_R1.json"
R0 = STATE / "per_project_pip" / "structlog.json"
DERIVED = "pytest-asyncio>=0.24"
sys.path.insert(0, str(HERE))

import resolve_with_pip as resolver  # noqa: E402


def main():
    roots_doc = json.loads(ROOTS.read_text(encoding="utf-8"))
    project = next(item for item in roots_doc["projects"]
                   if item["project"] == "structlog")
    roots = []
    for item in project["requirements"]:
        requirement = __import__("packaging.requirements", fromlist=["Requirement"]) \
            .Requirement(item["raw"])
        if requirement.url:
            raise SystemExit(f"url_root_not_authorized:{item['raw']}")
        roots.append(item["raw"])
    if DERIVED not in roots:
        roots.append(DERIVED)
    outcome = resolver.run_pip("structlog", project["version"], roots, "R1")
    if not outcome["ok"]:
        raise SystemExit(json.dumps({"status": "resolution_failed",
                                     "outcome": outcome}, ensure_ascii=False))
    install = outcome["report"].get("install") or []
    local = resolver.load_local()
    rows, candidate, selected = [], None, {}
    for item in install:
        meta = item.get("metadata") or {}
        name = resolver.canonicalize_name(meta.get("name") or "")
        version = str(meta.get("version"))
        download = item.get("download_info") or {}
        archive = download.get("archive_info") or {}
        digest = (archive.get("hashes") or {}).get("sha256")
        url = download.get("url")
        row = {"name": name, "version": version,
               "filename": url.rsplit("/", 1)[-1] if url else None,
               "sha256": digest, "url": url,
               "requires_python": meta.get("requires_python"),
               "dependencies": meta.get("requires_dist") or [],
               "requested_extras": sorted(item.get("requested_extras") or []),
               "source": "pypi"}
        local_rows = local.get((name, version)) or []
        if local_rows:
            chosen = sorted(local_rows, key=lambda row: row["filename"])[0]
            if resolver.wheel_compatible(chosen["filename"]):
                row.update({"source": "local", "local_path": chosen["path"],
                            "filename": chosen["filename"], "sha256": chosen["sha256"],
                            "bytes": chosen["bytes"]})
        selected[name] = version
        if name == resolver.canonicalize_name("structlog"):
            candidate = row
        else:
            rows.append(row)
    roots_ok, root_rows = resolver.evaluate(roots, selected)
    edges = []
    for row in rows + ([candidate] if candidate else []):
        for raw in row["dependencies"]:
            dep = resolver.Requirement(raw)
            dep_name = resolver.canonicalize_name(dep.name)
            dep_version = selected.get(dep_name)
            applies = True
            if dep.marker:
                try:
                    applies = dep.marker.evaluate({**roots_doc["target"], "extra": ""})
                except Exception:  # noqa: BLE001
                    applies = True
            if applies:
                edges.append({"from": row["name"], "requirement": raw,
                              "to": dep_name, "selected": dep_version,
                              "satisfied": bool(dep_version
                                                and dep.specifier.contains(
                                                    resolver.Version(dep_version)))})
    edges_ok = all(edge["satisfied"] for edge in edges)
    pytest_row = next((row for row in rows if row["name"] == "pytest"), None)
    entry = {
        "project": "structlog", "version": project["version"],
        "project_source_sha256": project["source_sha256"],
        "status": "locked" if roots_ok and edges_ok else "lock_invalid",
        "stages": ["R1"], "r0_excluded": False,
        "pytest_version": pytest_row["version"] if pytest_row else None,
        "lock": {"stage": "R1", "rows": rows, "candidate": candidate,
                 "roots_satisfied": roots_ok, "root_checks": root_rows,
                 "dependency_edges_satisfied": edges_ok,
                 "dependency_edges": edges},
        "rejected_roots": [], "root_count": len(roots),
        "roots_sha256": hashlib.sha256(ROOTS.read_bytes()).hexdigest(),
        "derived_constraints": [{
            "requirement": DERIVED,
            "kind": "selection constraint from frozen project config and plugin API",
            "does_not_rewrite_project_declaration": True,
            "witness": "asyncio_default_fixture_loop_scope requires pytest-asyncio>=0.24",
        }],
        "preserves": {"r0_lock": str(R0.relative_to(NEXT)),
                      "r0_lock_sha256": hashlib.sha256(R0.read_bytes()).hexdigest()
                      if R0.is_file() else None},
    }
    OUT.write_text(json.dumps(entry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(json.dumps({"out": str(OUT),
                      "sha256": hashlib.sha256(OUT.read_bytes()).hexdigest(),
                      "status": entry["status"], "pytest": entry["pytest_version"],
                      "pytest_asyncio": selected.get("pytest-asyncio"),
                      "rows": len(rows)}, ensure_ascii=False, indent=2))
    return 0 if entry["status"] == "locked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
