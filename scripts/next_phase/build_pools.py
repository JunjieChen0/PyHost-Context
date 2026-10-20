"""ClawsGO Science Agent：按预注册规则构建两组候选池（只读静态检查）。

流程：

1. 从冻结 frame 构造全集 U（五类别、非 excluded、有 sdist、排除 E1—E5）；
2. 锁定参照池 R：U 按全局 rank 的前 16 项（不做机制筛选）；
3. 在 U \\ R 中按 rank 逐项静态检查，取前 32 个符合 R5 者组成机制池；
4. 每个候选写证据：项目自身读取链、直接依赖读取链、测试引用、家族与暴露记录。

不执行候选代码、不安装、不跑测试；只下载 sdist 字节并做 SHA-256 校验与 AST 解析。
"""
import ast
import hashlib
import html
import io
import json
import pathlib
import re
import sys
import tarfile
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
EXT = HERE.parent
ROOT = EXT.parent
FRAME = EXT / "frame" / "frame_20261015.json"
CACHE = HERE / "cache"
SDISTS = CACHE / "sdists"
SCANS = CACHE / "scans"
META = CACHE / "meta"
STATE = CACHE / "pools_state.json"
for d in (SDISTS, SCANS, META):
    d.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(HERE))
from scan_candidate import classify, scan_source  # noqa: E402

EXCLUDE = set()
for project in json.loads((EXT / "freeze" / "final_sample_20261015.json").read_text())["categories"].values():
    for row in project:
        EXCLUDE.add(re.sub(r"[-_.]+", "-", row["project"]).lower())
for name in ("termcolor", "click", "tqdm", "babel", "pytz", "humanize", "colorama",
             "more-itertools", "flask", "apache-arrow", "pyarrow", "rich"):
    EXCLUDE.add(re.sub(r"[-_.]+", "-", name).lower())

CATEGORIES = {"time_i18n", "dev_test_tools", "text_config", "cli", "data"}
INFRA = re.compile(r"^(pip|setuptools|wheel|pytest|coverage|tox|nox|virtualenv|"
                   r"hatchling|flit-core|poetry-core|mypy|ruff|black|isort|flake8)$")
TEST_EXTRAS = {"test", "tests", "testing", "dev", "develop", "development"}
MIRROR = "https://mirrors.ustc.edu.cn/pypi/packages/"
STUDY_KEYS = {"TZ", "LC_ALL", "LANG", "PYTHONHASHSEED", "NO_COLOR", "TERM", "COLUMNS", "LINES"}
PLATFORM_FILE = re.compile(r"(win32|windows|_nt\b|nt\.py|msvcrt)", re.I)
INDEX = "https://mirrors.aliyun.com/pypi/simple/{name}/"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def fetch(url, timeout=90):
    request = urllib.request.Request(url, headers={"User-Agent": "pyhost-fetch/1.0"})
    return urllib.request.urlopen(request, timeout=timeout).read()


def get_sdist(filename, url, expected_sha, expected_bytes):
    path = SDISTS / filename
    if path.is_file():
        data = path.read_bytes()
        if sha256(data) == expected_sha and len(data) == expected_bytes:
            return path
        path.unlink()
    mirror = url.replace("https://files.pythonhosted.org/packages/", MIRROR)
    last = None
    for candidate in (mirror, url):
        try:
            data = fetch(candidate)
        except Exception as exc:
            last = exc
            continue
        if sha256(data) != expected_sha or len(data) != expected_bytes:
            last = RuntimeError("hash_mismatch:" + candidate)
            continue
        path.write_bytes(data)
        return path
    raise RuntimeError(f"fetch_failed:{filename}:{last}")


def parse_requires(pkg_info):
    deps = []
    for line in pkg_info.splitlines():
        if not line.lower().startswith("requires-dist:"):
            continue
        value = line.split(":", 1)[1].strip()
        name, rest = re.match(r"([A-Za-z0-9_.-]+)(.*)", value).groups()
        marker = None
        if ";" in rest:
            rest, marker = rest.split(";", 1)
            marker = marker.strip()
        if marker and "extra" in marker:
            extras = re.findall(r"extra\s*==\s*['\"]([^'\"]+)['\"]", marker)
            if extras and not (set(e.lower() for e in extras) & TEST_EXTRAS):
                continue
        if marker and "python_version" in marker and "3.11" not in marker:
            continue
        spec = rest.strip().strip("()").strip()
        deps.append({"name": name, "spec": spec, "marker": marker})
    return deps


