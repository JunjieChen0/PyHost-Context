"""ClawsGO Science Agent：D 档八项的联合依赖解析（wheel-only，resolvelib）。

输入：Reviewer 001 的 `D_ACQUISITION_ROOTS.json`（有效根清单）、封存的本地
wheel 清单、官方 PyPI 元数据快照。输出每项的精确锁或具名缺口。

约束：
- 目标 CPython 3.11.9 / Linux x86_64；只解析 wheel 与其元数据；
- 不运行 sdist 构建后端、setup.py 或动态元数据代码；
- 网络只访问 pypi.org 的元数据接口与 files.pythonhosted.org 的登记文件地址；
- 不安装、不导入候选、不执行候选测试。

用法：
  python3 resolve_d_toolchains.py --project structlog
  python3 resolve_d_toolchains.py --all
"""
import argparse
import datetime
import faulthandler
import hashlib
import json
import pathlib
import sys
import time
import zipfile

from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version, InvalidVersion

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
ROOT = NEXT.parent
STATE = NEXT / "state" / "d_toolchain"
META = STATE / "meta"
LIB = STATE / "lib"
LOCAL = STATE / "D_LOCAL_WHEEL_INVENTORY.json"
ROOTS_FILE = HERE / "D_ACQUISITION_ROOTS.json"
OUT = STATE / "D_RESOLUTION_LOCKS.json"
LOG = STATE / "D_RESOLUTION_LOG.jsonl"

# resolvelib 从既有 pdm wheelhouse 的 wheel 中解出，仅用于库调用，不安装。
RESOLVELIB_WHEEL = "resolvelib-1.2.1-py3-none-any.whl"


def ensure_resolvelib():
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    try:
        import resolvelib  # noqa: F401
        return
    except ImportError:
        pass
    candidates = list((NEXT / "state/wheelhouse/pdm").glob(RESOLVELIB_WHEEL))
    if not candidates:
        candidates = list((ROOT / "wheelhouse").rglob(RESOLVELIB_WHEEL))
    if not candidates:
        raise SystemExit("resolvelib wheel not found in existing local wheelhouse")
    LIB.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(candidates[0]) as archive:
        archive.extractall(LIB)
    sys.path.insert(0, str(LIB))


ensure_resolvelib()
from resolvelib import (AbstractProvider, Resolver, BaseReporter, ResolutionImpossible,
                        ResolutionTooDeep)  # noqa: E402

TARGET = {
    "implementation_name": "cpython",
    "implementation_version": "3.11.9",
    "os_name": "posix",
    "platform_machine": "x86_64",
    "platform_python_implementation": "CPython",
    "platform_system": "Linux",
    "python_full_version": "3.11.9",
    "python_version": "3.11",
    "sys_platform": "linux",
    "extra": "",
}
PYTEST_PIN = "7.2.0"
RESOLVER_VERSION = "clawsgo-d-resolver/2.0 (resolvelib 1.2.1)"
POLICY = {
    "version_order": "descending within source class",
    "source_preference": "version descending; local wheel preferred over remote for the same version",
    "file_tiebreak": "filename lexicographic ascending",
    "pytest_stage_cap": 2,
    "r0_pin": f"pytest=={PYTEST_PIN}",
    "r1": "drop the common pytest pin only when R0 is unsatisfiable",
}
UA = "pyhost-context-dprep/1.0 (research; contact: clawsgo)"
REMOTE_INDEX = {}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def log(event):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at": now(), **event}, ensure_ascii=False) + "\n")


def fetch_json(url, retries=3, accept=None, timeout=45):
    import signal
    import urllib.request

    class FetchTimeout(Exception):
        pass

    def alarm(signum, frame):
        raise FetchTimeout("wall_clock_timeout")

    last = None
    for attempt in range(retries):
        previous = signal.signal(signal.SIGALRM, alarm)
        signal.alarm(timeout)
        try:
            headers = {"User-Agent": UA}
            if accept:
                headers["Accept"] = accept
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
            return json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
    raise RuntimeError(f"metadata_fetch_failed:{url}:{last}")


def pypi_version(name, version):
    cache = META / canonicalize_name(name) / f"{version}.json"
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8"))
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    document = fetch_json(url)
    if str(document.get("info", {}).get("version")) != str(version):
        raise RuntimeError(f"metadata_version_mismatch:{name}:{version}")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n",
                     encoding="utf-8")
    log({"event": "metadata_fetch", "name": name, "version": version, "url": url})
    time.sleep(0.1)
    return document


