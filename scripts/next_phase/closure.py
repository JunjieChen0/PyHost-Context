"""ClawsGO Science Agent：S1a 入口的**本地导入闭包**（只读静态解析）。

从入口文件出发，按 AST 的 ``import`` / ``from ... import`` 递归解析项目内的本地模块，
给出每个被导入文件的 SHA-256。解析不到项目文件的模块（标准库、第三方）记入
``unresolved``，不计入闭包——它们由冻结解释器提供，不随本包字节变化。

这不是运行时导入追踪；实际读取的规范 JSON 由入口的 ``static_inputs()`` 另行显式绑定。
合成消费证明会在运行后核对入口实际打开/导入的项目内文件是否都落在闭包里。
"""
import ast
import hashlib
import pathlib

SKIP_PARTS = {"__pycache__", ".git"}


def sha256_of(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def _search_roots(entry):
    entry = pathlib.Path(entry).resolve()
    here = entry.parent
    roots = [here, here / "admission"]
    # extension/tooling、tooling、tooling/admission_runner、matrix/tooling、matrix_runner
    for parent in here.parents:
        for rel in ("extension/tooling", "tooling", "tooling/admission_runner",
                    "matrix/tooling", "matrix/tooling/matrix_runner"):
            candidate = parent / rel
            if candidate.is_dir():
                roots.append(candidate)
    seen, unique = set(), []
    for root in roots:
        if root.is_dir() and root not in seen:
            seen.add(root)
            unique.append(root)
    return unique


def _resolve(module, roots):
    parts = module.split(".")
    for root in roots:
        base = root.joinpath(*parts)
        if base.with_suffix(".py").is_file():
            return base.with_suffix(".py")
        if (base / "__init__.py").is_file():
            return base / "__init__.py"
    return None


def _module_of(path, roots):
    """把文件路径变成可导入名（用于相对导入解析）。"""
    path = pathlib.Path(path).resolve()
    for root in roots:
        try:
            rel = path.relative_to(root.resolve())
        except ValueError:
            continue
        parts = list(rel.parts)
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1][:-3]
        if parts:
            return ".".join(parts), root
    return None, None


def _imports(tree, module_name):
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                prefix = module_name.split(".")
                if node.level > 1:
                    prefix = prefix[:-(node.level - 1)]
                base = ".".join(prefix + ([base] if base else []))
            for alias in node.names:
                if alias.name == "*":
                    continue
                out.append(base + "." + alias.name if base else alias.name)
                if base:
                    out.append(base)
    return out


def closure_report(entry):
    entry = pathlib.Path(entry).resolve()
    roots = _search_roots(entry)
    files, unresolved = {}, []
    queue = [entry]
    seen = set()
    while queue:
        path = pathlib.Path(queue.pop()).resolve()
        if path in seen or any(part in SKIP_PARTS for part in path.parts):
            continue
        seen.add(path)
        try:
            rel = str(path.relative_to(entry.parents[len(entry.parents) - 1]))
        except ValueError:
            rel = str(path)
        # 统一相对 pyhost-context 根
        for parent in path.parents:
            if (parent / "extension").is_dir() and (parent / "matrix").is_dir():
                rel = str(path.relative_to(parent))
                break
        files[rel] = sha256_of(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        module_name, _ = _module_of(path, roots)
        for name in _imports(tree, module_name or ""):
            target = _resolve(name, roots)
            if target is not None:
                if target.resolve() not in seen:
                    queue.append(target)
            else:
                top = name.split(".")[0]
                if not (path.parent / (top + ".py")).is_file():
                    unresolved.append(name)
    return {"files": dict(sorted(files.items())),
            "unresolved": sorted(set(unresolved)),
            "entry": str(entry.name),
            "count": len(files)}


def local_import_closure(entry):
    return closure_report(entry)["files"]
