"""ClawsGO Science Agent：生成 S3 执行输入（静态物化，不执行候选）。

按 Reviewer 001 的 ``S3_COMPLETION_CHECKLIST.json`` 一次补齐四类输入：

* S3-INPUT-1：九个具名产品变异（三案例各三枚语义不同），逐文件登记补丁前后哈希；
* S3-INPUT-2：产品 × 套件组合树、逐行源码/工具链/上下文/argv/cwd/补丁绑定；
* S3-INPUT-3：见证探针、目标断言、预期阶段与失败签名、激活证据；
* S3-INPUT-4：240 行清单（供 ``s3_adapter.py`` 消费）与单一事前承诺。

本脚本只读冻结 sdist、既有派生树与已关闭的修复工件；不导入候选、不安装、不联网。
产物写入本目录 ``inputs/`` 与 ``S3_ROWS_20261019.json``。
"""
from __future__ import annotations

import ast
import hashlib
import json
import pathlib
import shutil
import sys
import tarfile
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import s3_common as common  # noqa: E402

# 在交付包内单独解包时没有上层工作区；只有 package_digest 与常量需要可用。
WS = HERE.parents[3] if len(HERE.parents) > 3 else HERE
EXT = WS / "pyhost-context/extension"
NEXT = EXT / "next_phase"
CASE_TABLES = NEXT / "CASE_TABLES_20261018_REV1.json"
DERIVED = NEXT / "state/derived"
REPAIR = EXT / "repair_artifacts"
SDISTS = {
    "rich-argparse": WS / "pyhost-context/admission_artifacts/rich-argparse/rich_argparse-1.8.0.tar.gz",
    "rich-rst": WS / "pyhost-context/admission_artifacts/rich-rst/rich_rst-2.2.0.tar.gz",
}
OUT = HERE / "inputs"
TREES = OUT / "trees"
SEED = "pyhost-context-S3-v1-20261019"

PRODUCTS = ["original", "M1", "M2", "M3"]
SUITES = ["S0_original", "R1_global_fix", "R2_local_isolation",
          "R3_explicit_parameterization"]

# R3 在测试内参数化目标测试，节点身份随之改变；按冻结套件源码登记参数 id。
# 这是**精确**映射：只允许 ``base[<id>]`` 或 ``base[...-<id>]``，不做无约束前缀匹配。
PARAMETERIZED_TARGETS = {
    ("A", "R3_explicit_parameterization"): ["no_color_absent", "no_color_set"],
    ("B", "R3_explicit_parameterization"): ["columns_40", "columns_100"],
    ("C", "R3_explicit_parameterization"): ["columns_unset", "columns_40"],
}
CONTEXTS = {
    "A": ["A-C1", "A-C2", "A-C3", "A-C4"],
    "B": ["B-C1", "B-C2", "B-C3", "B-C4"],
    "C": ["C-C1", "C-C2", "C-C3", "C-C4"],
}
CONTEXT_KEY = {"A": "NO_COLOR", "B": "COLUMNS", "C": "COLUMNS"}

# 已关闭工件中的套件测试侧来源；只取 tests/ 下的文件，产品文件一律来自派生树。
SUITE_SOURCES = {
    "A": {
        "R1_global_fix": "rp-k05-r1-orig/rp-k05-r1-orig.tar.gz",
        "R2_local_isolation": "rp-k05-r2-orig/rp-k05-r2-orig.tar.gz",
        "R3_explicit_parameterization": "rp-k05-r3-orig/rp-k05-r3-orig.tar.gz",
    },
    "B": {
        "R1_global_fix": "rp-k07-r1-orig/rp-k07-r1-orig.tar.gz",
        "R2_local_isolation": "rp-k07-r2-orig/rp-k07-r2-orig.tar.gz",
        "R3_explicit_parameterization": "rp-k07-r3-orig/rp-k07-r3-orig.tar.gz",
    },
    "C": {
        "R1_global_fix": "validation_richrst_k07/richrst_k07_r1/richrst_k07_r1.tar.gz",
        "R2_local_isolation": "validation_richrst_k07/richrst_k07_r2/richrst_k07_r2.tar.gz",
        "R3_explicit_parameterization": "validation_richrst_k07/richrst_k07_r3/richrst_k07_r3.tar.gz",
    },
}