def resolve_version(name, spec):
    key = META / (re.sub(r"[^A-Za-z0-9_.-]", "_", name + "@" + spec) + ".json")
    if key.is_file():
        doc = json.loads(key.read_text())
        return doc["version"], doc["filename"], doc["sha256"], doc["url"]
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version
    index_name = name.replace("_", "-").replace(".", "-")
    page = fetch(INDEX.format(name=index_name)).decode("utf-8", "replace")
    links = re.findall(r'href="([^"]+)"[^>]*>([^<]+)</a>', page)
    best = None
    wanted = SpecifierSet(spec) if spec else SpecifierSet()
    for href, text in links:
        href = html.unescape(href)
        match = re.search(r"([A-Za-z0-9_.]+)-([0-9][^-]*?)\.tar\.gz", href)
        if not match:
            continue
        version = match.group(2)
        try:
            parsed = Version(version)
        except Exception:
            continue
        if parsed.is_prerelease or not wanted.contains(parsed):
            continue
        if best is None or parsed > best[0]:
            best = (parsed, href)
    if best is None:
        raise RuntimeError(f"no_release:{name}{spec}")
    from urllib.parse import urljoin
    href = urljoin(INDEX.format(name=index_name), best[1])
    data_hash = re.search(r"#sha256=([0-9a-f]{64})", href)
    filename = href.rsplit("/", 1)[-1].split("#")[0]
    doc = {"version": str(best[0]), "filename": filename,
           "sha256": data_hash.group(1) if data_hash else None, "url": href.split("#")[0]}
    key.write_text(json.dumps(doc, sort_keys=True))
    return doc["version"], doc["filename"], doc["sha256"], doc["url"]


def scan_dep(name, spec):
    version, filename, digest, url = resolve_version(name, spec)
    if not digest:
        return {"name": name, "version": version, "error": "no_fragment_hash"}
    path = SDISTS / filename
    if not path.is_file():
        mirror = url.replace("https://files.pythonhosted.org/packages/", MIRROR)
        try:
            data = fetch(mirror)
        except Exception:
            data = fetch(url)
        if sha256(data) != digest:
            return {"name": name, "version": version, "error": "hash_mismatch"}
        path.write_bytes(data)
    scan_path = SCANS / (filename + ".json")
    if scan_path.is_file():
        return json.loads(scan_path.read_text())
    with tarfile.open(path) as tar:
        members = []
        for member in tar.getmembers():
            if not member.isfile() or not member.name.endswith(".py"):
                continue
            if ".." in pathlib.PurePosixPath(member.name).parts:
                continue
            members.append((member.name, tar.extractfile(member).read()))
    hits = []
    for member, data in members:
        parts = pathlib.PurePosixPath(member).parts
        if any(p in ("tests", "test", "testing", "docs", "doc", "examples", "benchmarks") for p in parts[:-1]):
            continue
        if member.split("/")[-1].startswith("test_"):
            continue
        hits.extend(classify(scan_source(member, data)))
    result = {"name": name, "version": version, "sdist": filename,
              "reads": [h for h in hits if h["classification"] in
                        ("import_or_collection", "construction")]}
    scan_path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def project_scan(path):
    key = SCANS / (path.name + ".project.json")
    if key.is_file():
        return json.loads(key.read_text())
    with tarfile.open(path) as tar:
        members = []
        for member in tar.getmembers():
            if not member.isfile() or not member.name.endswith(".py"):
                continue
            if ".." in pathlib.PurePosixPath(member.name).parts:
                continue
            members.append((member.name, tar.extractfile(member).read()))
    runtime_hits, test_hits = [], []
    source_parts, test_parts = [], []
    pkg_info = ""
    for name, data in members:
        text = data.decode("utf-8", "replace")
        parts = pathlib.PurePosixPath(name).parts
        skip_tree = any(p in ("docs", "doc", "examples", "benchmarks", ".github") for p in parts[:-1])
        if skip_tree:
            continue
        is_test = any(p in ("tests", "test", "testing") for p in parts[:-1]) or \
            parts[-1].startswith("test_") or parts[-1].endswith("_test.py")
        hits = classify(scan_source(name, data))
        if is_test:
            test_hits.extend(hits)
            test_parts.append(text)
        else:
            runtime_hits.extend(hits)
            source_parts.append(text)
    result = {"sdist": path.name, "py_files": len(members),
              "runtime_hits": runtime_hits, "test_hits": test_hits,
              "source_blob": "\n".join(source_parts)[:400000],
              "tests_blob": "\n".join(test_parts)[:400000],
              "pkg_info": pkg_info}
    key.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def read_pkg_info(path):
    with tarfile.open(path) as tar:
        for member in tar.getmembers():
            if member.isfile() and member.name.count("/") == 1 and member.name.endswith("PKG-INFO"):
                return tar.extractfile(member).read().decode("utf-8", "replace")
    return ""


def frame_universe():
    frame = json.loads(FRAME.read_text())
    universe = []
    for item in frame["candidates"]:
        if item.get("category") not in CATEGORIES:
            continue
        if item.get("status") == "excluded" or not item.get("sdist"):
            continue
        if re.sub(r"[-_.]+", "-", item["project"]).lower() in EXCLUDE:
            continue
        universe.append(item)
    universe.sort(key=lambda x: (x["rank"], x["project"]))
    return universe


