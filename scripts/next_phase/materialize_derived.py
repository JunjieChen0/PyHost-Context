"""ClawsGO Science Agent：按案例表把补丁应用到冻结源码，生成**派生工件**（不执行）。

只在 S3 修复阶段前调用；本文件本身不导入候选代码。产物：

    state/derived/<case>/<mutation>/<source_dir>/...

每个派生树附 `DERIVED_MANIFEST.json`：源 sdist 哈希、逐文件前像/后像哈希、补丁行号。
"""
import hashlib
import json
import pathlib
import shutil
import sys
import tarfile

NEXT = pathlib.Path(__file__).resolve().parent
EXT = NEXT.parent
ROOT = EXT.parent
TABLE = NEXT / "CASE_TABLES_20261018_REV1.json"
OUT = NEXT / "state" / "derived"
INDEX = NEXT / "DERIVED_INDEX_20261018.json"
SDISTS = {
    "rich-argparse": ROOT / "admission_artifacts" / "rich-argparse" / "rich_argparse-1.8.0.tar.gz",
    "rich-rst": ROOT / "admission_artifacts" / "rich-rst" / "rich_rst-2.2.0.tar.gz",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def extract(project, target):
    sdist = SDISTS[project]
    expected = sha(sdist.read_bytes())
    with tarfile.open(sdist) as tar:
        members = [m for m in tar.getmembers() if m.isfile()
                   and ".." not in pathlib.PurePosixPath(m.name).parts]
        top = members[0].name.split("/")[0]
        for member in members:
            rel = pathlib.PurePosixPath(member.name).parts[1:]
            path = target.joinpath(*rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(tar.extractfile(member).read())
    return {"sdist": str(sdist.relative_to(ROOT)), "sdist_sha256": expected,
            "source_dir": top}


def apply_patch(root, hunks):
    records = []
    for hunk in sorted(hunks, key=lambda h: h["line"], reverse=True):
        path = root / hunk["file"]
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        index = hunk["line"] - 1
        before = lines[index].rstrip("\n")
        if before != hunk["before"]:
            raise AssertionError(f"anchor_mismatch:{path}:{hunk['line']}")
        indent = before[:len(before) - len(before.lstrip())]
        lines[index] = indent + hunk["after"] + "\n"
        path.write_text("".join(lines), encoding="utf-8")
        records.append({"file": hunk["file"], "line": hunk["line"],
                        "before_sha256": sha(hunk["before"].encode()),
                        "after_sha256": sha(hunk["after"].encode())})
    return records


def main():
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    written = []
    for case in table["cases"]:
        base = OUT / case["case_id"] / "original"
        if base.exists():
            shutil.rmtree(base)
        meta = extract(case["project"], base)
        for mutation in case["mutations"]:
            target = OUT / case["case_id"] / mutation["id"]
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(base, target)
            patches = apply_patch(target, mutation["patch"]["hunks"])
            manifest = {
                "schema_version": "pyhost-context/derived-product/1.0",
                "case": case["case_id"], "mutation": mutation["id"],
                "project": case["project"], "version": case["version"],
                **meta, "patches": patches,
                "tree_sha256": tree_hash(target),
                "candidate_executed": False,
            }
            (target.parent / f"{mutation['id']}.manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            written.append({"mutation": mutation["id"], "tree": str(target.relative_to(ROOT)),
                            "tree_sha256": manifest["tree_sha256"]})
    document = {"schema_version": "pyhost-context/derived-products-index/1.0",
                "written_at_utc": "2026-10-18T00:00:00Z",
                "source_table": str(TABLE.relative_to(ROOT)),
                "source_table_sha256": sha(TABLE.read_bytes()),
                "derived": written, "count": len(written),
                "candidate_executed": False}
    INDEX.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"derived": written, "count": len(written)},
                     ensure_ascii=False, indent=2))
    return 0


def tree_hash(root):
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rows.append(f"{path.relative_to(root).as_posix()}:{sha(path.read_bytes())}")
    return sha("\n".join(rows).encode())


if __name__ == "__main__":
    raise SystemExit(main())
