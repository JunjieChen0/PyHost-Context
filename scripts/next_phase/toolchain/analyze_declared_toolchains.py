"""ClawsGO Science Agent：九项机制候选的**声明工具链**只读静态分析。

只读取既有冻结 sdist 的字节，解析安装元数据 extras、PEP 735 依赖组、
tox / pytest 配置与 requirements 文件；不导入候选、不构建、不运行 resolver、
不联网。输出每项的声明来源、pytest 约束、插件与配置，以及本地既有工件中
可确定的精确版本。

用法：``python3 analyze_declared_toolchains.py``
"""
import configparser
import json
import pathlib
import re
import tarfile
import tomllib

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
ROOT = NEXT.parent
REQUEST = HERE / "EXISTING_BYTES_REQUEST.json"
OUT = NEXT / "DECLARED_TOOLCHAINS_20261018.json"

TEST_EXTRAS = ("test", "tests", "testing", "dev", "develop", "development", "ci")
TEST_GROUPS = ("test", "tests", "testing", "dev", "develop", "development", "ci")
REQUIREMENT_LEAVES = ("requirements.txt", "requirements-test.txt", "requirements_test.txt",
                      "requirements-dev.txt", "test-requirements.txt", "dev-requirements.txt")

# 配置/命令行选项到分发包的保守映射；只列能由选项名直接确定的项。
OPTION_PLUGINS = (
    (re.compile(r"^--cov"), "pytest-cov"),
    (re.compile(r"^--no-cov"), "pytest-cov"),
    (re.compile(r"^--disable-socket"), "pytest-socket"),
    (re.compile(r"^--allow-(hosts|unix-socket)"), "pytest-socket"),
    (re.compile(r"^--enable-socket"), "pytest-socket"),
    (re.compile(r"^(-n|--numprocesses)(=|$)"), "pytest-xdist"),
    (re.compile(r"^--dist(=|$)"), "pytest-xdist"),
    (re.compile(r"^--benchmark"), "pytest-benchmark"),
    (re.compile(r"^--asyncio-mode"), "pytest-asyncio"),
    (re.compile(r"^--timeout"), "pytest-timeout"),
    (re.compile(r"^--randomly"), "pytest-randomly"),
    (re.compile(r"^--remaster"), "pytest-remaster"),
    (re.compile(r"^--reruns"), "pytest-rerunfailures"),
    (re.compile(r"^--instafail"), "pytest-instafail"),
    (re.compile(r"^--forked"), "pytest-forked"),
    (re.compile(r"^--json-report"), "pytest-json-report"),
    (re.compile(r"^--html"), "pytest-html"),
)
CONFIG_PLUGINS = {
    "asyncio_mode": "pytest-asyncio",
    "asyncio_default_fixture_loop_scope": "pytest-asyncio",
    "timeout": "pytest-timeout",
}


def sha(path):
    import hashlib
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def read_members(path):
    with tarfile.open(path) as tar:
        files = {m.name: tar.extractfile(m).read()
                 for m in tar.getmembers() if m.isfile()}
    return files


def top_dir(files):
    return sorted({name.split("/")[0] for name in files})[0]


def parse_requirement(text):
    try:
        return Requirement(text)
    except Exception:
        return None


def requirement_record(text, source, line, channel, extra=None):
    req = parse_requirement(text)
    if req is None:
        return {"raw": text, "source": source, "line": line, "channel": channel,
                "extra": extra, "name": None}
    return {"raw": text, "source": source, "line": line, "channel": channel,
            "extra": extra, "name": canonicalize_name(req.name),
            "specifier": str(req.specifier), "marker": str(req.marker) if req.marker else None,
            "url": req.url}


def resolve_group(name, groups, seen=None):
    """展开 PEP 735 依赖组，含 include-group；返回 (requirements, includes)。"""
    seen = seen or set()
    if name in seen or name not in groups:
        return [], []
    seen.add(name)
    items, includes = [], []
    for entry in groups[name]:
        if isinstance(entry, dict) and "include-group" in entry:
            inner = str(entry["include-group"])
            includes.append(inner)
            sub_reqs, sub_inc = resolve_group(inner, groups, seen)
            items.extend(sub_reqs)
            includes.extend(sub_inc)
        elif isinstance(entry, str):
            items.append(entry)
    return items, includes


