"""ClawsGO Science Agent：把预览源码树与冻结工件逐文件比对。

用途：确认 ``admission_inspect/<candidate>`` 与 ``admission_artifacts/`` 中的 frozen
sdist **文件集合与内容一致**。两类偏差分开报告：

- ``extra``：树里有、工件里没有——测试套件自建的临时文件，或上一轮运行的残留；
- ``missing`` / ``differing``：工件里有、树里没有或内容不同——这才会影响"固定工件"的前提。

前瞻修订：以注册表指定的 sdist 为准；不再把 wheel 文件混入源码比较。
只读，不修改任何文件。用法：``python3 verify_source_tree.py [candidate ...]``
"""
import hashlib
import json
import pathlib
import sys
import tarfile
from artifact_identity import identify_sdist, selected_sdist

ROOT = pathlib.Path(__file__).resolve().parent.parent  # pyhost-context/
ART = ROOT / "admission_artifacts"
TREE = ROOT / "admission_inspect"
IGNORE_PARTS = ("__pycache__", ".git")
IGNORE_SUFFIX = (".pyc", ".pyo")


def _normalize(rel):
    """sdist 顶层目录名（如 ``babel-2.15.0/``）剥掉，得到包内相对路径。"""
    parts = pathlib.PurePosixPath(rel).parts
    return "/".join(parts[1:]) if len(parts) > 1 else rel


def artifact_files(candidate_dir):
    """Hash only the exact pinned sdist, never a wheel union or first archive."""
    candidate_dir = pathlib.Path(candidate_dir)
    sdist = selected_sdist(ROOT, ART, candidate_dir.name)
    if sdist.parent != candidate_dir.resolve():
        raise ValueError("artifact_directory_identity_mismatch")
    files = {}
    with tarfile.open(sdist) as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            rel = _normalize(member.name)
            if any(p in rel.split("/") for p in IGNORE_PARTS):
                continue
            files[rel] = hashlib.sha256(tf.extractfile(member).read()).hexdigest()
    return files


def tree_files(source_dir):
    """返回源码树的 ``{相对路径: sha256}``，排除 __pycache__ 与编译产物。"""
    files = {}
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source_dir).as_posix()
        if any(p in rel.split("/") for p in IGNORE_PARTS) or rel.endswith(IGNORE_SUFFIX):
            continue
        files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def artifact_dir(name):
    return selected_sdist(ROOT, ART, name).parent


def compare(name, source_dir):
    artifact = artifact_files(artifact_dir(name))
    tree = tree_files(source_dir)
    extra = sorted(set(tree) - set(artifact))
    missing = sorted(set(artifact) - set(tree))
    differing = sorted(p for p in set(tree) & set(artifact) if tree[p] != artifact[p])
    return {
        "name": name,
        "artifact_identity": identify_sdist(ROOT, ART, name),
        "artifact_dir": str(artifact_dir(name).relative_to(ROOT)),
        "source_dir": str(source_dir.relative_to(ROOT)),
        "artifact_file_count": len(artifact),
        "tree_file_count": len(tree),
        "extra_in_tree": extra,
        "missing_from_tree": missing,
        "differing": differing,
        "tree_is_clean_subset_of_artifact": not missing and not differing,
        "tree_equals_artifact": not (extra or missing or differing),
    }


def main(argv):
    names = argv or sorted(p.name for p in TREE.iterdir() if p.is_dir())
    out = []
    for name in names:
        source_dir = TREE / name
        if not source_dir.is_dir():
            continue
        out.append(compare(name, source_dir))
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
