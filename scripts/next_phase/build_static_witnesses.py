"""ClawsGO Science Agent：13 项机制候选的**静态链见证**（只读，不执行候选代码）。

对每一项给出复核方要求的最小链证据：

* 读取点原文、符号、作用域与真实时机（按更正后的 R2：类体属导入/收集期）；
* 测试位置与调用 API 的原文行（不是布尔值）；
* 读取前是否在测试/conftest 中设置过该键，以及设置的作用域与先后关系；
* 依赖通道的工件身份（发行版、声明约束、解析版本、文件名、SHA-256、字节数）；
* 规则/扫描器版本与 358 项工件索引。

同时记录两处扫描缺陷的更正：

1. `os.environ["X"] = ...` 被当成读取（实为写入）——django-structlog 的 TZ 链即此情况；
   该候选改用同一 sdist 中 structlog NO_COLOR 的真实读取链，仍在池内，不扩池。
2. 预注册 R2 文本把"类体读取"写成构造期；类体在 class 语句执行时求值，属导入/收集期。
   13 项中没有类体读取，因此不影响任何一项的时机标签。
"""
import ast
import hashlib
import json
import pathlib
import re
import tarfile

NEXT = pathlib.Path(__file__).resolve().parent
EXT = NEXT.parent
ROOT = EXT.parent
CACHE = NEXT / "cache" / "sdists"
META = NEXT / "cache" / "meta"
STATE = NEXT / "cache" / "pools_state.json"
POOLS = NEXT / "POOLS_20261018.json"
SCREEN = NEXT / "SCREEN_EVIDENCE_20261018.json"
FRAME = EXT / "frame" / "frame_20261015.json"
OUT = NEXT / "STATIC_WITNESSES_20261018.json"