def wheel_compatible(tags):
    py = (tags.get("python_tag") or "").lower()
    abi = (tags.get("abi_tag") or "").lower()
    plat = (tags.get("platform_tag") or "").lower()
    if plat == "any":
        platform_ok = True
    else:
        platform_ok = plat == "linux_x86_64" or (plat.startswith("manylinux")
                                                 and plat.endswith("_x86_64"))
    if not platform_ok:
        return False
    if py in ("py3", "py2.py3"):
        return abi in ("none", "abi3")
    if py.startswith("cp"):
        digits = py[2:]
        try:
            if len(digits) == 2 and digits[0] == "3":
                minor = int(digits[1])
            elif len(digits) == 3 and digits[0] == "3":
                minor = int(digits[1:])
            else:
                return False
        except ValueError:
            return False
        if (3, minor) == (3, 11) and abi in ("cp311", "none"):
            return True
        if abi == "abi3" and (3, minor) <= (3, 11):
            return True
    return False


def parse_wheel_filename_tags(filename):
    parts = filename[:-4].split("-")
    if len(parts) < 5:
        return None
    return {"name": parts[0], "version": parts[1],
            "python_tag": parts[-3], "abi_tag": parts[-2], "platform_tag": parts[-1]}


def eval_marker(marker, extras=()):
    if not marker:
        return True
    text = str(marker)
    if "extra" in text:
        for extra in (extras or ("",)):
            env = dict(TARGET)
            env["extra"] = extra
            try:
                if Marker(text).evaluate(env):
                    return True
            except Exception:  # noqa: BLE001
                return True
        return False
    try:
        return Marker(text).evaluate(TARGET)
    except Exception:  # noqa: BLE001
        return True


class LocalIndex:
    def __init__(self, path):
        document = json.loads(path.read_text(encoding="utf-8"))
        self.by_name = {}
        for entry in document["entries"]:
            if not wheel_compatible(entry):
                continue
            self.by_name.setdefault(canonicalize_name(entry["wheel_name"]), []).append(entry)
        self.document = document

    def rows(self, name):
        return list(self.by_name.get(name, []))


def remote_rows(name):
    if name in REMOTE_INDEX:
        return REMOTE_INDEX[name]
    cache = META / canonicalize_name(name) / "_project.json"
    document = None
    if cache.is_file():
        try:
            document = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            document = None
    if document is None:
        simple_url = f"https://pypi.org/simple/{canonicalize_name(name)}/"
        try:
            document = fetch_json(simple_url,
                                  accept="application/vnd.pypi.simple.v1+json",
                                  timeout=90)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n",
                             encoding="utf-8")
            log({"event": "simple_index_fetch", "name": name,
                 "files": len(document.get("files") or [])})
        except Exception:  # noqa: BLE001
            log({"event": "simple_index_failed", "name": name})
            REMOTE_INDEX[name] = []
            return []
    rows = []
    for item in document.get("files") or []:
        filename = item.get("filename") or ""
        if not filename.endswith(".whl") or item.get("yanked"):
            continue
        tags = parse_wheel_filename_tags(filename)
        if not tags or not wheel_compatible(tags):
            continue
        try:
            Version(tags["version"])
        except InvalidVersion:
            continue
        rows.append({
            "filename": filename, "version": tags["version"],
            "sha256": (item.get("hashes") or {}).get("sha256"),
            "bytes": item.get("size"), "url": item.get("url"),
            "requires_python": item.get("requires-python"),
            "source": "pypi", **tags,
        })
    REMOTE_INDEX[name] = rows
    log({"event": "remote_index", "name": name, "wheels": len(rows)})
    time.sleep(0.1)
    return rows


class Meta:
    def __init__(self):
        self.cache = {}

    def get(self, name, version):
        key = (canonicalize_name(name), str(version))
        if key in self.cache:
            return self.cache[key]
        document = pypi_version(name, version)
        info = document.get("info") or {}
        entry = {"requires_python": info.get("requires_python"),
                 "requires_dist": info.get("requires_dist") or []}
        self.cache[key] = entry
        return entry


class Candidate:
    def __init__(self, row, extras):
        self.row = row
        self.name = canonicalize_name(row.get("wheel_name") or parse_wheel_filename_tags(
            row["filename"])["name"])
        self.version = row["version"]
        self.extras = tuple(sorted(extras))
        self.source = row.get("source") or "local"

    @property
    def key(self):
        return (self.name, self.version, self.extras)

    def __repr__(self):
        return f"{self.name}=={self.version}[{','.join(self.extras)}]/{self.source}"

    def __eq__(self, other):
        return isinstance(other, Candidate) and self.key == other.key

    def __hash__(self):
        return hash(self.key)


class Req:
    def __init__(self, requirement, parent="root"):
        self.requirement = requirement
        self.parent = parent

    def __repr__(self):
        return f"{self.requirement} <- {self.parent}"


