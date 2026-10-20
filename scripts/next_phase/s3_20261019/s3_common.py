"""ClawsGO Science Agent：S3 输入构建与执行共用的只读工具。

本模块只做字节级操作：哈希、确定性打包/解包、树指纹。它不导入候选代码，
也不执行任何候选命令。
"""
from __future__ import annotations

import gzip
import hashlib
import io
import pathlib
import tarfile

FIXED_MTIME = 0
FIXED_MODE = 0o644


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    return sha256_bytes(pathlib.Path(path).read_bytes())


def tree_hash(root) -> str:
    """目录树指纹：按相对路径排序的 ``path:sha256`` 行，末尾带换行。"""
    root = pathlib.Path(root)
    rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rows.append(f"{path.relative_to(root).as_posix()}:{sha256_file(path)}")
    return sha256_bytes(("\n".join(rows) + "\n").encode("utf-8"))


def file_rows(root) -> list[dict]:
    root = pathlib.Path(root)
    rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        data = path.read_bytes()
        rows.append({"path": path.relative_to(root).as_posix(),
                     "sha256": sha256_bytes(data), "bytes": len(data)})
    return rows


def pack_tree(root, arc_prefix: str) -> bytes:
    """确定性 tar.gz：固定 mtime/uid/gid/mode、排序、无 gzip 文件名与时间戳。"""
    root = pathlib.Path(root)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            data = path.read_bytes()
            name = f"{arc_prefix}/{path.relative_to(root).as_posix()}"
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = FIXED_MTIME
            info.mode = FIXED_MODE
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    return gzip.compress(buffer.getvalue(), mtime=0, compresslevel=9)


def unpack_tree(blob: bytes, target) -> list[str]:
    """解包到目标目录，拒绝绝对路径、``..`` 与链接成员；返回文件名列表。"""
    target = pathlib.Path(target)
    target.mkdir(parents=True, exist_ok=True)
    names = []
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                raise ValueError(f"non_file_member:{member.name}")
            pure = pathlib.PurePosixPath(member.name)
            if pure.is_absolute() or ".." in pure.parts:
                raise ValueError(f"unsafe_member:{member.name}")
            parts = pure.parts[1:]
            if not parts:
                continue
            out = target.joinpath(*parts)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(tar.extractfile(member).read())
            names.append("/".join(parts))
    return sorted(names)


EXCLUDED_PACKAGE_PARTS = {"__pycache__", ".pytest_cache"}


def package_digest(root, exclude=(), exclude_review=True) -> str:
    """输入包指纹：排序 ``path:sha256`` 行加末行换行后整体 SHA-256。

    排除 ``__pycache__``/``.pytest_cache``、审阅件副本 ``review/`` 与包清单，
    与 ``build_s3_inputs.package_digest`` 的算法一致，供交付包内离线复算。
    """
    root = pathlib.Path(root)
    excluded = {pathlib.Path(path).resolve() for path in exclude}
    rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        resolved = path.resolve()
        if resolved in excluded:
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED_PACKAGE_PARTS for part in relative.parts):
            continue
        if exclude_review and relative.parts and relative.parts[0] == "review":
            continue
        if relative.name == "PACKAGE_MANIFEST.json":
            continue
        rows.append(f"{relative.as_posix()}:{sha256_file(path)}")
    return sha256_bytes(("\n".join(rows) + "\n").encode("utf-8"))
