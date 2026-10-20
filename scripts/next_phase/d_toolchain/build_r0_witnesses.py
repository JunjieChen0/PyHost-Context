"""ClawsGO Science Agent：记录 R0/R1 决策的声明级见证。

R0 跳过必须有直接声明或 minversion 的排除；间接排除必须枚举允许范围内的
全部发行版本，并逐版本核对它们对 pytest 的约束。这里只读官方 PyPI 元数据，
不安装、不执行候选。
"""
import datetime
import hashlib
import json
import pathlib
import urllib.request

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
STATE = NEXT / "state" / "d_toolchain"
ROOTS = HERE / "D_ACQUISITION_ROOTS.json"
CACHE = STATE / "r0_witness"
OUT = NEXT / "D_R0_WITNESSES_20261018.json"
PIN = "7.2.0"
INDIRECT = {  # 项目 -> 迫使 pytest 下限的声明根
    "ipython": [("pytest-asyncio", ">=1.0.0")],
    "pdm": [("pytest-httpx", ">=0.34.0")],
}
UA = {"User-Agent": "ClawsGO-Science-Agent/1.0 (pyhost-context D preparation)"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def fetch(name, version):
    cache = CACHE / name / f"{version}.json"
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8")), "cache"
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=90) as response:
        raw = response.read()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(raw)
    return json.loads(raw), "network"


def project_versions(name):
    cache = CACHE / name / "_index.json"
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8"))
    request = urllib.request.Request(f"https://pypi.org/pypi/{name}/json", headers=UA)
    with urllib.request.urlopen(request, timeout=90) as response:
        raw = response.read()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(raw)
    return json.loads(raw)


def pytest_allows(requires_dist):
    out = []
    for raw in requires_dist or []:
        req = Requirement(raw)
        if req.name.lower().replace("_", "-") != "pytest":
            continue
        if req.marker and "extra" in str(req.marker):
            continue
        out.append((raw, str(req.specifier)))
    return out


def main():
    roots = json.loads(ROOTS.read_text(encoding="utf-8"))
    entries = []
    for project in roots["projects"]:
        name = project["project"]
        direct = [r["raw"] for r in project["requirements"]
                  if r["name"].lower().replace("_", "-") == "pytest"]
        minversion = project.get("pytest_minversion")
        direct_excludes = any(not Requirement(raw).specifier.contains(PIN)
                              for raw in direct)
        minversion_excludes = False
        if minversion:
            try:
                minversion_excludes = not SpecifierSet(f">={minversion}").contains(PIN)
            except Exception:  # noqa: BLE001
                minversion_excludes = False
        entry = {"project": name, "r0_pin": f"pytest=={PIN}",
                 "direct_pytest_roots": direct, "minversion": minversion,
                 "direct_exclusion": direct_excludes,
                 "minversion_exclusion": minversion_excludes,
                 "indirect_witness": None,
                 "decision": "r0_eligible"}
        if direct_excludes or minversion_excludes:
            entry["decision"] = "r0_excluded_direct_or_minversion"
        elif name in INDIRECT:
            witnesses = []
            all_exclude = True
            for package, spec in INDIRECT[name]:
                index = project_versions(package)
                versions = sorted(index["releases"], key=Version)
                allowed = [v for v in versions
                           if Requirement(f"{package}{spec}").specifier.contains(Version(v))]
                rows = []
                for version in allowed:
                    document, origin = fetch(package, version)
                    constraints = pytest_allows(document["info"].get("requires_dist"))
                    allows_pin = any(SpecifierSet(specifier).contains(PIN)
                                     for _, specifier in constraints) or not constraints
                    if allows_pin:
                        all_exclude = False
                    rows.append({"version": version, "origin": origin,
                                 "pytest_constraints": constraints,
                                 "allows_7_2_0": allows_pin})
                witnesses.append({"root": f"{package}{spec}",
                                  "versions_checked": rows})
            entry["indirect_witness"] = witnesses
            entry["decision"] = ("r0_excluded_indirect"
                                 if all_exclude else "r0_indeterminate")
        entries.append(entry)
    document = {"schema_version": "pyhost-context/d-r0-witnesses/1.0",
                "generated_at": now(),
                "roots_sha256": hashlib.sha256(ROOTS.read_bytes()).hexdigest(),
                "entries": entries}
    OUT.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(json.dumps({"out": str(OUT),
                      "sha256": hashlib.sha256(OUT.read_bytes()).hexdigest(),
                      "decisions": {e["project"]: e["decision"] for e in entries}},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