# 每个修复套件对“有效上下文”的影响，以及**请求上下文的行为分支是否被执行**。
#
# 约定：
# * ``applicability`` 说明修复如何建立条件：``full``（保留启动上下文）、
#   ``local``（测试内显式建立）、``restricted``（会话级钉死到 Z）；
# * ``coverage`` 逐上下文声明“请求分支是否被实际执行”；它只描述行为覆盖，
#   不把 Z 钉死说成原上下文（``restricted`` 永远不计 RETAINED）。
SUITE_CONTEXT_EFFECT = {
    "A": {
        "S0_original": {
            "applicability": "full", "effective_context": "requested",
            "coverage": {ctx: {"covers": True, "reason": "未修改套件读取启动环境"}
                         for ctx in ["A-C1", "A-C2", "A-C3", "A-C4"]},
            "note": "未修改的项目测试套件"},
        "R1_global_fix": {
            "applicability": "restricted",
            "effective_context": "Z:NO_COLOR_absent_session_wide",
            "coverage": {
                "A-C1": {"covers": True, "reason": "Z 的 NO_COLOR 缺席与 C1 同支"},
                "A-C2": {"covers": False, "reason": "C2 的 NO_COLOR=1 分支被会话级移除"},
                "A-C3": {"covers": False, "reason": "空串分支不再出现（被移除而非保留为空串）"},
                "A-C4": {"covers": False, "reason": "C4 的非空值分支被会话级移除"},
            },
            "note": "会话级移除 NO_COLOR；请求的 C2/C3/C4 分支不再执行"},
        "R2_local_isolation": {
            "applicability": "local",
            "effective_context": "B_on(FORCE_COLOR=1,NO_COLOR_absent)",
            "coverage": {
                "A-C1": {"covers": True, "reason": "C1 为颜色开启分支，局部前置与之一致"},
                "A-C2": {"covers": False, "reason": "C2 的禁用分支被局部强制颜色开启覆盖"},
                "A-C3": {"covers": True, "reason": "C3 为空串仍属颜色开启分支"},
                "A-C4": {"covers": False, "reason": "C4 的禁用分支被局部强制颜色开启覆盖"},
            },
            "note": "force_color fixture 局部建立颜色开启前置；目标测试不再读取请求上下文的 NO_COLOR 值"},
        "R3_explicit_parameterization": {
            "applicability": "local",
            "effective_context": "branches{NO_COLOR absent,NO_COLOR=1}",
            "coverage": {
                "A-C1": {"covers": True, "reason": "参数化包含缺席"},
                "A-C2": {"covers": True, "reason": "参数化包含非空值分支（1）"},
                "A-C3": {"covers": False, "reason": "参数化不含空串分支"},
                "A-C4": {"covers": True, "reason": "参数化包含非空值分支（1 与 0 同支）"},
            },
            "note": "测试内参数化两种取值；请求的单个上下文值不再决定分支"},
    },
    "B": {
        "S0_original": {
            "applicability": "full", "effective_context": "requested",
            "coverage": {ctx: {"covers": True, "reason": "未修改套件读取启动环境"}
                         for ctx in ["B-C1", "B-C2", "B-C3", "B-C4"]},
            "note": "未修改的项目测试套件"},
        "R1_global_fix": {
            "applicability": "restricted",
            "effective_context": "Z:COLUMNS=80_at_collection",
            "coverage": {
                "B-C1": {"covers": True, "reason": "C1 与 Z 的有效宽度同为 78"},
                "B-C2": {"covers": False, "reason": "C2 的 COLUMNS=40 被收集前固定覆盖"},
                "B-C3": {"covers": True, "reason": "C3 请求 COLUMNS=80，与 Z 相同"},
                "B-C4": {"covers": False, "reason": "C4 的 COLUMNS=120 被收集前固定覆盖"},
            },
            "note": "收集前固定 COLUMNS=80（formatter.width=78）；请求上下文不再执行"},
        "R2_local_isolation": {
            "applicability": "local",
            "effective_context": "explicit width=78",
            "coverage": {
                "B-C1": {"covers": True, "reason": "C1 请求的有效宽度同为 78"},
                "B-C2": {"covers": False, "reason": "C2 请求宽度 38，未被显式 78 覆盖"},
                "B-C3": {"covers": True, "reason": "C3 请求宽度 78，与显式值相同"},
                "B-C4": {"covers": False, "reason": "C4 请求宽度 118，未被显式 78 覆盖"},
            },
            "note": "目标构造器显式传 width=78；不再读取 COLUMNS"},
        "R3_explicit_parameterization": {
            "applicability": "local",
            "effective_context": "explicit width=78 + COLUMNS{40,100}",
            "coverage": {
                "B-C1": {"covers": True, "reason": "显式 78 覆盖 C1 的有效宽度"},
                "B-C2": {"covers": True, "reason": "参数化 COLUMNS=40 并断言无显式宽度时 width=38"},
                "B-C3": {"covers": True, "reason": "显式 78 覆盖 C3 的有效宽度"},
                "B-C4": {"covers": False, "reason": "无 COLUMNS=120 参数"},
            },
            "note": "显式宽度覆盖环境，并参数化两种 COLUMNS 取值"},
    },
    "C": {
        "S0_original": {
            "applicability": "full", "effective_context": "requested",
            "coverage": {ctx: {"covers": True, "reason": "未修改套件读取启动环境"}
                         for ctx in ["C-C1", "C-C2", "C-C3", "C-C4"]},
            "note": "未修改的项目测试套件"},
        "R1_global_fix": {
            "applicability": "restricted",
            "effective_context": "Z:COLUMNS=80_session_wide",
            "coverage": {
                "C-C1": {"covers": True, "reason": "C1 未设时 Console 宽度同为 80"},
                "C-C2": {"covers": False, "reason": "C2 的 COLUMNS=40 被会话级覆盖"},
                "C-C3": {"covers": True, "reason": "C3 请求 COLUMNS=80，与 Z 相同"},
                "C-C4": {"covers": False, "reason": "C4 的 COLUMNS=120 被会话级覆盖"},
            },
            "note": "会话级固定 COLUMNS=80；请求上下文不再执行"},
        "R2_local_isolation": {
            "applicability": "local",
            "effective_context": "CLI --width 80",
            "coverage": {
                "C-C1": {"covers": True, "reason": "C1 未设时宽度同为 80"},
                "C-C2": {"covers": False, "reason": "C2 请求宽度 40，未被 --width 80 覆盖"},
                "C-C3": {"covers": True, "reason": "C3 请求宽度 80"},
                "C-C4": {"covers": False, "reason": "C4 请求宽度 120，未被 --width 80 覆盖"},
            },
            "note": "目标测试显式传 --width 80；不再读取 COLUMNS"},
        "R3_explicit_parameterization": {
            "applicability": "local",
            "effective_context": "COLUMNS{unset,40} + unwrapped text",
            "coverage": {
                "C-C1": {"covers": True, "reason": "参数化包含未设"},
                "C-C2": {"covers": True, "reason": "参数化包含 COLUMNS=40"},
                "C-C3": {"covers": False, "reason": "参数化不含 COLUMNS=80"},
                "C-C4": {"covers": False, "reason": "参数化不含 COLUMNS=120"},
            },
            "note": "测试内参数化两种 COLUMNS 取值，并对折行做可见文本折叠"},
    },
}

