"""ClawsGO Science Agent：生成 S1b 准备输入（名单、工具链、调度、事前承诺）。

本脚本只读工作区中的冻结锁、wheel 库、S1a 记录与 sdist；不安装、不导入、不执行候选。
输出全部写入本目录，供打包与审阅方签署前核对。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import tarfile
from packaging.utils import canonicalize_name

HERE = pathlib.Path(__file__).resolve().parent
PROJECT = HERE.parents[2]          # pyhost-context/
WS = PROJECT.parent                # project-20261013-1/
OUT = HERE

ROSTER = [
    # position, project, version, group, profile, pytest
    (2, "structlog", "26.1.0", "mechanism", "D-R1", "9.1.1"),
    (4, "rich-toolkit", "0.20.5", "mechanism", "D-R1", "9.1.1"),
    (7, "twine", "7.0.0", "mechanism", "D-R0", "7.2.0"),
    (18, "typer", "0.27.2", "reference", "S1a", "7.2.0"),
    (25, "pytest-xdist", "3.8.0", "reference", "S1a", "7.2.0"),
]
CONDITIONS = [
    ("K01_TZ", "TZ"), ("K02_LC_ALL", "LC_ALL"), ("K03_LANG", "LANG"),
    ("K04_HASH", "PYTHONHASHSEED"), ("K05_NO_COLOR", "NO_COLOR"),
    ("K06_TERM", "TERM"), ("K07_COLUMNS", "COLUMNS"), ("K08_LINES", "LINES"),
]
SEED = "pyhost-context-S1b-v1-20261019"
BATCH = "next-S1b-20261019"
OBSERVER = ("pyhost-observer", "0.2.0")
REVIEWER_DIR = HERE / "inputs"
AMENDMENT = REVIEWER_DIR / "DECLARED_PYTEST_METADATA_AMENDMENT.json"
TRANSITION = REVIEWER_DIR / "pytest_runner_transition.py"
CLOSURE = REVIEWER_DIR / "REVIEWER001_D_RESULTS_CLOSURE_20261019.json"
QUALIFIED = REVIEWER_DIR / "D_QUALIFIED_AND_S1B_PREPARATION.json"


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def rel(path):
    return str(pathlib.Path(path).resolve().relative_to(WS))


def sdist_members(path):
    """普通文件视图，去掉 sdist 顶层目录。"""
    files = {}
    with tarfile.open(path) as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            parts = pathlib.PurePosixPath(member.name).parts
            key = "/".join(parts[1:]) if len(parts) > 1 else parts[0]
            if key in files:
                raise SystemExit(f"duplicate_member:{path}:{key}")
            files[key] = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
    return files


def d_toolchain(project, version, pytest_version):
    document = read(PROJECT / "extension/next_phase/state/d_toolchain/D_RESOLUTION_LOCKS_REV1.json")
    lock = next(row for row in document["projects"] if row["project"] == project)
    rows = list(lock["lock"]["rows"]) + [lock["candidate_wheel"]]
    wheel_root = PROJECT / "extension/next_phase/state/d_toolchain/wheels"
    files, expected = [], {}
    for row in rows:
        path = wheel_root / row["sha256"] / row["filename"]
        if not path.is_file() or sha(path) != row["sha256"]:
            raise SystemExit(f"d_wheel_drift:{project}:{row['filename']}")
        files.append({"filename": row["filename"], "sha256": row["sha256"],
                      "bytes": path.stat().st_size, "path": rel(path)})
        expected[canonicalize_name(row["name"])] = str(row["version"])
    expected[OBSERVER[0]] = OBSERVER[1]
    plugin_map = read(PROJECT / "extension/next_phase/state/d_toolchain/D_PLUGIN_MAP_REV1.json")
    entry = next(row for row in plugin_map["projects"] if row["project"] == project)
    defaults = read(PROJECT / "extension/next_phase/D_PLUGIN_DEFAULTS_20261018.json")
    extra = list((defaults["projects"].get(project) or {}).get("extra_pytest_args") or [])
    return {
        "profile": "D-R1" if pytest_version == "9.1.1" else "D-R0",
        "pytest_version": pytest_version,
        "lock": {"path": rel(PROJECT / "extension/next_phase/state/d_toolchain/D_RESOLUTION_LOCKS_REV1.json"),
                 "sha256": sha(PROJECT / "extension/next_phase/state/d_toolchain/D_RESOLUTION_LOCKS_REV1.json"),
                 "project_record_sha256": hashlib.sha256(json.dumps(lock, sort_keys=True,
                                                                   ensure_ascii=False).encode()).hexdigest()},
        "install_files": files,
        "expected_distributions": dict(sorted(expected.items())),
        "plugin_loads": list(entry["loads"]),
        "disabled_plugins": [],
        "extra_pytest_args": extra,
        "plugin_source": "extension/next_phase/state/d_toolchain/D_PLUGIN_MAP_REV1.json",
        "plugin_entry_sha256": hashlib.sha256(json.dumps(entry, sort_keys=True,
                                                         ensure_ascii=False).encode()).hexdigest(),
    }


def s1a_toolchain(project, version):
    candidates = read(PROJECT / "extension/next_phase/state/candidates_next.json")["candidates"][project]
    if candidates["version"] != version:
        raise SystemExit(f"s1a_version:{project}")
    wheelhouse = PROJECT / "extension/next_phase/state/wheelhouse" / project
    files, expected = [], {}
    for filename, digest in sorted(candidates["dependencies"]["wheels"].items()):
        path = wheelhouse / filename
        if not path.is_file() or sha(path) != digest:
            raise SystemExit(f"s1a_wheel_drift:{project}:{filename}")
        files.append({"filename": filename, "sha256": digest,
                      "bytes": path.stat().st_size, "path": rel(path)})
    for name, value in candidates["dependencies"]["resolved_distributions"].items():
        expected[canonicalize_name(name)] = str(value)
    expected[OBSERVER[0]] = OBSERVER[1]
    result = read(PROJECT / "extension/next_phase/S1A_ADMISSION_RESULTS_20261018.json")
    admitted = next(row for row in result["candidates"]
                    if row["project"] == project and row["version"] == version)
    return {
        "profile": "S1a",
        "pytest_version": admitted["resolved_distributions"]["pytest"],
        "s1a_record": {"result_sha256": admitted["result_sha256"],
                       "candidate_commitment": admitted["candidate_commitment"],
                       "attempt_dir": admitted["attempt_dir"]},
        "install_files": files,
        "expected_distributions": dict(sorted(expected.items())),
        "plugin_loads": [],
        "disabled_plugins": [],
        "extra_pytest_args": [],
        "plugin_source": "S1a A7 used plugin_loads=() as recorded in run_extension.admit_one",
        "plugin_entry_sha256": None,
    }


def sdist_record(project, filename, digest):
    path = PROJECT / "extension/next_phase/state/artifacts" / project / filename
    if not path.is_file() or sha(path) != digest:
        raise SystemExit(f"sdist_drift:{project}:{filename}")
    members = sdist_members(path)
    return {"filename": filename, "sha256": digest, "bytes": path.stat().st_size,
            "path": rel(path), "regular_files": len(members),
            "members_sha256": hashlib.sha256(json.dumps(members, sort_keys=True).encode()).hexdigest(),
            "source_dir": project}


def build_roster():
    projects = []
    for position, project, version, group, profile, pytest_version in ROSTER:
        projects.append({
            "position": position, "project": project, "version": version,
            "group": group, "profile": profile, "pytest_version": pytest_version,
            "qualification": ("D_admission_amended_20261019" if group == "mechanism"
                              else "S1a_admitted_20261018"),
        })
    return {
        "schema_version": "pyhost-context/S1b-roster/1.0",
        "batch_id": BATCH,
        "qualification_profile": "signed_post_execution_metadata_amendment_20261019",
        "amended_admitted": ["structlog", "rich-toolkit", "twine"],
        "original_rule_admitted": ["twine"],
        "mechanism_group": ["structlog", "rich-toolkit", "twine"],
        "reference_group": ["typer", "pytest-xdist"],
        "projects": projects,
        "expected_actual_N": 5,
        "main_matrix": {"formula": "5 x 9 x 5", "runs": 225},
        "independent_b0": {"formula": "5 x 45", "runs": 225},
        "total_runs": 450,
        "b0_runs_per_project_total": 50,
        "profiles": ["D-R1", "D-R0", "S1a"],
        "profile_stratification_required": True,
        "between_group_causal_attribution_allowed": False,
        "notes": [
            "位置、分组与家族上限沿用冻结记录，不扩池、不补位。",
            "机制组与参照组工具链不同；组间差异只作分层/描述性报告。",
        ],
    }


def build_toolchains():
    projects = {}
    sdists = {
        "structlog": ("structlog-26.1.0.tar.gz",
                      "f63a716cbd1b1291cf7661de7794b455acfa4c43c5bcf1630e6ad5ddc1adb3b7"),
        "rich-toolkit": ("rich_toolkit-0.20.5.tar.gz",
                         "0c9e1c414ffb0720be26285d472e263d1e704b71d31a7e13274b9996db4969e1"),
        "twine": ("twine-7.0.0.tar.gz",
                  "85cdb29c518efef867360ae4acd4b0dfd61c8654a22fca08e6f8539f05022177"),
        "typer": ("typer-0.27.2.tar.gz",
                  "269b7eb9d3c202ca84b4bc9618cb04ebb43d3d4d1e567e4c768607232c05f945"),
        "pytest-xdist": ("pytest_xdist-3.8.0.tar.gz",
                         "7e578125ec9bc6050861aa93f2d59f1d8d085595d6551c2c90b6f4fad8d3a9f1"),
    }
    for position, project, version, group, profile, pytest_version in ROSTER:
        toolchain = (d_toolchain(project, version, pytest_version) if group == "mechanism"
                     else s1a_toolchain(project, version))
        filename, digest = sdists[project]
        toolchain.update({
            "project": project, "version": version, "group": group,
            "source": sdist_record(project, filename, digest),
            "python_version": "3.11.9",
            "python_implementation": "CPython",
            "install_mode": "exact_files_offline_no_deps",
            "runner_profile": "s1b/2026-10-19-v1",
        })
        projects[project] = toolchain
    return {
        "schema_version": "pyhost-context/S1b-toolchains/1.0",
        "batch_id": BATCH,
        "projects": projects,
        "observer": {"name": OBSERVER[0], "version": OBSERVER[1],
                     "install": "matrix_runner.observer_install"},
        "amended_transition_rule": {
            "path": rel(TRANSITION), "sha256": sha(TRANSITION),
            "declaration": rel(AMENDMENT), "declaration_sha256": sha(AMENDMENT),
            "applies_to_profiles": ["D-R1"],
            "declared_pytest_version": "9.1.1",
            "pytest_config_source_sha256":
                "98b05c2c37d09e1d5c05975efc2d9af98d4aff6ea47de410a8e93956420873c1",
            "applies_to_reference_profiles": False,
        },
        "group_isolation": {
            "mechanism_plugins_loaded_explicitly": True,
            "reference_plugins_loaded": [],
            "reason": "S1a A7 used plugin_loads=(); S1b keeps each project's original profile",
        },
    }


def build_schedule(roster, toolchains):
    entries = []
    for row in roster["projects"]:
        project = row["project"]
        for condition, _key in [("B0", None)] + CONDITIONS:
            for repeat in range(1, 6):
                label = f"main|pos={row['position']:02d}|{project}|{condition}|r={repeat}"
                entries.append({
                    "arm": "main", "project": project, "position": row["position"],
                    "condition": condition, "repeat": repeat, "label": label,
                    "run_id": f"s1b-main-p{row['position']:02d}-{condition}-r{repeat}",
                })
        for repeat in range(1, 46):
            label = f"control|pos={row['position']:02d}|{project}|B0|r={repeat}"
            entries.append({
                "arm": "control", "project": project, "position": row["position"],
                "condition": "B0", "repeat": repeat, "label": label,
                "run_id": f"s1b-ctl-p{row['position']:02d}-B0-r{repeat:02d}",
            })
    for entry in entries:
        entry["label_sha256"] = hashlib.sha256(f"{SEED}|{entry['label']}".encode()).hexdigest()
    entries.sort(key=lambda e: (e["label_sha256"], e["run_id"]))
    for index, entry in enumerate(entries, 1):
        entry["order"] = index
    counts = {}
    for entry in entries:
        key = (entry["arm"], entry["project"])
        counts[key] = counts.get(key, 0) + 1
    schedule = {
        "schema_version": "pyhost-context/S1b-schedule/1.0",
        "batch_id": BATCH,
        "seed": SEED,
        "algorithm": "SHA-256(seed|label) hex ascending; ties by run_id; labels fixed before hashing",
        "main_runs": 225,
        "control_runs": 225,
        "total_runs": 450,
        "per_project_counts": {f"{arm}:{project}": count
                               for (arm, project), count in sorted(counts.items())},
        "run_id_disjoint_arms": True,
        "entries": entries,
    }
    schedule["schedule_sha256"] = hashlib.sha256(
        json.dumps({k: v for k, v in schedule.items() if k != "schedule_sha256"},
                   sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return schedule


def commitment_files(roster, toolchains, schedule):
    files = {
        rel(OUT / "S1B_ROSTER_20261019.json"): None,
        rel(OUT / "S1B_TOOLCHAINS_20261019.json"): None,
        rel(OUT / "S1B_SCHEDULE_20261019.json"): None,
        rel(OUT / "S1B_PREREGISTERED_PREDICTIONS_20261019.json"): None,
        rel(OUT / "S1B_REPAIR_MUTATION_ADJUDICATION_20261019.json"): None,
        rel(OUT / "s1b_adapter.py"): None,
        rel(OUT / "test_s1b_adapter.py"): None,
        rel(TRANSITION): None, rel(AMENDMENT): None, rel(CLOSURE): None, rel(QUALIFIED): None,
        rel(PROJECT / "extension/effective/EFFECTIVE_PROTOCOL_v1.1_Reviewer001.json"): None,
        rel(PROJECT / "extension/effective/EFFECTIVE_PROTOCOL_v1.2_Reviewer001.json"): None,
        rel(PROJECT / "admission_freeze/b0_environment.json"): None,
        rel(PROJECT / "matrix/protocol/matrix_protocol_v0.4_reviewed.json"): None,
        rel(PROJECT / "matrix/tooling/run_matrix.py"): None,
        rel(PROJECT / "tooling/artifact_identity.py"): None,
        rel(PROJECT / "tooling/restore_source_tree.py"): None,
        rel(PROJECT / "extension/tooling/run_extension.py"): None,
        rel(PROJECT / "extension/next_phase/state/d_toolchain/D_RESOLUTION_LOCKS_REV1.json"): None,
        rel(PROJECT / "extension/next_phase/state/d_toolchain/D_PLUGIN_MAP_REV1.json"): None,
        rel(PROJECT / "extension/next_phase/D_PLUGIN_DEFAULTS_20261018.json"): None,
        rel(PROJECT / "extension/next_phase/state/candidates_next.json"): None,
        rel(PROJECT / "extension/next_phase/S1A_ADMISSION_RESULTS_20261018.json"): None,
    }
    for path in PROJECT.joinpath("matrix/tooling/matrix_runner").glob("*.py"):
        files[rel(path)] = None
    for toolchain in toolchains["projects"].values():
        files[toolchain["source"]["path"]] = None
    for path in files:
        if not (WS / path).is_file():
            raise SystemExit(f"commitment_input_missing:{path}")
    return {path: sha(WS / path) for path in files}


def aggregate(files):
    return hashlib.sha256("".join(f"{path}:{files[path]}\n"
                                  for path in sorted(files)).encode()).hexdigest()


def main():
    roster = build_roster()
    toolchains = build_toolchains()
    schedule = build_schedule(roster, toolchains)
    for name, value in (("S1B_ROSTER_20261019.json", roster),
                        ("S1B_TOOLCHAINS_20261019.json", toolchains),
                        ("S1B_SCHEDULE_20261019.json", schedule)):
        (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                           sort_keys=True) + "\n", encoding="utf-8")
    files = commitment_files(roster, toolchains, schedule)
    commitment = {
        "schema_version": "pyhost-context/S1b-pre-run-commitment/1.1",
        "batch_id": BATCH,
        "algorithm": "sorted('{path}:{sha256}\\n') joined, then one SHA-256",
        "files": files,
        "pre_run_commitment_hash": aggregate(files),
        "schedule_sha256": schedule["schedule_sha256"],
        "main_runs": 225,
        "control_runs": 225,
        "total_runs": 450,
        "sealed_before_execution": [
            "S1B_PREREGISTERED_PREDICTIONS_20261019.json",
            "S1B_REPAIR_MUTATION_ADJUDICATION_20261019.json",
        ],
        "supersedes": {
            "pre_run_commitment_hash":
                "68db49ea751567c527c43fd841565eadf9556541870ff93cd41e93929a330bcc",
            "reason": "加入执行前封存的机制预测与修复-变异判定函数；其余输入与调度未变",
        },
        "authorization_required": {
            "signed_by": "Reviewer 001",
            "decision": "APPROVED_FOR_S1B_EXECUTION",
            "S1b_execution": True,
            "S2": False, "S3": False, "repro": False, "new_perturbation_runs": False,
        },
        "execution_authorized": False,
        "note": "本文件是准备输入；S1b 执行仍需 Reviewer 001 另行签署并绑定本承诺哈希。",
    }
    (OUT / "S1B_PRE_RUN_COMMITMENT_20261019.json").write_text(
        json.dumps(commitment, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(json.dumps({"commitment": commitment["pre_run_commitment_hash"],
                      "schedule": schedule["schedule_sha256"],
                      "files": len(files),
                      "runs": len(schedule["entries"]),
                      "counts": schedule["per_project_counts"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
