"""ClawsGO Science Agent：工具链静态准备包的自检。

核对：九份冻结 sdist 的请求哈希、pdm M1 原始承诺、声明表的九项覆盖与机械一致性、
附件的九项覆盖、提案中的不变量文字。不执行候选、不安装、不联网。

用法：``python3 verify_toolchain_preparation.py --package <解压目录>``
"""
import argparse
import hashlib
import json
import pathlib
import re
import sys

EXPECTED = {
    "ipython": ("ipython-9.17.1.tar.gz", "8919be8c27f20a6f4423145028063f6637b42a03ce57665bb12015ee1f073529", 4539289),
    "structlog": ("structlog-26.1.0.tar.gz", "f63a716cbd1b1291cf7661de7794b455acfa4c43c5bcf1630e6ad5ddc1adb3b7", 1459881),
    "rich-toolkit": ("rich_toolkit-0.20.5.tar.gz", "0c9e1c414ffb0720be26285d472e263d1e704b71d31a7e13274b9996db4969e1", 213207),
    "pylint": ("pylint-4.1.1.tar.gz", "47538540de0a563ff0b6cb781330944b0c9c1a130986ad1c183116ed37ec4538", 1737156),
    "twine": ("twine-7.0.0.tar.gz", "85cdb29c518efef867360ae4acd4b0dfd61c8654a22fca08e6f8539f05022177", 215032),
    "rich-click": ("rich_click-1.9.9.tar.gz", "324cba7513cd4187ee92b2eef21f071714e45be062458c8b157bd7e0c81103e3", 75866),
    "argparse": ("argparse-1.4.0.tar.gz", "62b089a55be1d8949cd2bc7e0df0bddb9e028faefc8c32038cc84862aefdd6e4", 70508),
    "hatch": ("hatch-1.18.1.tar.gz", "222fff78e197cf12a39db7efe5318ca403f2614db213416fd106a6c3260ef221", 5277846),
    "pdm": ("pdm-2.29.2.tar.gz", "bad9705e9482e7d877a53380ff25273b01e9c1d313ee6ce7b215a6562a9d8bdd", 2714556),
}


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=pathlib.Path, default=pathlib.Path(__file__).parent)
    args = parser.parse_args()
    root = args.package.resolve()
    checks = []

    def check(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    bad = []
    for project, (filename, expected, size) in EXPECTED.items():
        path = root / "sdists" / filename
        if not path.is_file() or path.stat().st_size != size or sha(path) != expected:
            bad.append(project)
    check("nine_frozen_sdists", not bad, {"mismatches": bad, "count": 9})

    m1 = root / "records/pdm/commitment-M1.json"
    if m1.is_file():
        doc = json.loads(m1.read_text(encoding="utf-8"))
        check("pdm_m1_original",
              doc.get("pre_run_commitment_hash")
              == "0bfa45739efca278c328c0a488ed5ffdcc8c8904952c9eb39823de2523e5240a",
              {"file_sha256": sha(m1),
               "internal_commitment": doc.get("pre_run_commitment_hash")})
    else:
        check("pdm_m1_original", False, {"reason": "missing"})

    declared = json.loads((root / "DECLARED_TOOLCHAINS_20261018.json").read_text(encoding="utf-8"))
    projects = declared["projects"]
    check("declared_nine", len(projects) == 9 and {p["project"] for p in projects} == set(EXPECTED),
          sorted(p["project"] for p in projects))
    check("declared_hashes_match",
          all(p["sdist"]["match"] for p in projects),
          [p["project"] for p in projects if not p["sdist"]["match"]])
    check("no_candidate_execution_flags",
          declared["scope"]["candidate_code_imported"] is False
          and declared["scope"]["resolver_executed"] is False
          and declared["scope"]["network_calls"] == 0
          and declared["scope"]["installs"] == 0, declared["scope"])
    required_fields = ("declared_test_dependencies", "declared_pytest_specifier",
                       "required_plugins", "pin_table", "classification")
    check("project_fields", all(all(key in p for key in required_fields) for p in projects), None)

    annex = (root / "TOOLCHAIN_PROPOSAL_ANNEX_20261018.md").read_text(encoding="utf-8")
    missing_annex = [project for project in EXPECTED if f"## {project} " not in annex]
    check("annex_covers_nine", not missing_annex, missing_annex)

    proposal = (root / "TOOLCHAIN_AMENDMENT_PROPOSAL_20261018.md").read_text(encoding="utf-8")
    invariants = ["CPython 3.11.9", "50 项", "60 秒", "240 秒", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
                  "不重判、不改写 0/9", "DRAFT"]
    check("proposal_invariants", all(term in proposal for term in invariants),
          [term for term in invariants if term not in proposal])
    check("proposal_keeps_scope",
          "S1b/S2/S3/repro" in proposal and "不构成执行授权" in proposal, None)

    errata = root / "ERRATA_ACKNOWLEDGED.md"
    check("errata_present", errata.is_file(), None)
    if errata.is_file():
        text = errata.read_text(encoding="utf-8")
        terms = ["hatch", "插件选项", "dependenc", "1513", "0/9", "2/16"]
        check("errata_terms", all(term in text for term in terms),
              [term for term in terms if term not in text])

    # 声明表中的每个测试作用域行都必须有来源与状态。
    row_bad = []
    for project in projects:
        for row in project["pin_table"]:
            if set(row["scopes"]) & {"test", "plugin", "requirements", "tox"}:
                if not row.get("sources") or not row.get("status"):
                    row_bad.append(f"{project['project']}:{row['name']}")
    check("pin_rows_have_sources", not row_bad, row_bad)

    document = {
        "schema_version": "pyhost-context/toolchain-preparation-verification/1.0",
        "package": str(root),
        "checks": checks,
        "all_ok": all(c["ok"] for c in checks),
        "candidate_tests_installs_network": 0,
    }
    (root / "TOOLCHAIN_PREPARATION_VERIFICATION.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(json.dumps({"all_ok": document["all_ok"],
                      "failed": [c for c in checks if not c["ok"]]},
                     ensure_ascii=False, indent=2))
    return 0 if document["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