def collect(path):
    files = read_members(path)
    top = top_dir(files)
    record = {"files_scanned": [], "declarations": [], "pytest_config": {},
              "requires_python": None, "extras": {}, "groups": {}, "group_includes": {},
              "tox": {}, "local_plugins": [], "errors": []}

    # PKG-INFO
    pkg = next((name for name in files if name.endswith("PKG-INFO")), None)
    if pkg:
        record["files_scanned"].append(pkg)
        text = files[pkg].decode("utf-8", "replace")
        for i, line in enumerate(text.splitlines(), 1):
            if line.startswith("Requires-Python:"):
                record["requires_python"] = line.split(":", 1)[1].strip()
            if not line.startswith("Requires-Dist:"):
                continue
            body = line.split(":", 1)[1].strip()
            m = re.search(r'extra\s*==\s*["\']([^"\']+)["\']', body)
            extra = m.group(1) if m else None
            req_text = body.split(";", 1)[0].strip()
            item = requirement_record(req_text, pkg, i, "metadata_extra",
                                      extra=extra) if extra else requirement_record(
                req_text, pkg, i, "runtime")
            item["marker"] = body.split(";", 1)[1].strip() if ";" in body else None
            record["declarations"].append(item)
            if extra:
                record["extras"].setdefault(extra, []).append(item)

    # pyproject
    pyproject = next((name for name in files if name.count("/") == 1
                      and name.endswith("pyproject.toml")), None)
    if pyproject:
        record["files_scanned"].append(pyproject)
        try:
            doc = tomllib.loads(files[pyproject].decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            record["errors"].append(f"pyproject:{exc}")
            doc = {}
        project = doc.get("project") or {}
        if project.get("requires-python"):
            record["requires_python"] = project["requires-python"]
        for i, text in enumerate(project.get("optional-dependencies", {}) or {}, 1):
            pass
        for extra, items in (project.get("optional-dependencies") or {}).items():
            for text in items:
                item = requirement_record(text, pyproject, None, "project_extra", extra=extra)
                record["declarations"].append(item)
                record["extras"].setdefault(extra, []).append(item)
        groups = doc.get("dependency-groups") or {}
        record["groups"] = {name: list(items) for name, items in groups.items()}
        optional = project.get("optional-dependencies") or {}
        project_name = project.get("name") or ""
        record["self_extra_expansions"] = []
        for name in groups:
            items, includes = resolve_group(name, groups)
            record["group_includes"][name] = sorted(set(includes))
            for text in items:
                expanded = _expand_self_extra(text, project_name, optional)
                if expanded is not None:
                    record["self_extra_expansions"].append(
                        {"group": name, "reference": text, "expanded": expanded})
                    for sub in expanded:
                        record["declarations"].append(
                            requirement_record(sub, pyproject, None, "self_extra_expansion",
                                               extra=name))
                else:
                    record["declarations"].append(
                        requirement_record(text, pyproject, None, "dependency_group", extra=name))
        pytest_cfg = ((doc.get("tool") or {}).get("pytest") or {}).get("ini_options") or {}
        if pytest_cfg:
            record["pytest_config"] = pytest_cfg
        for plugin in pytest_cfg.get("required_plugins") or []:
            name = str(plugin).split(">=")[0].split("==")[0].split("~=")[0].strip()
            if name:
                record["declarations"].append(
                    requirement_record(name, pyproject, None, "required_plugins"))
        for arg in _as_list(pytest_cfg.get("addopts")):
            if arg.startswith("-p"):
                module = arg[2:] if len(arg) > 2 else ""
                record["local_plugins"].append(module)
        tox = (doc.get("tool") or {}).get("tox") or {}
        if tox:
            record["tox"]["pyproject"] = tox

    # tox.ini
    toxfile = next((name for name in files if name.count("/") == 1
                    and name.endswith("tox.ini")), None)
    if toxfile:
        record["files_scanned"].append(toxfile)
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(files[toxfile].decode("utf-8", "replace"))
            for section in parser.sections():
                if section == "tox" or section.startswith("testenv"):
                    deps = parser.get(section, "deps", fallback="")
                    commands = parser.get(section, "commands", fallback="")
                    record["tox"][section] = {"deps": deps, "commands": commands}
                    for line in deps.splitlines():
                        text = line.strip()
                        if not text or text.startswith(("#", "!", "-c", "--constraint")):
                            continue
                        if text.startswith(("-r", "--requirement")):
                            record["declarations"].append(
                                {"raw": text, "source": toxfile, "channel": "tox_requirements_ref",
                                 "name": None})
                            continue
                        if text.startswith("{[") or text.startswith("{"):
                            continue
                        item = requirement_record(text, toxfile, None, "tox_deps")
                        if item:
                            record["declarations"].append(item)
        except Exception as exc:  # noqa: BLE001
            record["errors"].append(f"tox:{exc}")

    # pytest.ini
    pytest_ini = next((name for name in files if name.count("/") == 1
                       and name.endswith("pytest.ini")), None)
    if pytest_ini:
        record["files_scanned"].append(pytest_ini)
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(files[pytest_ini].decode("utf-8", "replace"))
            if parser.has_section("pytest"):
                cfg = dict(parser.items("pytest"))
                record["pytest_config"].update(cfg)
                for arg in str(cfg.get("addopts", "")).split():
                    if arg.startswith("-p") and len(arg) > 2:
                        record["local_plugins"].append(arg[2:])
                if cfg.get("required_plugins"):
                    for plugin in cfg["required_plugins"].replace("\n", " ").split():
                        name = plugin.split(">=")[0].split("==")[0].split("~=")[0].strip()
                        if name:
                            record["declarations"].append(
                                requirement_record(name, pytest_ini, None, "required_plugins"))
        except Exception as exc:  # noqa: BLE001
            record["errors"].append(f"pytest_ini:{exc}")

    # setup.cfg extras / pytest
    setup_cfg = next((name for name in files if name.count("/") == 1
                      and name.endswith("setup.cfg")), None)
    if setup_cfg:
        record["files_scanned"].append(setup_cfg)
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(files[setup_cfg].decode("utf-8", "replace"))
            if parser.has_section("options.extras_require"):
                for extra, value in parser.items("options.extras_require"):
                    for line in value.splitlines():
                        if line.strip():
                            item = requirement_record(line.strip(), setup_cfg, None,
                                                      "setup_cfg_extra", extra=extra)
                            record["declarations"].append(item)
                            record["extras"].setdefault(extra, []).append(item)
            if parser.has_section("tool:pytest"):
                record["pytest_config"].update(dict(parser.items("tool:pytest")))
        except Exception as exc:  # noqa: BLE001
            record["errors"].append(f"setup_cfg:{exc}")

    # requirements 文件（仅顶层与 tests/ 下的常见测试清单）
    for name in files:
        parts = name.split("/")
        if len(parts) > 3 or not name.endswith(".txt"):
            continue
        leaf = parts[-1]
        if leaf not in REQUIREMENT_LEAVES and "requirements" not in leaf:
            continue
        if len(parts) > 2 and parts[1] not in ("tests", "test"):
            continue
        record["files_scanned"].append(name)
        for i, raw in enumerate(files[name].decode("utf-8", "replace").splitlines(), 1):
            line = raw.split("#")[0].strip()
            if not line or line.startswith(("-r", "--requirement", "-c", "--constraint", "-e")):
                continue
            item = requirement_record(line, name, i, "requirements_file")
            if item:
                record["declarations"].append(item)

    # 插件：addopts 与配置键
    addopts = _as_list(record["pytest_config"].get("addopts"))
    plugins = set()
    for arg in addopts:
        for pattern, plugin in OPTION_PLUGINS:
            if pattern.search(arg):
                plugins.add(plugin)
    for key in CONFIG_PLUGINS:
        if key in record["pytest_config"]:
            plugins.add(CONFIG_PLUGINS[key])
    for plugin in record["pytest_config"].get("required_plugins") or []:
        name = str(plugin).split(">=")[0].split("==")[0].split("~=")[0].strip()
        if name:
            plugins.add(canonicalize_name(name))
    record["required_plugins"] = sorted(plugins)
    record["local_plugins"] = sorted(set(p for p in record["local_plugins"] if p))
    return record


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return value.split()
    return [str(item) for item in value]


def declared_specs(record, target):
    specs = []
    for item in record["declarations"]:
        if item.get("name") == target and item.get("specifier"):
            specs.append({"specifier": item["specifier"], "source": item["source"],
                          "channel": item["channel"], "extra": item.get("extra")})
    return specs


def combine(specs):
    combined = SpecifierSet()
    for spec in specs:
        try:
            combined &= SpecifierSet(spec["specifier"])
        except Exception:  # noqa: BLE001
            pass
    return combined


def local_artifacts():
    """既有本地产物索引：decoded name -> [(version, path)]。"""
    index = {}
    roots = [NEXT / "state" / "wheelhouse", NEXT / "cache" / "sdists",
             NEXT / "state" / "artifacts", ROOT / "extension" / "wheelhouse"]
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in (".whl", ".gz"):
                continue
            version = None
            if path.suffix == ".whl":
                parts = path.stem.split("-")
                if len(parts) >= 2:
                    version = parts[1]
                name = parts[0]
            else:
                stem = path.name
                for suffix in (".tar.gz", ".tgz"):
                    if stem.endswith(suffix):
                        stem = stem[: -len(suffix)]
                parts = stem.rsplit("-", 1)
                if len(parts) == 2:
                    name, version = parts
                else:
                    name = stem
            if not version:
                continue
            try:
                Version(version)
            except Exception:  # noqa: BLE001
                continue
            index.setdefault(canonicalize_name(name), []).append(
                {"version": version, "path": str(path)})
    for name in index:
        index[name].sort(key=lambda item: Version(item["version"]))
    return index


TEST_ONLY = ("test", "tests", "testing")
DEV_ONLY = ("dev", "develop", "development", "ci")


def scope_of(decl):
    channel = decl.get("channel")
    extra = (decl.get("extra") or "").lower()
    if channel in ("tox_deps", "tox_requirements_ref"):
        return "tox"
    if channel == "requirements_file":
        return "requirements"
    if channel == "required_plugins":
        return "plugin"
    if extra in TEST_ONLY:
        return "test"
    if extra in DEV_ONLY:
        return "dev"
    return "other"


def _expand_self_extra(text, project_name, optional):
    """把 ``本项目[extra]`` 形式的自引用展开为该 extra 的声明；仅一层。"""
    if not project_name:
        return None
    req = parse_requirement(text)
    if req is None or canonicalize_name(req.name) != canonicalize_name(project_name):
        return None
    if not req.extras:
        return None
    out = []
    for extra in sorted(req.extras):
        out.extend(optional.get(extra, []))
    return out


def artifact_pytest_requirement(index, sdist_index, name, version):
    """从既有 wheel 或 sdist 读取该分发包对 pytest 的直接约束。"""
    import zipfile
    candidates = []
    wheel = index.get(name, {}).get(version)
    if wheel:
        candidates.append(wheel)
    for item in sdist_index.get(name, []):
        if item["version"] == version and item["path"].endswith(".tar.gz"):
            candidates.append(item["path"])
    for path in candidates:
        try:
            if path.endswith(".whl"):
                with zipfile.ZipFile(path) as archive:
                    member = next(n for n in archive.namelist() if n.endswith("METADATA"))
                    text = archive.read(member).decode("utf-8", "replace")
            else:
                with tarfile.open(path) as tar:
                    member = next(m.name for m in tar.getmembers()
                                  if m.name.endswith("PKG-INFO"))
                    text = tar.extractfile(member).read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        for line in text.splitlines():
            if not line.startswith("Requires-Dist:"):
                continue
            body = line.split(":", 1)[1].strip()
            req = parse_requirement(body.split(";", 1)[0].strip())
            if req and canonicalize_name(req.name) == "pytest" and str(req.specifier):
                return {"specifier": str(req.specifier), "artifact": path}
    return None


def _satisfies(version, spec):
    if not spec:
        return True
    try:
        return SpecifierSet(spec).contains(Version(version), prereleases=False)
    except Exception:  # noqa: BLE001
        return False


def wheel_index():
    """既有本地 wheel 的 name -> {version: path}；只读索引，不安装。"""
    index = {}
    roots = [NEXT / "state" / "wheelhouse", ROOT / "extension" / "wheelhouse",
             ROOT / "admission_artifacts",
             ROOT.parent / "pycompat-env-select" / "stage2" / "wheelhouse"]
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.whl"):
            parts = path.name[:-4].split("-")
            if len(parts) < 2:
                continue
            try:
                version = Version(parts[1])
            except Exception:  # noqa: BLE001
                continue
            index.setdefault(canonicalize_name(parts[0]), {})[str(version)] = str(path)
    return index


def plugin_entry_points(index):
    """从既有 wheel 的 entry_points.txt 读取 pytest11 入口点名称。"""
    import zipfile
    mapping = {}
    for name, versions in index.items():
        for version, path in versions.items():
            try:
                with zipfile.ZipFile(path) as archive:
                    for member in archive.namelist():
                        if not member.endswith("entry_points.txt"):
                            continue
                        parser = configparser.ConfigParser()
                        parser.read_string(archive.read(member).decode("utf-8", "replace"))
                        if parser.has_section("pytest11"):
                            for entry_name, module in parser.items("pytest11"):
                                mapping.setdefault(name, {})[entry_name] = {
                                    "module": module, "wheel": path, "version": version}
            except Exception:  # noqa: BLE001
                continue
    return mapping


def main():
    request = json.loads(REQUEST.read_text(encoding="utf-8"))
    artifacts = local_artifacts()
    wheels = wheel_index()
    entry_points = plugin_entry_points(wheels)
    projects = []
    for item in request["source_artifacts"]:
        path = pathlib.Path(item["existing_path"])
        actual = {"sha256": sha(path), "bytes": path.stat().st_size}
        record = collect(path)
        pytest_specs = declared_specs(record, "pytest")
        pytest_combined = combine(pytest_specs)
        plugin_rows = []
        for plugin in record["required_plugins"]:
            specs = declared_specs(record, canonicalize_name(plugin))
            available = [a for a in artifacts.get(canonicalize_name(plugin), [])]
            plugin_rows.append({
                "plugin": plugin,
                "declared": bool(specs),
                "specifiers": specs,
                "local_artifacts": available[-5:],
            })
        # 声明的测试依赖：来自测试 extras / 测试依赖组 / tox deps / requirements。
        declared_test = []
        for decl in record["declarations"]:
            if decl.get("channel") == "metadata_extra" and decl.get("extra") in TEST_EXTRAS:
                declared_test.append(decl)
            elif decl.get("channel") == "project_extra" and decl.get("extra") in TEST_EXTRAS:
                declared_test.append(decl)
            elif decl.get("channel") == "dependency_group" and decl.get("extra") in TEST_GROUPS:
                declared_test.append(decl)
            elif decl.get("channel") == "self_extra_expansion" and decl.get("extra") in TEST_GROUPS:
                declared_test.append(decl)
            elif decl.get("channel") in ("tox_deps", "requirements_file", "required_plugins"):
                declared_test.append(decl)
            elif decl.get("channel") == "setup_cfg_extra" and decl.get("extra") in TEST_EXTRAS:
                declared_test.append(decl)
        test_names = sorted({d["name"] for d in declared_test if d.get("name")})
        # 每项声明测试依赖的本地可安装版本与建议精确钉。
        grouped = {}
        for decl in declared_test:
            if not decl.get("name"):
                continue
            decl = dict(decl)
            decl["scope"] = scope_of(decl)
            grouped.setdefault(decl["name"], []).append(decl)
        pin_table = []
        for name, decls in sorted(grouped.items()):
            specifiers = sorted({d.get("specifier") or "" for d in decls})
            versions = wheels.get(name, {})
            satisfying = [v for v in versions if all(_satisfies(v, s) for s in specifiers)]
            satisfying.sort(key=Version)
            sdists = [a["version"] for a in artifacts.get(name, [])
                      if a["path"].endswith(".tar.gz") and all(_satisfies(a["version"], s)
                                                              for s in specifiers)]
            sdists = sorted(set(sdists), key=Version)
            proposed = satisfying[-1] if satisfying else None
            pin_table.append({
                "name": name,
                "scopes": sorted({d["scope"] for d in decls}),
                "specifiers": specifiers,
                "sources": sorted({f"{d['source']}:{d.get('line')}" if d.get("line")
                                   else d["source"] for d in decls}),
                "local_wheel_versions": sorted(versions, key=Version),
                "satisfying_local_wheels": satisfying,
                "satisfying_local_sdists": sdists,
                "proposed_exact_pin": proposed,
                "status": "pinned_from_existing_wheel" if proposed else
                          ("source_artifact_only" if sdists else
                           ("local_wheels_do_not_satisfy" if versions else "no_local_artifact")),
            })
            chosen = proposed or (sdists[-1] if sdists else None)
            if chosen and name.startswith("pytest-"):
                pin_table[-1]["pytest_requirement"] = artifact_pytest_requirement(
                    wheels, artifacts, name, chosen)
                pin_table[-1]["pytest_requirement_version"] = chosen
        # 直接 pytest 声明与已钉插件自身对 pytest 的约束求交（一层）。
        effective = SpecifierSet(str(pytest_combined))
        for row in pin_table:
            requirement = row.get("pytest_requirement")
            if requirement and requirement.get("specifier"):
                try:
                    effective &= SpecifierSet(requirement["specifier"])
                except Exception:  # noqa: BLE001
                    pass
        effective_satisfies_72 = _satisfies("7.2.0", str(effective))
        pytest_combined = combine(pytest_specs)
        pytest_local = sorted(wheels.get("pytest", {}), key=Version)
        pytest_satisfying = [v for v in pytest_local
                             if _satisfies(v, str(pytest_combined))]
        # 分类：只描述静态可判定的事实。
        classification = []
        if not declared_test and not record["pytest_config"]:
            classification.append("no_test_declaration_in_sdist")
        if not effective_satisfies_72:
            classification.append("declared_pytest_excludes_7.2.0")
        test_scoped = [row for row in pin_table
                       if set(row["scopes"]) & {"test", "plugin", "requirements", "tox"}]
        if any(row["status"] != "pinned_from_existing_wheel" for row in test_scoped):
            classification.append("missing_local_artifacts")
        if record["local_plugins"]:
            classification.append("project_local_pytest_plugin")
        if item["project"] == "argparse":
            classification.append("backport_shadows_stdlib_argparse")
        projects.append({
            "project": item["project"],
            "version": item["version"],
            "sdist": {"filename": item["filename"], "requested_sha256": item["sha256"],
                      "requested_bytes": item["bytes"], "observed_sha256": actual["sha256"],
                      "observed_bytes": actual["bytes"], "match": actual["sha256"] == item["sha256"]
                      and actual["bytes"] == item["bytes"]},
            "requires_python": record["requires_python"],
            "files_scanned": sorted(set(record["files_scanned"])),
            "declared_test_dependencies": declared_test,
            "declared_test_names": test_names,
            "extras": {name: [d["raw"] for d in items] for name, items in record["extras"].items()},
            "dependency_groups": record["groups"],
            "group_includes": record["group_includes"],
            "self_extra_expansions": record.get("self_extra_expansions", []),
            "tox": record["tox"],
            "pytest_config": record["pytest_config"],
            "declared_pytest_specs": pytest_specs,
            "declared_pytest_specifier": str(pytest_combined) or None,
            "effective_pytest_constraint": str(effective) or None,
            "effective_pytest_satisfies_7_2_0": effective_satisfies_72,
            "pytest_local_wheels": pytest_local,
            "pytest_satisfying_local": pytest_satisfying,
            "proposed_pytest_pin": pytest_satisfying[-1] if pytest_satisfying else None,
            "pin_table": pin_table,
            "classification": classification,
            "required_plugins": plugin_rows,
            "plugin_entry_points": {
                row["plugin"]: entry_points.get(canonicalize_name(row["plugin"]), {})
                for row in plugin_rows
            },
            "local_plugins": record["local_plugins"],
            "errors": record["errors"],
        })

    document = {
        "schema_version": "pyhost-context/declared-toolchains/1.0",
        "generated_by": "ClawsGO Science Agent",
        "request": {"path": str(REQUEST.relative_to(ROOT)), "sha256": sha(REQUEST)},
        "scope": {"candidates": 9, "candidate_code_imported": False,
                  "resolver_executed": False, "network_calls": 0,
                  "installs": 0},
        "rules_used": [
            "只解析 sdist 内文件；不导入候选，不执行构建。",
            "测试依赖来源：安装元数据测试 extras、PEP 735 测试依赖组（含 include-group）、"
            "tox testenv deps、顶层/测试 requirements 文件、required_plugins。",
            "插件由 addopts 选项与配置键保守映射；未知选项不猜测。",
            "本地精确版本仅在既有本地产物中存在时列出，不联网补取。",
        ],
        "projects": projects,
    }
    OUT.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    for row in projects:
        print(json.dumps({
            "project": row["project"], "match": row["sdist"]["match"],
            "requires_python": row["requires_python"],
            "pytest": row["declared_pytest_specifier"],
            "test_names": len(row["declared_test_names"]),
            "groups": sorted(row["dependency_groups"]),
            "plugins": [p["plugin"] for p in row["required_plugins"]],
            "local_plugins": row["local_plugins"],
        }, ensure_ascii=False))
    print(json.dumps({"out": str(OUT), "sha256": sha(OUT)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
