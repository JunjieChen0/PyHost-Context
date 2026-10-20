"""ClawsGO Science Agent：把候选源码树**完整恢复**为冻结 sdist 的文件集合与字节内容。

动机（回应 v3 独立审计第 1 条）：上一版 `prune()` 只删除「树里有、sdist 里没有」的文件，
`kept_files_matching_sdist` 也只检查路径属于工件列表，**既不校验保留文件的内容，也不补回缺失
的工件文件**。因此它只能支持"记录的源码文件哈希逐轮一致"，不能支持"源码树与冻结工件一致"。

本版做三件事，并逐项记录：

1. **补回/写回**：sdist 里有的文件，树里缺失或内容不同的一律按工件字节写回；
2. **删除**：树里有、sdist 里没有的文件一律删除（babel 的 fixture 输出、残留的 .pytest_cache 等）；
3. **清缓存**：删除 ``__pycache__`` / ``*.pyc`` / ``*.pyo``，使每轮起始时源码树内不存在
   解释器字节码缓存，从而"源码树状态"与"工件文件集合"是同一个可核验对象。

这不修改候选源码——它把源码恢复为冻结工件的原始字节，方向与"修改"相反。全过程只读 sdist、
只写候选源码树。用法：``python3 restore_source_tree.py [--apply] [候选目录名 ...]``
"""
import hashlib
import json
import pathlib
import shutil
import sys
import tarfile

from artifact_identity import identify_sdist, selected_sdist

ROOT = pathlib.Path(__file__).resolve().parent.parent  # pyhost-context/
ART = ROOT / "admission_artifacts"
TREE = ROOT / "admission_inspect"
IGNORE_PARTS = (".git",)
CACHE_DIRS = ("__pycache__",)
CACHE_SUFFIX = (".pyc", ".pyo")


def artifact_dir(name):
    """Return only the registered project's own, hash-verified directory."""
    return selected_sdist(ROOT, ART, name).parent


def artifact_identity(name):
    return identify_sdist(ROOT, ART, name)


def sdist_bytes(name):
    """Exact pinned sdist, independently checked by PKG-INFO name/version."""
    sdist = selected_sdist(ROOT, ART, name)
    files = {}
    with tarfile.open(sdist) as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            parts = pathlib.PurePosixPath(member.name).parts
            rel = "/".join(parts[1:]) if len(parts) > 1 else member.name
            if any(p in rel.split("/") for p in IGNORE_PARTS):
                continue
            files[rel] = tf.extractfile(member).read()
    return sdist, files


def sdist_files(name):
    """兼容旧调用：返回 ``(sdist 路径, {相对路径: sha256})``。"""
    sdist, payloads = sdist_bytes(name)
    return sdist, {rel: hashlib.sha256(data).hexdigest() for rel, data in payloads.items()}


def restore(name, apply=False):
    """把 ``admission_inspect/<name>`` 恢复为冻结 sdist；``apply=False`` 只报告。"""
    sdist, expected = sdist_bytes(name)
    source = TREE / name
    if not source.is_dir():
        raise FileNotFoundError(f"源码树不存在：{source}")

    restored, deleted, caches_removed, unchanged = [], [], [], 0

    for rel, data in sorted(expected.items()):
        target = source / rel
        want = hashlib.sha256(data).hexdigest()
        if target.is_file():
            got = hashlib.sha256(target.read_bytes()).hexdigest()
            if got == want:
                unchanged += 1
                continue
            restored.append(rel)
        else:
            restored.append(rel)
        if apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source).as_posix()
        if any(part in CACHE_DIRS for part in path.parts) or rel.endswith(CACHE_SUFFIX):
            caches_removed.append(rel)
            if apply:
                path.unlink()
            continue
        if rel in expected:
            continue
        deleted.append(rel)
        if apply:
            path.unlink()

    for directory in sorted(source.rglob("*"), reverse=True):
        if directory.is_dir() and directory.name in CACHE_DIRS:
            if apply:
                shutil.rmtree(directory, ignore_errors=True)

    return {
        "name": name,
        "sdist": sdist.name,
        "artifact_identity": artifact_identity(name),
        "artifact_file_count": len(expected),
        "bytes_verified_unchanged": unchanged,
        "restored_from_artifact": restored,
        "restored_count": len(restored),
        "deleted_not_in_artifact": deleted,
        "deleted_count": len(deleted),
        "cache_files_removed": caches_removed,
        "cache_files_removed_count": len(caches_removed),
        "applied": apply,
    }


def verify(name):
    """恢复后复核：源码树（不含缓存）是否与 sdist 逐字节一致。"""
    _, expected = sdist_bytes(name)
    source = TREE / name
    tree = {}
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        if any(part in CACHE_DIRS for part in path.parts):
            continue
        rel = path.relative_to(source).as_posix()
        tree[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    want = {rel: hashlib.sha256(data).hexdigest() for rel, data in expected.items()}
    return {
        "name": name,
        "artifact_identity": artifact_identity(name),
        "tree_file_count": len(tree),
        "artifact_file_count": len(want),
        "missing_from_tree": sorted(set(want) - set(tree)),
        "extra_in_tree": sorted(set(tree) - set(want)),
        "content_differs": sorted(rel for rel in set(tree) & set(want) if tree[rel] != want[rel]),
        "identical_to_artifact": tree == want,
    }


def main(argv):
    apply = "--apply" in argv
    names = [a for a in argv if not a.startswith("-")]
    if not names:
        names = sorted(p.name for p in TREE.iterdir() if p.is_dir())
    out = [restore(n, apply=apply) for n in names]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