class Provider(AbstractProvider):
    def __init__(self, project, local, meta):
        self.project = project
        self.local = local
        self.meta = meta
        self.rejections = []

    def identify(self, requirement_or_candidate):
        if isinstance(requirement_or_candidate, Candidate):
            return requirement_or_candidate.name
        return canonicalize_name(requirement_or_candidate.requirement.name)

    def get_preference(self, identifier, resolutions, candidates, information, backtrack_causes):
        # 先解决约束最多的包；同数按名称稳定排序。
        return (len(list(information.get(identifier, ()))), identifier)

    def find_matches(self, identifier, requirements, incompatibilities):
        reqs = list(requirements.get(identifier, ()))
        if not reqs:
            return []
        specifiers = [SpecifierSet(str(r.requirement.specifier))
                      for r in reqs if r.requirement.specifier]
        extras = set()
        for r in reqs:
            extras.update(canonicalize_name(e) for e in r.requirement.extras)
        blocked = {c.version for c in incompatibilities.get(identifier, ())}
        local = []
        for row in self.local.rows(identifier):
            if not self._sat(row["version"], specifiers):
                continue
            local.append(Candidate(row, extras))
        remote = []
        for row in remote_rows(identifier):
            if not self._sat(row["version"], specifiers):
                continue
            if row.get("requires_python"):
                try:
                    if not SpecifierSet(row["requires_python"]).contains(
                            Version("3.11.9")):
                        continue
                except Exception:  # noqa: BLE001
                    pass
            remote.append(Candidate(row, extras))
        combined = local + remote
        # 版本降序为主键；同版本本地字节优先；再按文件名字典序。
        combined.sort(key=lambda c: c.row["filename"])
        combined.sort(key=lambda c: (0 if c.source == "local" else 1))
        combined.sort(key=lambda c: Version(c.version), reverse=True)
        seen_versions = set()
        out = []
        for candidate in combined:
            if candidate.version in blocked:
                continue
            key = (candidate.version, candidate.extras)
            if key in seen_versions:
                continue
            seen_versions.add(key)
            out.append(candidate)
        if not out:
            self.rejections.append({"identifier": identifier,
                                    "specifiers": [str(s) for s in specifiers],
                                    "parents": [r.parent for r in reqs]})
        return out

    @staticmethod
    def _sat(version, specifiers):
        try:
            parsed = Version(version)
        except InvalidVersion:
            return False
        return all(s.contains(parsed) for s in specifiers)

    def is_satisfied_by(self, requirement, candidate):
        if canonicalize_name(requirement.requirement.name) != candidate.name:
            return False
        try:
            return requirement.requirement.specifier.contains(Version(candidate.version))
        except Exception:  # noqa: BLE001
            return False

    def get_dependencies(self, candidate):
        meta = self.meta.get(candidate.name, candidate.version)
        out = []
        for raw in meta.get("requires_dist") or []:
            req = Requirement(raw)
            if canonicalize_name(req.name) == candidate.name:
                continue
            if not eval_marker(req.marker, candidate.extras):
                continue
            out.append(Req(req, parent=f"{candidate.name}=={candidate.version}"))
        return out


class Reporter(BaseReporter):
    def __init__(self):
        self.events = []

    def adding_requirement(self, requirement, parent):
        self.events.append(("add", str(requirement), parent))

    def rejecting_candidate(self, criterion, candidate):
        self.events.append(("reject", criterion, str(candidate)))


def resolve_project(project, roots, stage, local, meta, max_rounds=5000):
    provider = Provider(project["project"], local, meta)
    reporter = Reporter()
    resolver = Resolver(provider, reporter)
    try:
        result = resolver.resolve([Req(r, "root") for r in roots], max_rounds=max_rounds)
    except ResolutionTooDeep:
        return {"ok": False, "stage": stage, "reason": "resolution_too_deep",
                "max_rounds": max_rounds, "rejections": provider.rejections[-20:]}
    except ResolutionImpossible as exc:
        causes = [{"requirement": str(getattr(c, "requirement", c)),
                   "parent": str(getattr(c, "parent", None))}
                  for c in exc.causes]
        return {"ok": False, "stage": stage, "reason": "resolution_impossible",
                "causes": causes, "rejections": provider.rejections[-20:],
                "events": reporter.events[-30:]}
    rows = []
    for name, candidate in sorted(result.mapping.items()):
        row = dict(candidate.row)
        row["name"] = candidate.name
        row["source"] = candidate.source
        if candidate.source == "local":
            row.setdefault("local_path", row.get("path"))
        row["requested_extras"] = list(candidate.extras)
        rows.append(row)
    return {"ok": True, "stage": stage, "rows": rows,
            "rejections": provider.rejections, "events": reporter.events}