# 工具链：只安装运行测试所需的依赖；项目本身只从组合树导入。
TOOLCHAIN_PROJECT = {"A": "rich-argparse", "B": "rich-argparse", "C": "rich-rst"}
RUNTIME_REQUIREMENTS = {
    "rich-argparse": ["pytest", "pluggy", "iniconfig", "packaging", "attrs",
                      "rich", "markdown-it-py", "mdurl", "pygments"],
    "rich-rst": ["pytest", "pluggy", "iniconfig", "packaging", "attrs",
                 "rich", "markdown-it-py", "mdurl", "pygments"],
}
OBSERVER_FILES = {
    "jsonl_producer.py": WS / "pyhost-context/tooling/admission_runner/integration/jsonl_producer.py",
    "pytest_jsonl.py": WS / "pyhost-context/tooling/admission_runner/pytest_jsonl.py",
    "sitecustomize.py": WS / "pyhost-context/matrix/tooling/matrix_runner/netwatch_sitecustomize.py",
}


def read_json(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def write_json(path, payload):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def canonical_sha(payload) -> str:
    return common.sha256_bytes(json.dumps(payload, sort_keys=True,
                                          ensure_ascii=False).encode("utf-8"))


def extract_sdist(project, target):
    sdist = SDISTS[project]
    with tarfile.open(sdist) as tar:
        members = [m for m in tar.getmembers() if m.isfile()
                   and ".." not in pathlib.PurePosixPath(m.name).parts]
        top = members[0].name.split("/")[0]
        for member in members:
            rel = pathlib.PurePosixPath(member.name).parts[1:]
            path = target.joinpath(*rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(tar.extractfile(member).read())
    return top


def apply_hunks(root, hunks):
    records = []
    for hunk in sorted(hunks, key=lambda h: h["line"], reverse=True):
        path = root / hunk["file"]
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        index = hunk["line"] - 1
        before = lines[index].rstrip("\n")
        if before != hunk["before"]:
            raise AssertionError(f"anchor_mismatch:{path}:{hunk['line']}")
        indent = before[:len(before) - len(before.lstrip())]
        lines[index] = indent + hunk["after"] + "\n"
        path.write_text("".join(lines), encoding="utf-8")
        records.append({"file": hunk["file"], "line": hunk["line"],
                        "before": hunk["before"], "after": hunk["after"],
                        "before_sha256": common.sha256_bytes(hunk["before"].encode()),
                        "after_sha256": common.sha256_bytes(hunk["after"].encode())})
    return records


def build_products(table, staging):
    """从冻结 sdist 重建九枚产品变异，逐文件核对声明的前像/后像哈希。"""
    products = {}
    for case in table["cases"]:
        case_id = case["case_id"]
        base = staging / "products" / case_id / "original"
        if base.exists():
            shutil.rmtree(base)
        extract_sdist(case["project"], base)
        entry = {"case": case_id, "project": case["project"],
                 "version": case["version"], "sdist": str(SDISTS[case["project"]].relative_to(WS)),
                 "sdist_sha256": common.sha256_file(SDISTS[case["project"]]),
                 "source_dir": base.name, "variants": {}}
        entry["variants"]["original"] = {
            "derived_dir": "original", "tree_sha256": common.tree_hash(base),
            "patches": [], "mutation_id": None, "semantic_change": "冻结 sdist 原样",
            "suite_detectable": None, "target_nodes": [], "affected_nodes": [],
            "target_assertions": [], "activation": {}, "applicable_contexts": {},
            "obligation": "无变异；作为 S0 健康参照",
            "patch_sha256": canonical_sha([])}
        for mutation in case["mutations"]:
            target = staging / "products" / case_id / mutation["id"]
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(base, target)
            records = apply_hunks(target, mutation["patch"]["hunks"])
            for rel, declared in mutation["patch"]["derived_files"].items():
                observed = common.sha256_file(target / rel)
                if observed != declared["after_sha256"]:
                    raise AssertionError(
                        f"derived_hash_mismatch:{case_id}:{mutation['id']}:{rel}:{observed}")
            variant = mutation["id"].split("-", 1)[1]
            entry["variants"][variant] = {
                "derived_dir": mutation["id"],
                "tree_sha256": common.tree_hash(target),
                "patches": records,
                "mutation_id": mutation["id"],
                "semantic_change": mutation["semantic_change"],
                "suite_detectable": mutation.get("suite_detectable"),
                "target_nodes": mutation.get("target_nodes", []),
                "affected_nodes": mutation.get("affected_nodes", []),
                "target_assertions": mutation.get("target_assertions", []),
                "activation": mutation.get("activation", {}),
                "applicable_contexts": mutation.get("applicable_contexts", {}),
                "witness": mutation.get("witness", {}),
                "obligation": mutation.get("semantic_change"),
                "patch_sha256": canonical_sha({"hunks": mutation["patch"]["hunks"],
                                               "derived_files": mutation["patch"]["derived_files"]}),
            }
        products[case_id] = entry
    return products


def suite_test_files(blob_path):
    """从套件 tarball 读取 tests/ 下的文件（只读内存，不落盘）。"""
    files = {}
    with tarfile.open(blob_path) as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            parts = pathlib.PurePosixPath(member.name).parts
            if len(parts) >= 3 and parts[1] == "tests":
                rel = "/".join(parts[1:])
                files[rel] = tar.extractfile(member).read()
    return files


def build_suites(table, staging):
    """登记每个修复套件的测试侧补丁；基线取冻结 sdist 的 tests/。"""
    suites = {}
    for case in table["cases"]:
        case_id = case["case_id"]
        base_tests = staging / "products" / case_id / "original" / "tests"
        base = {"tests/" + p.relative_to(base_tests).as_posix(): p.read_bytes()
                for p in base_tests.rglob("*") if p.is_file()}
        entry = {"S0_original": {"description": "未修改的项目测试套件",
                                 "changed_files": [], "patch_sha256": canonical_sha([]),
                                 "context_effect": SUITE_CONTEXT_EFFECT[case_id]["S0_original"],
                                 "target_parameterization": {"mode": "exact",
                                                             "parameter_ids": [],
                                                             "note": "目标节点身份保持原 nodeid"}}}
        for suite, relative in SUITE_SOURCES[case_id].items():
            artifact = REPAIR / relative
            files = suite_test_files(artifact)
            changed = []
            for rel, data in sorted(files.items()):
                before = base.get(rel)
                if before == data:
                    continue
                changed.append({
                    "path": rel,
                    "before_sha256": common.sha256_bytes(before) if before is not None else None,
                    "after_sha256": common.sha256_bytes(data),
                    "bytes_before": len(before) if before is not None else None,
                    "bytes_after": len(data),
                })
            entry[suite] = {
                "description": SUITE_CONTEXT_EFFECT[case_id][suite]["note"],
                "changed_files": changed,
                "source_artifact": str(artifact.relative_to(WS)),
                "source_artifact_sha256": common.sha256_file(artifact),
                "context_effect": SUITE_CONTEXT_EFFECT[case_id][suite],
                "target_parameterization": {
                    "mode": ("parameterized"
                             if (case_id, suite) in PARAMETERIZED_TARGETS else "exact"),
                    "parameter_ids": PARAMETERIZED_TARGETS.get((case_id, suite), []),
                    "note": ("R3 在测试内参数化目标测试；子节点身份以 base[...-<id>] 精确匹配"
                             if (case_id, suite) in PARAMETERIZED_TARGETS
                             else "目标节点身份保持原 nodeid"),
                },
                "patch_sha256": canonical_sha(
                    {"source_artifact_sha256": common.sha256_file(artifact),
                     "changed_files": changed}),
                "file_bytes": files,
            }
        suites[case_id] = entry
    return suites


def compose(product_dir, suite_files, target):
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(product_dir, target)
    for rel, data in suite_files.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def build_trees(products, suites, staging):
    manifest = {}
    for case_id, product in products.items():
        for variant, info in product["variants"].items():
            for suite in SUITES:
                key = f"{case_id}/{variant}__{suite}"
                product_dir = staging / "products" / case_id / info["derived_dir"]
                target = staging / "compose" / key.replace("/", "__")
                files = suites[case_id][suite].get("file_bytes", {})
                compose(product_dir, files, target)
                blob = common.pack_tree(target, f"{case_id}-{variant}-{suite}")
                out = TREES / f"{key}.tar.gz"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(blob)
                manifest[key] = {
                    "path": str(out.relative_to(HERE)),
                    "tar_sha256": common.sha256_bytes(blob),
                    "bytes": len(blob),
                    "tree_sha256": common.tree_hash(target),
                    "product": f"{case_id}/{variant}",
                    "suite": suite,
                }
                assertions = {}
                for info in products[case_id]["variants"].values():
                    mutation_id = info["mutation_id"]
                    if not mutation_id:
                        continue
                    assertions[mutation_id] = resolve_assertions(
                        target, mutation_id, info["target_nodes"],
                        info["target_assertions"])
                manifest[key]["assertions"] = assertions
    return manifest


def row_plan():
    """按提交种子重建 240 行标签与顺序（与复核方草稿逐行一致）。"""
    suites, witnesses = [], []
    for case in ("A", "B", "C"):
        for context in CONTEXTS[case]:
            for product in PRODUCTS:
                for suite in SUITES:
                    label = f"{case}|{context}|{product}|{suite}|r1"
                    suites.append({"row_key": label, "case": case, "context": context,
                                   "product_variant": product, "suite_variant": suite,
                                   "repeat": 1, "call_kind": "suite",
                                   "label_sha256": common.sha256_bytes(
                                       f"{SEED}|{label}".encode())})
                label = f"{case}|{context}|{product}|witness|r1"
                witnesses.append({"row_key": label, "case": case, "context": context,
                                  "product_variant": product, "repeat": 1,
                                  "call_kind": "witness",
                                  "label_sha256": common.sha256_bytes(
                                      f"{SEED}|{label}".encode())})
    for group in (suites, witnesses):
        group.sort(key=lambda r: r["label_sha256"])
        for index, row in enumerate(group, 1):
            row["order"] = index
    return suites, witnesses


def expand_identities(case, base_nodes, parameter_ids):
    """参数化套件的完整子节点身份；B 的 formatter 参数在前，只登记后缀规则。"""
    if not parameter_ids or case == "B":
        return []
    children = []
    for base in base_nodes:
        for parameter_id in parameter_ids:
            if base.endswith("]"):
                children.append(base[:-1] + "-" + parameter_id + "]")
            else:
                children.append(base + "[" + parameter_id + "]")
    return sorted(children)


# 断言选择器：按冻结组合树里的实际断言重新定位，不沿用原产品行号。
ASSERTION_SELECTORS = {
    "A-M1": ["format_help", "format_usage", "help_text", "rich_help"],
    "A-M2": ["format_help", "format_usage", "help_text", "rich_help"],
    "A-M3": ["format_help", "format_usage", "help_text", "rich_help"],
    "B-M1": ["format_help"],
    "B-M2": ["format_help"],
    "B-M3": ["output == expected"],
    "C-M1": ["Error parsing reStructuredText"],
    "C-M2": [],
    "C-M3": ["Fake parse failure"],
}
# 目标函数本身没有 assert、由 helpers 辅助方法断言的案例。
ASSERTION_HELPERS = {
    "B-M3": {"file": "tests/helpers.py", "function": "assert_format_help_equal"},
}


def _function_asserts(source: str, function: str, selectors: list[str]):
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assert):
                    segment = ast.get_source_segment(source, sub) or ""
                    if not selectors or any(selector in segment for selector in selectors):
                        found.append((sub.lineno, segment.splitlines()[0].strip()[:160]))
    return sorted(found)


def _helper_asserts(root: pathlib.Path, helper: dict, selectors: list[str]):
    path = root / helper["file"]
    if not path.exists():
        return []
    return _function_asserts(path.read_text(encoding="utf-8"),
                             helper["function"], selectors)


def resolve_assertions(root, mutation_id: str, target_nodes: list[str],
                       target_assertions: list[dict]) -> dict:
    """从组合树读取每个目标函数的实际断言位置。"""
    selectors = ASSERTION_SELECTORS.get(mutation_id, [])
    locations = []
    by_node = {}
    seen = set()
    targets = []
    for node in target_nodes:
        targets.append((node, node.split("::")[0],
                        node.split("::")[1].split("[")[0]))
    for declared in target_assertions:
        # M2 类变异没有目标节点，断言只以声明形式登记；
        # 行号仍须按本组合树重新解析，不能沿用原产品行号。
        key = declared["file"] + "::" + declared["function"]
        if not any(file == declared["file"] and function == declared["function"]
                   for _, file, function in targets):
            targets.append((key, declared["file"], declared["function"]))
    for node, file, function in targets:
        path = pathlib.Path(root) / file
        if not path.exists():
            by_node[node] = {"file": file, "function": function, "lines": [],
                             "missing": "file_not_found"}
            continue
        found = _function_asserts(path.read_text(encoding="utf-8"), function, selectors)
        if not found and mutation_id in ASSERTION_HELPERS:
            found = _helper_asserts(pathlib.Path(root), ASSERTION_HELPERS[mutation_id],
                                    selectors)
            helper_file = ASSERTION_HELPERS[mutation_id]["file"]
            helper_function = ASSERTION_HELPERS[mutation_id]["function"]
            entry = {"file": helper_file, "function": helper_function,
                     "lines": [line for line, _ in found],
                     "sources": [source for _, source in found],
                     "resolved_via": "helper"}
        else:
            entry = {"file": file, "function": function,
                     "lines": [line for line, _ in found],
                     "sources": [source for _, source in found]}
        by_node[node] = entry
        key = (entry["file"], entry["function"], tuple(entry["lines"]))
        if key not in seen and entry["lines"]:
            seen.add(key)
            locations.append({k: entry[k] for k in ("file", "function", "lines", "sources")
                              if k in entry})
            if "resolved_via" in entry:
                locations[-1]["resolved_via"] = entry["resolved_via"]
    return {"locations": locations, "by_node": by_node}


def context_three_state(table, case_id, context_id):
    for case in table["cases"]:
        if case["case_id"] != case_id:
            continue
        for context in case["contexts"]:
            if context["id"] == context_id:
                return context["environment"]
    raise KeyError(f"context_missing:{case_id}:{context_id}")


def toolchains(table):
    result = {}
    for case in table["cases"]:
        project = case["project"]
        wheel_dir = EXT / "wheelhouse" / project
        wheels = []
        for wheel in sorted(wheel_dir.glob("*.whl")):
            name = wheel.name.split("-", 1)[0].lower().replace("_", "-")
            if name in RUNTIME_REQUIREMENTS[project]:
                wheels.append({"filename": wheel.name,
                               "path": str(wheel.relative_to(WS)),
                               "sha256": common.sha256_file(wheel),
                               "bytes": wheel.stat().st_size})
        missing = sorted(set(RUNTIME_REQUIREMENTS[project])
                         - {w["filename"].split("-", 1)[0].lower().replace("_", "-")
                            for w in wheels})
        if missing:
            raise AssertionError(f"wheel_missing:{project}:{missing}")
        observer = {}
        for name, path in OBSERVER_FILES.items():
            observer[name] = {"path": str(path.relative_to(WS)),
                              "sha256": common.sha256_file(path),
                              "bytes": path.stat().st_size}
        result[project] = {
            "project": project,
            "project_version": case["version"],
            "python": {"version": "3.11.9", "abi": "cp311"},
            "install": {"mode": "file-pinned offline install",
                        "installer": "uv pip install --offline --no-index",
                        "project_wheel_excluded": True,
                        "reason": "产品代码只从组合树导入，避免 site-packages 中的同名已发布包遮蔽"},
            "wheels": wheels,
            "observer": observer,
            "pytest": "7.2.0",
            "pytest_argv": ["-m", "pytest", "-p", "jsonl_producer",
                            "-p", "no:cacheprovider"],
            "jsonl_env": "PYHOST_JSONL_PATH",
        }
    return result


def witness_spec(table):
    """见证规格：探针、每个上下文的预期原始观测、判定阈值。"""
    spec = {}
    for case in table["cases"]:
        case_id = case["case_id"]
        mutations = {m["id"]: m for m in case["mutations"]}
        spec[case_id] = {
            "case": case_id,
            "project": case["project"],
            "version": case["version"],
            "reading_layer": case.get("reading_layer"),
            "reading_chain": case.get("reading_chain", []),
            "obligations": case.get("obligations", []),
            "controls": case.get("controls", {}),
            "probes": {
                "A": [{"name": "colour", "script": "witness_probes.py",
                       "observes": ["colour_present", "argparse_args_sgr", "sgr_sequence"]}],
                "B": [{"name": "width", "script": "witness_probes.py",
                       "observes": ["formatter_width_default", "formatter_width_explicit_70",
                                    "argparse_width_explicit_70",
                                    "max_help_line_width_default",
                                    "max_help_line_width_explicit_70",
                                    "argparse_max_line_width_explicit_70"]}],
                "C": [{"name": "panel", "script": "witness_probes.py",
                       "observes": ["exit_code", "max_line_width", "unwrapped",
                                    "repeat_stable"]}],
            }[case_id],
            "extra_environment": {"A": {"FORCE_COLOR": "1"}}.get(case_id, {}),
            "expected_original": {},
            "mutation_checks": {},
        }
        for context in CONTEXTS[case_id]:
            env = context_three_state(table, case_id, context)
            state = env["state"]
            value = env.get("value")
            if case_id == "A":
                colour_expected = state != "set" or value == ""
                expected = {
                    "colour_present": colour_expected,
                    "argparse_args_sgr_cyan": colour_expected,
                    "colour_role_observable": colour_expected,
                }
            elif case_id == "B":
                columns = 80 if state == "unset" else int(value)
                expected = {
                    "formatter_width_default": columns - 2,
                    "formatter_width_explicit_70": 70,
                    "argparse_width_explicit_70": 70,
                    "max_help_line_width_default": columns - 2,
                    "max_help_line_width_explicit_70": 70,
                    "argparse_max_line_width_explicit_70": 70,
                }
            else:
                width = 80 if state == "unset" else int(value)
                expected = {
                    "max_line_width": width,
                    "category_phrase_present": True,
                    "exception_detail_present": True,
                }
            spec[case_id]["expected_original"][context] = {
                **expected, "context_environment": env}
        for mutation_id, mutation in mutations.items():
            variant = mutation_id.split("-", 1)[1]
            spec[case_id]["mutation_checks"][variant] = {
                "mutation_id": mutation_id,
                "semantic_change": mutation["semantic_change"],
                "suite_detectable": mutation.get("suite_detectable"),
                "target_nodes": mutation.get("target_nodes", []),
                "target_assertions": mutation.get("target_assertions", []),
                "witness": mutation.get("witness", {}),
                "activation": mutation.get("activation", {}),
                "applicable_contexts": mutation.get("applicable_contexts", {}),
                "check": {
                    "A": {
                        "M1": "colour_role_is_magenta_in_colour_observable_contexts",
                        "M2": "colour_present_when_NO_COLOR_nonempty",
                        "M3": "colour_absent_when_NO_COLOR_empty",
                    },
                    "B": {
                        "M1": "default_width_differs_from_COLUMNS_minus_2",
                        "M2": "declared_witness_does_not_claim_detection_record_explicit_width_diagnostic",
                        "M3": "explicit_width_70_not_respected",
                    },
                    "C": {
                        "M1": "category_phrase_absent",
                        "M2": "panel_width_differs_from_COLUMNS_or_80",
                        "M3": "exception_detail_absent",
                    },
                }[case_id][variant],
            }
    return spec


def build_rows(table, products, suites, trees, tools, witnesses):
    rows, witness_rows = row_plan()
    expected_total = 240
    if len(rows) + len(witness_rows) != expected_total:
        raise AssertionError("row_count")
    suite_rows = []
    for row in rows:
        case, context = row["case"], row["context"]
        variant, suite = row["product_variant"], row["suite_variant"]
        key = f"{case}/{variant}__{suite}"
        tree = trees[key]
        product = products[case]["variants"][variant]
        mutation = product["mutation_id"]
        context_env = context_three_state(table, case, context)
        effect = suites[case][suite]["context_effect"]
        coverage = effect["coverage"][context]
        suite_rows.append({
            **row,
            "project": products[case]["project"],
            "project_version": products[case]["version"],
            "condition": next(c["condition"] for c in table["cases"] if c["case_id"] == case),
            "context_key": CONTEXT_KEY[case],
            "context_environment": context_env,
            "requested_context": context,
            "effective_context": effect["effective_context"],
            "applicability": effect["applicability"],
            "covers_requested_branch": coverage["covers"],
            "coverage_reason": coverage["reason"],
            "tree": {"path": tree["path"], "tar_sha256": tree["tar_sha256"],
                     "bytes": tree["bytes"], "tree_sha256": tree["tree_sha256"]},
            "product_patch_sha256": product["patch_sha256"],
            "product_patches": product["patches"],
            "suite_patch_sha256": suites[case][suite]["patch_sha256"],
            "suite_changed_files": suites[case][suite]["changed_files"],
            "mutation_id": mutation,
            "toolchain": {"project": products[case]["project"],
                          "python": "3.11.9", "pytest": "7.2.0"},
            "argv_template": (["{venv_python}", "-m", "pytest", "-p", "jsonl_producer",
                               "-p", "no:cacheprovider", "tests"]),
            "cwd": "{run_dir}/src",
            "expected": {
                "target_nodes": product["target_nodes"],
                "target_assertions": product["target_assertions"],
                "suite_detectable": product["suite_detectable"],
                "applicable_contexts": product["applicable_contexts"],
                "activation": product["activation"],
                "witness_declaration": product.get("witness", {}),
                "expected_phase": "call",
                "identity": {
                    "mode": ("parameterized"
                             if (case, suite) in PARAMETERIZED_TARGETS else "exact"),
                    "base_nodes": product["target_nodes"],
                    "parameter_ids": PARAMETERIZED_TARGETS.get((case, suite), []),
                    "expected_child_identities": expand_identities(
                        case, product["target_nodes"],
                        PARAMETERIZED_TARGETS.get((case, suite), [])),
                    "match_rule": ("exact nodeid" if (case, suite) not in PARAMETERIZED_TARGETS
                                   else "base[<id>] 或 base[...-<id>]；B 的 formatter 参数可位于 id 之前"),
                    "note": ("子节点只按登记参数 id 精确匹配"
                             if (case, suite) in PARAMETERIZED_TARGETS
                             else "只接受完全相同的 nodeid"),
                },
                "failure_signature": {
                    "phase": "call",
                    "exception": "AssertionError",
                    "expected_deviation": product.get("semantic_change"),
                    "assertion_locations": [
                        {"file": item["file"], "function": item["function"],
                         "lines": item["lines"], "sources": item.get("sources", [])}
                        for item in (trees[key]["assertions"].get(mutation, {})
                                     .get("locations", []))],
                    "assertion_coverage": (trees[key]["assertions"].get(mutation, {})
                                           .get("by_node", {})),
                    "requires_usable_health_reference": True,
                    "requires_witness_activation": True,
                },
            },
        })
    witness_rows_out = []
    for row in witness_rows:
        case, context = row["case"], row["context"]
        variant = row["product_variant"]
        key = f"{case}/{variant}__S0_original"
        tree = trees[key]
        env = context_three_state(table, case, context)
        extra = {"A": {"FORCE_COLOR": "1"}}.get(case, {})
        witness_rows_out.append({
            **row,
            "project": products[case]["project"],
            "project_version": products[case]["version"],
            "context_key": CONTEXT_KEY[case],
            "context_environment": env,
            "requested_context": context,
            "extra_environment": extra,
            "tree": {"path": tree["path"], "tar_sha256": tree["tar_sha256"],
                     "bytes": tree["bytes"], "tree_sha256": tree["tree_sha256"]},
            "product_patch_sha256": products[case]["variants"][variant]["patch_sha256"],
            "mutation_id": products[case]["variants"][variant]["mutation_id"],
            "toolchain": {"project": products[case]["project"],
                          "python": "3.11.9", "pytest": "7.2.0"},
            "argv_template": ["{venv_python}", "{witness_script}", "--case", case,
                              "--product-root", "{cwd}", "--json-out", "{json_out}"],
            "cwd": "{run_dir}/src",
            "probes": witnesses[case]["probes"],
            "expected_original": witnesses[case]["expected_original"][context],
        })
    return suite_rows, witness_rows_out


def package_digest(exclude):
    return common.package_digest(HERE, exclude=exclude)


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    TREES.mkdir(parents=True)
    table = read_json(CASE_TABLES)
    staging = pathlib.Path(tempfile.mkdtemp(prefix="s3-build-"))
    try:
        products = build_products(table, staging)
        suites = build_suites(table, staging)
        trees = build_trees(products, suites, staging)
        for entry in suites.values():
            for suite in entry.values():
                suite.pop("file_bytes", None)
        tools = toolchains(table)
        witness = witness_spec(table)
        suite_rows, witness_rows = build_rows(table, products, suites, trees, tools, witness)
        write_json(OUT / "products.json",
                   {"schema_version": "pyhost-context/S3-products/1.0",
                    "case_tables": {"path": str(CASE_TABLES.relative_to(WS)),
                                    "sha256": common.sha256_file(CASE_TABLES)},
                    "products": products, "cases": ["A", "B", "C"],
                    "candidate_executed": False})
        write_json(OUT / "suites.json",
                   {"schema_version": "pyhost-context/S3-suites/1.0",
                    "suites": suites, "candidate_executed": False})
        write_json(OUT / "trees.json",
                   {"schema_version": "pyhost-context/S3-trees/1.0",
                    "trees": trees, "count": len(trees), "candidate_executed": False})
        write_json(OUT / "toolchains.json",
                   {"schema_version": "pyhost-context/S3-toolchains/1.0",
                    "toolchains": tools, "candidate_executed": False,
                    "install_executed": False})
        write_json(OUT / "witness_spec.json",
                   {"schema_version": "pyhost-context/S3-witness-spec/1.0",
                    "spec": witness, "candidate_executed": False})
        rows_doc = {
            "schema_version": "pyhost-context/S3-rows/1.0",
            "batch_id": "next-S3-20261019",
            "seed": SEED,
            "status": "prepared_not_authorized",
            "execution_authorized": False,
            "row_key": "case/context/product_variant/suite_variant/repeat",
            "counts": {"suite": len(suite_rows), "witness": len(witness_rows),
                       "total": len(suite_rows) + len(witness_rows)},
            "caps": {"repair_suites_maximum": 384, "repair_witnesses_maximum": 96},
            "suite_rows": suite_rows,
            "witness_rows": witness_rows,
            "rows_sha256": canonical_sha(suite_rows + witness_rows),
            "candidate_executed": False,
        }
        write_json(HERE / "S3_ROWS_20261019.json", rows_doc)
        labels_rows = []
        keep = ("row_key", "case", "context", "product_variant", "suite_variant",
                "repeat", "label_sha256", "call_kind", "order")
        for row in suite_rows + witness_rows:
            labels_rows.append({key: row[key] for key in keep if key in row})
        labels_rows_sha256 = canonical_sha(labels_rows)
        if labels_rows_sha256 != "1861bab19983fff880828f117bf777da7c84249f2efd36e855f8bb8ffdf4fc79":
            raise AssertionError("label_rows_differ_from_reviewer_draft")
        commitment_path = HERE / "S3_PRE_RUN_COMMITMENT_20261019.json"
        commitment = {
            "schema_version": "pyhost-context/S3-commitment/1.0",
            "batch_id": "next-S3-20261019",
            "algorithm": "sorted('path:sha256\\n') joined, then one SHA-256",
            "seed": SEED,
            "counts": rows_doc["counts"],
            "rows_sha256": rows_doc["rows_sha256"],
            "labels_rows_sha256": labels_rows_sha256,
            "labels_rows_sha256_note": "与复核方 S3_ROWS_DRAFT_20261019.original_rows_sha256 相同；标签与顺序未改",
            "inputs": {
                "case_tables": {"path": str(CASE_TABLES.relative_to(WS)),
                                "sha256": common.sha256_file(CASE_TABLES)},
                "products_sha256": common.sha256_file(OUT / "products.json"),
                "suites_sha256": common.sha256_file(OUT / "suites.json"),
                "trees_sha256": common.sha256_file(OUT / "trees.json"),
                "toolchains_sha256": common.sha256_file(OUT / "toolchains.json"),
                "witness_spec_sha256": common.sha256_file(OUT / "witness_spec.json"),
            },
            "rules": {
                "adjudication": "s3_adjudicate.py",
                "adjudication_sha256": common.sha256_file(HERE / "s3_adjudicate.py")
                if (HERE / "s3_adjudicate.py").exists() else None,
                "adapter": "s3_adapter.py",
                "adapter_sha256": common.sha256_file(HERE / "s3_adapter.py")
                if (HERE / "s3_adapter.py").exists() else None,
                "witness_script": "witness_probes.py",
                "witness_script_sha256": common.sha256_file(HERE / "witness_probes.py"),
            },
            "unused_quota": {"repair_suites": 384 - len(suite_rows),
                             "repair_witnesses": 96 - len(witness_rows),
                             "transfer": False},
            "execution_authorized": False,
        }
        check = {
            "suite": len(suite_rows), "witness": len(witness_rows),
            "unique_rows": len({r["row_key"] for r in suite_rows + witness_rows}),
            "trees": len(trees), "products": sum(len(p["variants"]) for p in products.values()),
        }
        commitment["checks"] = check
        commitment["package_digest"] = package_digest({commitment_path})
        write_json(commitment_path, commitment)
        print(json.dumps({"ok": True, **check,
                          "rows_sha256": rows_doc["rows_sha256"],
                          "labels_rows_sha256": labels_rows_sha256,
                          "package_digest": commitment["package_digest"]},
                         ensure_ascii=False, indent=2))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
