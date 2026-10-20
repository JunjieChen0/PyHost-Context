"""ClawsGO Science Agent：新阶段候选的**静态读取链**扫描（只读，不执行候选代码）。

用途：按 `PRE_REGISTRATION_20261018.json` 的 R1—R8 规则，在冻结 sdist 上识别

* 项目自身在 import/collection 期或构造期读取环境键；
* 测试是否引用读取所在模块/类，且未在读取前无条件固定该键。

只做语法与文本分析：`ast.parse` 不导入、不执行任何候选代码。
"""
import ast
import io
import json
import pathlib
import re
import tarfile

ENV_PATTERNS = [
    ("environ_getitem", re.compile(r"os\.environ\s*\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\]")),
    ("environ_get", re.compile(r"os\.environ\.get\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]")),
    ("getenv", re.compile(r"(?:os\.)?getenv\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]")),
    ("environ_setdefault", re.compile(r"os\.environ\.setdefault\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]")),
    ("environ_get_from", re.compile(r"environ\.get\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]")),
]
INFRA_KEYS = re.compile(
    r"^(PIP_|SETUPTOOLS_|HATCH_|POETRY_|PDM_|UV_|NOX_|TOX_|PYTEST_|PYTHONPATH$|"
    r"PYTHONHOME$|VIRTUAL_ENV$|CONDA_|CI$|GITHUB_|GITLAB_|BUILD_|CXX$|CC$|LDFLAGS$|CFLAGS$)")
TEST_DIRS = ("tests", "test", "testing")
TEST_FILE = re.compile(r"(^test_.*\.py$|.*_test\.py$)")
WRAPPER_NAMES = ("__init__", "__new__", "__post_init__")


def iter_members(tar):
    for member in tar.getmembers():
        if not member.isfile():
            continue
        name = member.name
        if ".." in pathlib.PurePosixPath(name).parts:
            continue
        if not name.endswith(".py"):
            continue
        data = tar.extractfile(member).read()
        yield name, data


def module_name(path):
    parts = [p for p in path.split("/") if p][1:]  # 去掉 sdist 顶层目录
    if not parts:
        return None
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]
    return ".".join(parts)


def top_module(path):
    parts = [p for p in path.split("/") if p][1:]
    return parts[0][:-3] if parts and parts[0].endswith(".py") else (parts[0] if parts else None)


def scope_of(tree, lineno):
    """返回读取所在作用域：module / class / init / function，以及类名与函数名。"""
    best = {"kind": "module", "function": None, "class": None}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        end = getattr(node, "end_lineno", node.lineno)
        if not (start <= lineno <= end):
            continue
        if isinstance(node, ast.ClassDef):
            kind = "class"
        elif node.name in WRAPPER_NAMES:
            kind = "init"
        else:
            kind = "function"
        # 取最内层作用域
        if best["kind"] == "module" or (kind in ("init", "function")):
            best = {"kind": kind, "function": getattr(node, "name", None),
                    "class": best["class"]}
        if isinstance(node, ast.ClassDef):
            best["class"] = node.name
    return best


def scan_source(path, data):
    try:
        text = data.decode("utf-8", "replace")
        tree = ast.parse(text)
    except Exception:
        return []
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for kind, pattern in ENV_PATTERNS:
            for match in pattern.finditer(line):
                key = match.group(1)
                hits.append({"file": path, "line": lineno, "key": key,
                             "read_kind": kind, "text": line.strip()[:160],
                             "scope": scope_of(tree, lineno)})
    return hits


def classify(hits):
    out = []
    for hit in hits:
        if INFRA_KEYS.match(hit["key"]):
            hit["classification"] = "infrastructure_key"
        elif hit["scope"]["kind"] == "module":
            hit["classification"] = "import_or_collection"
        elif hit["scope"]["kind"] == "class":
            hit["classification"] = "import_or_collection"
        elif hit["scope"]["kind"] == "init":
            hit["classification"] = "construction"
        else:
            hit["classification"] = "call_time"
        out.append(hit)
    return out


def test_evidence(members, module):
    """测试是否引用该模块/类，是否无条件设置相关键。"""
    imports = []
    sets = []
    for name, data in members:
        parts = pathlib.PurePosixPath(name).parts
        if len(parts) < 3:
            continue
        if not any(p in TEST_DIRS for p in parts[:-1]):
            continue
        text = data.decode("utf-8", "replace")
        if module and (module in text or module.split(".")[0] in text):
            imports.append(name)
        for match in re.finditer(r"(?:monkeypatch\.setenv|os\.environ\s*\[)[^'\"\n]*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]", text):
            sets.append({"file": name, "key": match.group(1)})
        for match in re.finditer(r"setenv\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]", text):
            sets.append({"file": name, "key": match.group(1)})
    return imports, sets


def scan_sdist(path):
    with tarfile.open(path) as tar:
        members = list(iter_members(tar))
    hits = []
    for name, data in members:
        hits.extend(scan_source(name, data))
    hits = classify(hits)
    by_module = {}
    for hit in hits:
        module = module_name(hit["file"])
        by_module.setdefault(module, []).append(hit)
    chain = []
    for module, module_hits in by_module.items():
        imports, sets = test_evidence(members, module)
        keys_set = {s["key"] for s in sets}
        for hit in module_hits:
            if hit["classification"] in ("import_or_collection", "construction") \
                    and imports and hit["key"] not in keys_set:
                chain.append({**hit, "module": module, "test_files": imports[:5],
                              "channel": ("project_import" if hit["classification"] == "import_or_collection"
                                          else "project_construction")})
    return {"sdist": str(path), "py_files": len(members), "hits": hits, "chains": chain}


if __name__ == "__main__":
    import sys
    print(json.dumps(scan_sdist(sys.argv[1]), ensure_ascii=False, indent=1)[:4000])