def exposure(project):
    """既往暴露：旧准入记录 / 是否曾只做 B0 / 是否有扰动结果。"""
    admitted = project in json.loads((EXT / "freeze" / "candidates.json").read_text())["candidates"]
    attempts = list((EXT / "freeze" / "attempts").glob(project + "*.json"))
    perturbed = any((EXT / "state" / "runs" / phase / project).is_dir()
                    for phase in ("exploration", "validation_7"))
    return {"in_extension_admission_records": admitted,
            "attempt_records": [p.name for p in attempts],
            "had_perturbation_results": perturbed,
            "label": ("prior_perturbation" if perturbed else
                      "admission_informed_b0_only" if admitted or attempts else "unseen")}


def candidate_evidence(item):
    sdist = item["sdist"]
    path = get_sdist(sdist["filename"], sdist["url"], sdist["sha256"], sdist["bytes"])
    project = project_scan(path)
    pkg = read_pkg_info(path) or project["pkg_info"]
    deps = parse_requires(pkg)

    # 测试正文（用于“测试引用该模块/依赖 API”的证据）
    tests_blob = project.get("tests_blob", "")

    chains = []
    seen = set()
    for hit in project["runtime_hits"]:
        if hit["classification"] not in ("import_or_collection", "construction"):
            continue
        if hit["key"] not in STUDY_KEYS:
            continue
        if PLATFORM_FILE.search(hit["file"]):
            continue
        ident = (hit["file"], hit["line"], hit["key"])
        if ident in seen:
            continue
        seen.add(ident)
        parts = [p for p in hit["file"].split("/")[1:]]
        if parts and parts[0] == "src":
            parts = parts[1:]
        if not parts:
            continue
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1][:-3]
        module = ".".join(parts)
        if not module or not tests_blob:
            continue
        pattern = re.compile(r"(?:from\s+%s\b)|(?:import\s+%s\b)|(?:%s\.)" %
                             (re.escape(module), re.escape(module), re.escape(module)))
        if pattern.search(tests_blob):
            family = ("project", module.split(".")[0], hit["key"])
            chains.append({"channel": "project_" + hit["classification"], "key": hit["key"],
                           "file": hit["file"], "line": hit["line"], "module": module,
                           "family": "|".join(family), "test_reference": True})

    dep_chains = []
    for dep in deps:
        if INFRA.match(dep["name"].lower()) or dep["name"].lower() == item["project"].lower():
            continue
        import_name = dep["name"].replace("-", "_").replace(".", "_").lower()
        candidate_text = project.get("source_blob", "") + "\n" + tests_blob
        used = ("import " + import_name) in candidate_text or ("from " + import_name) in candidate_text
        try:
            result = scan_dep(dep["name"], dep["spec"])
        except Exception as exc:
            dep_chains.append({"dep": dep["name"], "error": str(exc)[:120]})
            continue
        study_reads = [r for r in result.get("reads", []) if r["key"] in STUDY_KEYS
                       and not PLATFORM_FILE.search(r["file"])]
        if study_reads:
            read = study_reads[0]
            family = ("dependency", dep["name"].lower(), read["key"])
            dep_chains.append({"channel": "dependency_" + read["classification"],
                               "dep": dep["name"], "dep_version": result["version"],
                               "key": read["key"], "file": read["file"], "line": read["line"],
                               "family": "|".join(family),
                               "candidate_imports_dependency": used,
                               "qualified": used})
    qualified_chains = chains + [d for d in dep_chains if d.get("qualified")]
    return {"project": item["project"], "rank": item["rank"], "version": item["version"],
            "category": item["category"], "chains": chains,
            "dependency_chains": dep_chains,
            "qualified_chains": qualified_chains,
            "deps_scanned": len(deps)}


def main():
    universe = frame_universe()
    state = json.loads(STATE.read_text()) if STATE.exists() else {
        "universe": [x["project"] for x in universe],
        "reference": [x["project"] for x in universe[:16]],
        "scanned": {}, "mechanism": []}
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    ref = set(state["reference"])
    queue = [x for x in universe if x["project"] not in ref]
    done = 0
    for item in queue:
        name = item["project"]
        if name in state["scanned"]:
            continue
        if done >= limit or len(state["mechanism"]) >= 32:
            break
        try:
            evidence = candidate_evidence(item)
            state["scanned"][name] = evidence
            qualified = bool(evidence["qualified_chains"])
            if qualified:
                state["mechanism"].append({"slot": len(state["mechanism"]) + 1,
                                           "rank": item["rank"], "project": name,
                                           "version": item["version"],
                                           "category": item["category"],
                                           "chains": evidence["chains"][:3],
                                           "dependency_chains": evidence["dependency_chains"][:5]})
            print(name, "QUAL" if qualified else "-",
                  len(evidence["chains"]), len(evidence["dependency_chains"]))
        except Exception as exc:
            state["scanned"][name] = {"error": f"{type(exc).__name__}: {exc}"}
            print(name, "ERR", type(exc).__name__, str(exc)[:100])
        done += 1
        STATE.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True))
    STATE.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True))
    print("mechanism", len(state["mechanism"]), "scanned", len(state["scanned"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