def main():
    faulthandler.dump_traceback_later(300, repeat=True)
    parser = argparse.ArgumentParser()
    parser.add_argument("--project")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    roots_doc = json.loads(ROOTS_FILE.read_text(encoding="utf-8"))
    local = LocalIndex(LOCAL)
    meta = Meta()
    projects = roots_doc["projects"]
    if args.project:
        projects = [p for p in projects if p["project"] == args.project]
    results = []
    for project in projects:
        name = project["project"]
        if str(project.get("status", "")).startswith("static_excluded"):
            results.append({"project": name, "status": "static_excluded",
                            "reason": project["status"]})
            continue
        roots, rejected = [], []
        for item in project["requirements"]:
            req = Requirement(item["raw"])
            if not eval_marker(req.marker, ("",)):
                rejected.append({"raw": item["raw"], "reason": "marker_false_for_target"})
                continue
            if req.url:
                rejected.append({"raw": item["raw"], "reason": "url_requirement_not_authorized"})
                continue
            roots.append(req)
        direct_pytest = [r for r in roots if canonicalize_name(r.name) == "pytest"]
        minversion = project.get("pytest_minversion")
        r0_excluded = any(r.specifier and not r.specifier.contains(PYTEST_PIN)
                          for r in direct_pytest)
        if minversion:
            try:
                if not SpecifierSet(f">={minversion}").contains(PYTEST_PIN):
                    r0_excluded = True
            except Exception:  # noqa: BLE001
                pass
        stages = []
        outcome = None
        if not r0_excluded:
            stages.append("R0")
            outcome = resolve_project(project, roots + [Requirement(f"pytest=={PYTEST_PIN}")],
                                      "R0", local, meta, max_rounds=600)
            log({"event": "stage", "project": name, "stage": "R0",
                 "ok": outcome["ok"], "reason": outcome.get("reason")})
        if outcome is None or not outcome["ok"]:
            stages.append("R1")
            r0_error = None if outcome is None else {
                "reason": outcome.get("reason"), "causes": outcome.get("causes")}
            outcome = resolve_project(project, roots, "R1", local, meta, max_rounds=5000)
            outcome["r0_error"] = r0_error
            log({"event": "stage", "project": name, "stage": "R1",
                 "ok": outcome["ok"], "reason": outcome.get("reason")})
        if not outcome["ok"]:
            results.append({"project": name, "version": project["version"],
                            "status": "resolution_failed", "stages": stages,
                            "outcome": outcome, "rejected_roots": rejected,
                            "project_source_sha256": project["source_sha256"],
                            "pytest_version": None})
            continue
        pytest_row = next((r for r in outcome["rows"] if r["name"] == "pytest"), None)
        witnesses = []
        for row in outcome["rows"]:
            if not row["name"].startswith("pytest-"):
                continue
            try:
                dep_meta = meta.get(row["name"], row["version"])
            except Exception:  # noqa: BLE001
                continue
            for raw in dep_meta.get("requires_dist") or []:
                req = Requirement(raw)
                if canonicalize_name(req.name) != "pytest" or not req.specifier:
                    continue
                if not req.specifier.contains(PYTEST_PIN):
                    witnesses.append({"package": row["name"], "version": row["version"],
                                      "pytest_requirement": str(req.specifier)})
        results.append({
            "project": name, "version": project["version"],
            "project_source_sha256": project["source_sha256"],
            "status": "locked", "stages": stages, "r0_excluded": r0_excluded,
            "pytest_version": pytest_row["version"] if pytest_row else None,
            "r0_conflict_witnesses": witnesses,
            "lock": {"stage": outcome["stage"], "rows": outcome["rows"],
                     "rejections": outcome["rejections"]},
            "rejected_roots": rejected, "root_count": len(roots),
        })
    document = {
        "schema_version": "pyhost-context/d-resolution-locks/1.0",
        "generated_at": now(), "resolver_version": RESOLVER_VERSION,
        "policy": POLICY, "target": TARGET,
        "roots_sha256": sha_bytes(ROOTS_FILE.read_bytes()),
        "local_inventory_sha256": sha_bytes(LOCAL.read_bytes()),
        "projects": results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(json.dumps({"out": str(OUT), "sha256": sha_bytes(OUT.read_bytes()),
                      "projects": [{"project": r["project"], "status": r["status"],
                                    "stage": r.get("lock", {}).get("stage"),
                                    "packages": len(r.get("lock", {}).get("rows", [])),
                                    "pytest": r.get("pytest_version")}
                                   for r in results]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
