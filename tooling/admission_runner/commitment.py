"""ClawsGO Science Agent：把"运行前承诺"与"运行后归档"分开的规范文件清单。

独立审计 F03 指出：原 ``admission_rules_hash`` 计算于结果产生**之后**（09:40 UTC 晚于
09:13 的运行），且把 ``admission_result_v2.json`` 等**结果文件**纳入组合指纹。这种指纹
只能支持事后归档核验，不能证明事前冻结。

本模块只列**规范**文件（规则、冻结参数、候选条件、运行器与 observer 源码），
**不含任何结果文件**。驱动在任何一轮执行之前计算并写出 ``pre_run_commitment.json``；
运行结束后另出归档清单。两个指纹分开保存，互不覆盖。
"""
from pathlib import Path

from env_state import aggregate_fingerprint

ROOT = Path(__file__).resolve().parent.parent.parent  # pyhost-context/

# 规范文件：任何一条变更都必须重新计算并重新签署运行前承诺。
NORMATIVE_FILES = (
    "准入阶段规则草案_v0.1.md",
    "admission_rules_v0.1.json",
    "admission_candidate_metadata_probe_v0.2.json",
    "admission_freeze/b0_environment.json",
    "admission_freeze/artifact_acquisition_plan.json",
    "admission_freeze/artifact_acquisition_receipt.json",
    "admission_freeze/per_candidate_conditions.json",
    "admission_freeze/test_dependency_artifacts.json",
    "admission_freeze/plugin_inventory.json",
    "admission_freeze/execution_control_model.json",
    "tooling/run_admission_baseline.py",
    "tooling/acquire_artifacts.py",
    "tooling/verify_source_tree.py",
    "tooling/restore_source_tree.py",
    "tooling/admission_runner/__init__.py",
    "tooling/admission_runner/env_state.py",
    "tooling/admission_runner/timeout_ledger.py",
    "tooling/admission_runner/pytest_jsonl.py",
    "tooling/admission_runner/admission_gate.py",
    "tooling/admission_runner/b0_runner.py",
    "tooling/admission_runner/run_b0.py",
    "tooling/admission_runner/observer_install.py",
    "tooling/admission_runner/netwatch_sitecustomize.py",
    "tooling/admission_runner/commitment.py",
    "tooling/admission_runner/integration/jsonl_producer.py",
    "tooling/admission_runner/integration/pipeline.py",
)


def sha256_of(path):
    import hashlib
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def normative_hashes(root=None):
    """逐文件计算规范文件哈希；缺文件即报错，不静默跳过。"""
    root = Path(root or ROOT)
    missing = [rel for rel in NORMATIVE_FILES if not (root / rel).is_file()]
    if missing:
        raise FileNotFoundError(f"规范文件缺失，拒绝计算运行前承诺：{missing}")
    return {rel: sha256_of(root / rel) for rel in NORMATIVE_FILES}


def compute_commitment(root=None):
    """运行前承诺：规范文件哈希 + 组合指纹 + 明示算法。"""
    root = Path(root or ROOT)
    files = normative_hashes(root)
    fingerprint, payload_bytes = aggregate_fingerprint(files)
    return {
        "scope": "运行前承诺：仅规范文件（规则、冻结参数、候选条件、运行器与 observer 源码），不含任何结果文件",
        "aggregate_algorithm": (
            "对每个 (path, sha256) 取 \"{path}:{sha256}\\n\"，按 path 字节序升序拼接为 "
            "UTF-8 字节串，再做一次 SHA-256（见 tooling/admission_runner/env_state.py:"
            "aggregate_fingerprint）。"
        ),
        "files": files,
        "file_count": len(files),
        "canonical_payload_bytes": payload_bytes,
        "pre_run_commitment_hash": fingerprint,
    }


if __name__ == "__main__":  # pragma: no cover - 手工使用
    import json
    print(json.dumps(compute_commitment(), ensure_ascii=False, indent=2))