TEST_DIRS = ("tests", "test", "testing")
STUDY_KEYS = {"TZ", "LC_ALL", "LANG", "PYTHONHASHSEED", "NO_COLOR", "TERM", "COLUMNS", "LINES"}
ENV_PAT = re.compile(r"environ(?:\.get\(|\[)\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]"
                     r"|getenv\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]"
                     r"|environ\.setdefault\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_of(path):
    return sha256_bytes(pathlib.Path(path).read_bytes())


def load_json(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def members_of(path):
    out = {}
    with tarfile.open(path) as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            if ".." in pathlib.PurePosixPath(member.name).parts:
                continue
            out[member.name] = tar.extractfile(member).read()
    return out


def is_test(path):
    parts = pathlib.PurePosixPath(path).parts
    return any(p in TEST_DIRS for p in parts[:-1]) or parts[-1].startswith("test_") \
        or parts[-1].endswith("_test.py")


def scope_of(tree, lineno):
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
        elif node.name in ("__init__", "__new__"):
            kind = "init"
        else:
            kind = "function"
        if best["kind"] == "module" or kind in ("init", "function"):
            best = {"kind": kind, "function": getattr(node, "name", None),
                    "class": best["class"]}
        if isinstance(node, ast.ClassDef):
            best["class"] = node.name
    return best


def written_subscripts(tree):
    """收集处于赋值/删除目标的 os.environ[...] 行号——这些是写入，不是读取。"""
    writes = set()
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        elif isinstance(node, ast.Delete):
            targets = node.targets
        for target in targets:
            for sub in ast.walk(target):
                if isinstance(sub, ast.Subscript):
                    writes.add(sub.lineno)
    return writes


def read_hits(name, data):
    text = data.decode("utf-8", "replace")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    writes = written_subscripts(tree)
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for match in ENV_PAT.finditer(line):
            key = next(g for g in match.groups() if g)
            if key not in STUDY_KEYS:
                continue
            hits.append({
                "file": name, "line": lineno, "key": key,
                "source_line": line.strip(),
                "scope": scope_of(tree, lineno),
                "read_or_write": "write" if lineno in writes else "read",
            })
    return hits


def timing_of(scope):
    if scope["kind"] in ("module", "class"):
        return "import_or_collection"
    if scope["kind"] == "init":
        return "construction"
    return "call_time"


def fixture_info(tree, lineno):
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        if not (start <= lineno <= getattr(node, "end_lineno", node.lineno)):
            continue
        decorators = [ast.unparse(dec) for dec in node.decorator_list]
        if not any("fixture" in d for d in decorators):
            continue
        scope, autouse = "function", False
        for d in decorators:
            m = re.search(r"scope\s*=\s*['\"](\w+)['\"]", d)
            if m:
                scope = m.group(1)
            if "autouse=True" in d.replace(" ", ""):
                autouse = True
        return {"fixture": node.name, "scope": scope, "autouse": autouse,
                "decorators": decorators}
    return None


def env_settings(members, key):
    sites = []
    for name, data in members.items():
        if not name.endswith(".py") or not is_test(name):
            continue
        text = data.decode("utf-8", "replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            found = bool(
                re.search(r"setenv\(\s*['\"]%s['\"]" % re.escape(key), line)
                or re.search(r"environ\s*\[\s*['\"]%s['\"]\s*\]\s*=" % re.escape(key), line)
                or re.search(r"patch\.dict\([^)]*environ[^)]*%s" % re.escape(key), line))
            if not found:
                continue
            sites.append({"file": name, "line": lineno, "source_line": line.strip(),
                          "at_module_level": scope_of(tree, lineno)["kind"] == "module",
                          "fixture": fixture_info(tree, lineno)})
    return sites


def reference_sites(members, patterns, limit=8):
    rows = []
    for name, data in members.items():
        if not name.endswith(".py") or not is_test(name):
            continue
        text = data.decode("utf-8", "replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for kind, pattern in patterns:
                if pattern.search(line):
                    rows.append({"file": name, "line": lineno, "kind": kind,
                                 "source_line": line.strip()})
                    break
            if len(rows) >= limit:
                return rows
    return rows


def module_patterns(module, scope):
    esc = re.escape(module)
    patterns = [("import", re.compile(r"^\s*(?:from\s+%s\b|import\s+%s\b)" % (esc, esc)))]
    if scope.get("class") and scope.get("function") in ("__init__", "__new__"):
        patterns.append(("construction", re.compile(r"\b%s\s*\(" % re.escape(scope["class"]))))
    return patterns


def dependency_spec(members, dep):
    for name, data in members.items():
        if not name.endswith("PKG-INFO"):
            continue
        text = data.decode("utf-8", "replace")
        for line in text.splitlines():
            if line.lower().startswith("requires-dist:") and re.match(
                    r"\s*%s\b" % re.escape(dep), line.split(":", 1)[1].strip(), re.I):
                return line.split(":", 1)[1].strip()
    return None


def resolve_meta(dep, version):
    for path in META.glob("*.json"):
        try:
            doc = load_json(path)
        except Exception:
            continue
        filename = doc.get("filename", "")
        if re.match(r"^%s[-_]" % re.escape(dep.replace("-", "_")), filename) \
                or re.match(r"^%s-" % re.escape(dep), filename):
            if version and doc.get("version") == version:
                return doc
    return None


def dep_use_sites(members, dep):
    import_patterns = [("import", re.compile(r"^\s*(?:from\s+%s\b|import\s+%s\b)"
                                             % (re.escape(dep), re.escape(dep))))]
    api_patterns = {
        "rich": [("construction", re.compile(r"\brich\.console\.Console\s*\(|\bConsole\s*\("))],
        "structlog": [("call", re.compile(r"\bget_logger\s*\(|\bstructlog\.configure\s*\("))],
        "django": [("access", re.compile(r"\bdjango\.conf\.settings\b|\bsettings\.\w+"))],
        "pylint": [("construction", re.compile(r"\bTextReporter\s*\(|\bColorizedTextReporter\s*\("))],
    }.get(dep, [])
    rows = {"source_imports": [], "test_imports": [], "test_api_use": []}
    for name, data in members.items():
        if not name.endswith(".py"):
            continue
        text = data.decode("utf-8", "replace")
        test = is_test(name)
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(p.search(line) for _, p in import_patterns):
                rows["test_imports" if test else "source_imports"].append(
                    {"file": name, "line": lineno, "source_line": line.strip()})
            if test and any(p.search(line) for _, p in api_patterns):
                rows["test_api_use"].append(
                    {"file": name, "line": lineno, "source_line": line.strip()})
    for key in rows:
        rows[key] = rows[key][:8]
    return rows


def dep_members(chain):
    """依赖读取点位于依赖 sdist 中；按链上文件名定位缓存工件。"""
    filename = chain["file"].split("/")[0] + ".tar.gz"
    path = CACHE / filename
    if not path.is_file():
        raise RuntimeError(f"dependency_sdist_missing:{filename}")
    return members_of(path), path


def build_item(row, state):
    sdist = CACHE / row["sdist"]["filename"]
    if not sdist.is_file() or sha256_of(sdist) != row["sdist"]["sha256"]:
        raise RuntimeError(f"sdist_missing_or_drift:{row['project']}")
    members = members_of(sdist)
    chains = state["scanned"][row["project"]]["qualified_chains"]
    reads, write_chains = [], []
    for chain in chains:
        if chain["channel"].startswith("dependency"):
            pool, _path = dep_members(chain)
        else:
            pool = members
        member = chain["file"]
        if member not in pool:
            continue
        hits = [h for h in read_hits(member, pool[member])
                if h["line"] == chain["line"] and h["key"] == chain["key"]]
        if not hits:
            continue
        hit = hits[0]
        entry = {
            **hit,
            "declared_channel": chain["channel"],
            "declared_timing": ("import_or_collection" if "import" in chain["channel"]
                                else "construction"),
            "corrected_timing": timing_of(hit["scope"]),
            "timing_basis": ("类体在 class 语句执行时求值" if hit["scope"]["kind"] == "class"
                             else "模块顶层或 __init__"),
            "symbol": (f'{hit["scope"]["class"]}.{hit["scope"]["function"]}'
                       if hit["scope"]["class"] and hit["scope"]["function"]
                       else hit["scope"]["class"] or hit["scope"]["function"] or "<module>"),
            "dependency_version": chain.get("dep_version"),
        }
        if hit["read_or_write"] == "write":
            write_chains.append(entry)
        else:
            reads.append(entry)
    if not reads:
        raise RuntimeError(f"no_read_hit:{row['project']}")
    primary = reads[0]
    superseded = None
    if primary["key"] != row["key"] or primary["declared_channel"] != row["channel"]:
        superseded = {
            "pool_row_key": row["key"], "pool_row_channel": row["channel"],
            "selected_key": primary["key"], "selected_channel": primary["declared_channel"],
            "reason": "池行登记的首条链为写入（W1），改用同一候选的另一条真实读取链",
        }
    item = {
        "project": row["project"], "version": row["version"], "slot": row["slot"],
        "rank": row["rank"], "category": row["category"],
        "declared_channel": primary["declared_channel"],
        "declared_key": primary["key"],
        "declared_family": (row["family"] if not superseded else
                            f"dependency|{DEP_NAME.get(row['project'], row['project'])}|{primary['key']}"),
        "pool_row": {"key": row["key"], "channel": row["channel"], "family": row["family"]},
        "channel_superseded": superseded,
        "sdist": row["sdist"], "reads": reads, "write_chains_dropped": write_chains,
    }
    if not superseded:
        item["declared_family"] = row["family"]
    # 测试位置：项目通道找模块引用；依赖通道找依赖导入与 API 调用。
    if primary["declared_channel"].startswith("dependency"):
        dep = primary["file"].split("/")[0].rsplit("-", 1)[0].replace("_", "-")
        use = dep_use_sites(members, dep)
        item["test_references"] = use["test_imports"] + use["test_api_use"]
        item["source_references"] = use["source_imports"]
    else:
        module = primary["file"].split("/")[1:]
        if module and module[0] == "src":
            module = module[1:]
        if module[-1] == "__init__.py":
            module = module[:-1]
        else:
            module[-1] = module[-1][:-3]
        item["test_references"] = reference_sites(
            members, module_patterns(".".join(module), primary["scope"]))
    item["environment_settings_before_read"] = {
        "key": primary["key"],
        "sites": env_settings(members, primary["key"]),
        "r8_analysis": r8_analysis(env_settings(members, primary["key"]),
                                   primary["corrected_timing"]),
    }
    tests_present = has_tests(members)
    if primary["declared_channel"].startswith("dependency"):
        dep_ev = dependency_evidence(members, row["project"])
        item["dependency_test_evidence"] = dep_ev
        item["test_path"] = dep_ev["direct_test_calls"] + dep_ev["product_calls"]
        static_eligible = tests_present and dep_ev["static_eligible"]
        ineligible_reason = None if static_eligible else (
            "no_tests_in_sdist" if not tests_present else dep_ev["reason"])
    else:
        test_path = test_path_rows(members, row["project"])
        item["test_path"] = test_path
        static_eligible = tests_present and bool(test_path)
        ineligible_reason = None
        if not tests_present:
            ineligible_reason = "no_tests_in_sdist"
        elif not test_path:
            ineligible_reason = "no_test_line_triggering_read"
    if primary["declared_channel"].startswith("dependency"):
        dep = primary["file"].split("/")[0].rsplit("-", 1)[0].replace("_", "-")
        version = primary.get("dependency_version")
        spec = dependency_spec(members, dep)
        meta = resolve_meta(dep, version)
        path = CACHE / meta["filename"] if meta else None
        item["dependency"] = {
            "name": dep, "declared_constraint": spec, "resolved_version": version,
            "filename": meta.get("filename") if meta else None,
            "sha256": meta.get("sha256") if meta else None,
            "bytes": path.stat().st_size if path and path.is_file() else None,
            "url": meta.get("url") if meta else None,
            "read_site": primary["file"] + ":" + str(primary["line"]),
            "read_source_line": primary["source_line"],
            "read_symbol": primary["symbol"],
            "read_timing": primary["corrected_timing"],
            "artifact_in_cache": bool(path and path.is_file()),
        }
    item["qualification"] = {
        "rule": ("R4_dependency_channel" if primary["declared_channel"].startswith("dependency")
                 else ("R5_construction_own_source_and_R3"
                       if primary["declared_channel"].endswith("construction")
                       else "R5_import_or_collection_and_R3")),
        "test_reference_rows": len(item["test_references"] or []),
        "test_reference_present": bool(item["test_references"]),
        "tests_present_in_sdist": tests_present,
        "test_path_rows": len(item["test_path"]),
        "static_eligible": static_eligible,
        "ineligible_reason": ineligible_reason,
        "read_is_read_not_write": True,
        "timing_matches_declared": primary["declared_timing"] == primary["corrected_timing"],
    }
    return item


DEP_NAME = {"django-structlog": "structlog"}

# 每项的“测试经过读取路径”取证式样：只收测试文件里能触发读取的原文行。
TEST_PATH_PATTERNS = {
    "ipython": [(r"TerminalInteractiveShell\.instance\(|get_ipython\(\)\.Completer",
                 "shell 构造链上的 IPCompleter.__init__ 读取 TERM")],
    "structlog": [(r"^\s*(?:import\s+structlog\b|from\s+structlog\b)",
                   "导入模块即执行模块级 NO_COLOR 读取")],
    "rich-toolkit": [(r"\bConsole\s*\(|from\s+rich(?:\.console)?\s+import\s+Console",
                      "构造 rich Console 读取 COLUMNS")],
    "pylint": [(r"\bColorizedTextReporter\s*\(", "构造 reporter 读取 TERM")],
    "twine": [(r"rich\.reconfigure\(|^\s*import\s+rich\b",
               "rich.reconfigure 构造 Console 读取 COLUMNS")],
    "rich-click": [(r"\bConsole\s*\(", "构造 rich Console 读取 COLUMNS")],
    "argparse": [(r"\.format_help\(\)|\bHelpFormatter\s*\(",
                  "format_help 构造 HelpFormatter 读取 COLUMNS")],
    "hatch": [(r"\bConsole\s*\(|from\s+rich\.console\s+import\s+Console",
               "构造 rich Console 读取 COLUMNS")],
    "pdm": [(r"\bget_console\(|\bConsole\s*\(", "取用 rich Console 读取 COLUMNS")],
    "schemathesis": [(r"\bConsole\s*\(", "构造 rich Console 读取 COLUMNS")],
}


CONSTRUCT_FUNCS = {"Console", "reconfigure", "get_console", "make_console"}
PRODUCT_EXERCISE = {
    "twine": re.compile(r"cli\.dispatch\(|dunder_main\.main\(|\bmain\(\["),
    "schemathesis": re.compile(r"\bmain\(|runner\.invoke\(|CliRunner"),
}


def call_sites(members, only_tests):
    rows = []
    for name, data in members.items():
        if not name.endswith(".py") or is_test(name) != only_tests:
            continue
        text = data.decode("utf-8", "replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        lines = text.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            fname = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None)
            if fname not in CONSTRUCT_FUNCS:
                continue
            # 只认 rich.reconfigure；TextIOWrapper.reconfigure 之流不是依赖构造。
            if fname == "reconfigure" and not (
                    isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and func.value.id == "rich"):
                continue
            has_width = any(kw.arg == "width" for kw in node.keywords)
            rows.append({"file": name, "line": node.lineno, "call": fname,
                         "has_width_argument": has_width,
                         "reads_columns": not has_width,
                         "source_line": lines[node.lineno - 1].strip()[:200]})
    return rows


def pytest_forces_width(members):
    for name, data in members.items():
        if name.endswith(".py") and not is_test(name):
            text = data.decode("utf-8", "replace")
            if "PYTEST_VERSION" in text and re.search(r"setdefault\(\s*['\"]width['\"]", text):
                return name
    return None


def has_tests(members):
    return any(name.endswith(".py") and is_test(name) for name in members)


def dependency_evidence(members, project):
    tests = call_sites(members, only_tests=True)
    product = call_sites(members, only_tests=False)
    tests_text = "\n".join(data.decode("utf-8", "replace") for name, data in members.items()
                           if name.endswith(".py") and is_test(name))
    pattern = PRODUCT_EXERCISE.get(project)
    exercised = bool(pattern and pattern.search(tests_text))
    direct = [row for row in tests if row["reads_columns"]]
    indirect = [row for row in product if row["reads_columns"]] if exercised else []
    override = pytest_forces_width(members)
    if override:
        eligible, reason = False, "pytest_forces_width_override"
    elif direct:
        eligible, reason = True, "test_constructs_dependency_without_width"
    elif indirect:
        eligible, reason = True, "product_constructs_without_width_and_tests_exercise_product"
    else:
        eligible, reason = False, "no_test_path_through_read"
    return {"direct_test_calls": direct[:6], "product_calls": indirect[:6],
            "product_exercised": exercised,
            "pytest_width_override_file": override,
            "static_eligible": eligible, "reason": reason}


def test_path_rows(members, project, limit=4):
    patterns = [(re.compile(p), why) for p, why in TEST_PATH_PATTERNS.get(project, [])]
    rows = []
    for name, data in members.items():
        if not name.endswith(".py") or not is_test(name):
            continue
        text = data.decode("utf-8", "replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for pattern, why in patterns:
                if pattern.search(line):
                    rows.append({"file": name, "line": lineno,
                                 "source_line": line.strip(), "why": why})
                    break
            if len(rows) >= limit:
                return rows
    return rows


def r8_analysis(sites, timing):
    if not sites:
        return {"sets_key": False,
                "conclusion": "tests/conftest 未设置该键；读取前不存在无条件固定"}
    module_level = [s for s in sites if s["at_module_level"]]
    fixtures = [s for s in sites if s["fixture"]]
    plain = [s for s in sites if not s["at_module_level"] and not s["fixture"]]
    if module_level:
        return {"sets_key": True, "unconditional_sites": module_level,
                "conclusion": "存在模块级无条件设置；需按 R8 判断是否早于读取"}
    if fixtures and not plain:
        scopes = sorted({str((s["fixture"] or {}).get("scope")) for s in sites})
        return {"sets_key": True, "fixture_scopes": scopes,
                "conclusion": ("仅由 fixture 设置；fixture 在收集之后运行，"
                               f"读取时机为 {timing}，需按实际先后判读")}
    return {"sets_key": True, "plain_test_sites": len(plain),
            "fixture_sites": len(fixtures),
            "conclusion": ("设置只出现在个别测试函数（非 autouse fixture）；"
                           f"本读取时机为 {timing}，收集期/其他用例不受这些设置影响，"
                           "同一用例内需按先后判读")}


def artifact_index():
    pools = load_json(POOLS)
    by_project = {c["project"]: c for c in pools["mechanism_pool"]["rows"]}
    by_project.update({c["project"]: c for c in pools["reference_pool"]["rows"]})
    frame = {c["project"]: c for c in load_json(FRAME)["candidates"]}
    screen = load_json(SCREEN)
    rows = []
    for row in screen["rows"]:
        name = row["project"]
        source = by_project.get(name) or frame.get(name) or {}
        rows.append({"project": name, "version": row["version"],
                     "status": row["status"],
                     "sdist": source.get("sdist")})
    for name, row in by_project.items():
        if not any(r["project"] == name for r in rows):
            rows.append({"project": name, "version": row["version"],
                         "status": "pool", "sdist": row.get("sdist")})
    return sorted(rows, key=lambda r: r["project"])


def main():
    pools = load_json(POOLS)
    state = load_json(STATE)
    items = [build_item(row, state) for row in pools["mechanism_pool"]["rows"]]
    index = artifact_index()
    document = {
        "schema_version": "pyhost-context/next-static-witnesses/1.0",
        "written_at_utc": "2026-10-18T00:00:00Z",
        "scope": "13 项机制池候选；不重扫 358 项",
        "scanner": {
            "path": "extension/next_phase/scan_candidate.py",
            "sha256": sha256_of(NEXT / "scan_candidate.py"),
            "pools_builder": "extension/next_phase/build_pools.py",
            "pools_builder_sha256": sha256_of(NEXT / "build_pools.py"),
            "preregistration_sha256": sha256_of(NEXT / "PRE_REGISTRATION_20261018.json"),
            "command": "python3 extension/next_phase/build_pools.py <start> <limit>",
        },
        "corrections": [
            {"id": "W1_environ_subscript_write",
             "found": "django-structlog 的 django TZ 链是 os.environ['TZ'] = ...（写入），"
                      "不是读取；扫描器 environ_getitem 未区分赋值目标。",
             "effect": "该候选改用同一 sdist 中 structlog _config.py:34 的真实读取链；"
                       "家族改为 dependency|structlog|NO_COLOR，仍在池内，不扩池。"},
            {"id": "W2_class_body_timing",
             "found": "R2 原文把类体读取写为构造期。",
             "effect": "类体在 class 语句执行时求值，属导入/收集期；13 项中没有类体读取，"
                       "所有时机标签不变。"},
            {"id": "W3_source_import_counted_as_test_use",
             "found": "结构日志/插件类候选的 sdist 不含任何测试文件，"
                      "原扫描把候选源码里的依赖导入当成'测试使用依赖 API'。",
             "effect": "structlog-sentry、django-structlog、pylint-plugin-utils 三项"
                       "按 R4 判为静态不合格，保留在池内并记录理由，不派发准入；"
                       "机制池可执行候选为 10 项。"},
        ],
        "items": items,
        "artifact_index": index,
        "artifact_index_count": len(index),
        "summary": {
            "items": len(items),
            "write_chains_dropped": {i["project"]: len(i["write_chains_dropped"])
                                     for i in items if i["write_chains_dropped"]},
            "statically_eligible": [i["project"] for i in items
                                    if i["qualification"]["static_eligible"]],
            "statically_ineligible": {i["project"]: i["qualification"]["ineligible_reason"]
                                      for i in items
                                      if not i["qualification"]["static_eligible"]},
            "all_timing_matches": all(i["qualification"]["timing_matches_declared"]
                                      for i in items),
            "candidate_executions": 0,
        },
    }
    OUT.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(document["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
