"""ClawsGO Science Agent：S3 输入的离线复算（只读，不执行候选）。

    python3 recompute_s3_inputs.py

检查：承诺包指纹、240 行、48 棵树、9 枚变异、12 个套件补丁、见证规格、
标签/顺序哈希。任一不符返回 1。
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import s3_common as common  # noqa: E402
import build_s3_inputs as build  # noqa: E402

LABELS_SHA = "1861bab19983fff880828f117bf777da7c84249f2efd36e855f8bb8ffdf4fc79"


def main() -> int:
    problems = []
    rows = json.loads((HERE / "S3_ROWS_20261019.json").read_text(encoding="utf-8"))
    commitment = json.loads(
        (HERE / "S3_PRE_RUN_COMMITMENT_20261019.json").read_text(encoding="utf-8"))
    if len(rows["suite_rows"]) != 192 or len(rows["witness_rows"]) != 48:
        problems.append("row_counts")
    keys = [row["row_key"] for row in rows["suite_rows"] + rows["witness_rows"]]
    if len(keys) != len(set(keys)):
        problems.append("duplicate_row_keys")
    keep = ("row_key", "case", "context", "product_variant", "suite_variant",
            "repeat", "label_sha256", "call_kind", "order")
    labels = [{key: row[key] for key in keep if key in row}
              for row in rows["suite_rows"] + rows["witness_rows"]]
    if common.sha256_bytes(
            json.dumps(labels, sort_keys=True, ensure_ascii=False).encode()) != LABELS_SHA:
        problems.append("label_order_hash")
    if commitment.get("labels_rows_sha256") != LABELS_SHA:
        problems.append("commitment_labels_hash")
    if commitment.get("rows_sha256") != rows["rows_sha256"]:
        problems.append("commitment_rows_hash")

    trees = json.loads((HERE / "inputs/trees.json").read_text(encoding="utf-8"))["trees"]
    if len(trees) != 48:
        problems.append("tree_count")
    for key, tree in trees.items():
        path = HERE / tree["path"]
        if not path.is_file():
            problems.append(f"tree_missing:{key}")
            continue
        blob = path.read_bytes()
        if common.sha256_bytes(blob) != tree["tar_sha256"]:
            problems.append(f"tree_tar_hash:{key}")
        with tempfile.TemporaryDirectory() as temporary:
            common.unpack_tree(blob, temporary)
            if common.tree_hash(temporary) != tree["tree_sha256"]:
                problems.append(f"tree_content_hash:{key}")

    products = json.loads((HERE / "inputs/products.json").read_text(encoding="utf-8"))
    seen = set()
    for case in ("A", "B", "C"):
        for variant in ("M1", "M2", "M3"):
            info = products["products"][case]["variants"][variant]
            if info["tree_sha256"] in seen:
                problems.append(f"duplicate_product_tree:{case}:{variant}")
            seen.add(info["tree_sha256"])
    if len(seen) != 9:
        problems.append("product_count")

    suites = json.loads((HERE / "inputs/suites.json").read_text(encoding="utf-8"))["suites"]
    for case in ("A", "B", "C"):
        for name in ("R1_global_fix", "R2_local_isolation",
                     "R3_explicit_parameterization"):
            entry = suites[case][name]
            payload = {"source_artifact_sha256": entry["source_artifact_sha256"],
                       "changed_files": entry["changed_files"]}
            if entry["patch_sha256"] != common.sha256_bytes(
                    json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()):
                problems.append(f"suite_patch_hash:{case}:{name}")

    spec = json.loads((HERE / "inputs/witness_spec.json").read_text(encoding="utf-8"))["spec"]
    for case in ("A", "B", "C"):
        if set(spec[case]["expected_original"]) != set(build.CONTEXTS[case]):
            problems.append(f"witness_contexts:{case}")
        if set(spec[case]["mutation_checks"]) != {"M1", "M2", "M3"}:
            problems.append(f"witness_mutations:{case}")

    for item in rows["suite_rows"]:
        expected = item["expected"]
        identity = expected.get("identity") or {}
        if identity.get("base_nodes") != expected.get("target_nodes"):
            problems.append(f"identity_base_nodes:{item['row_key']}")
        if identity.get("mode") == "parameterized" and not identity.get("parameter_ids"):
            problems.append(f"identity_parameters_missing:{item['row_key']}")
        signature = expected.get("failure_signature") or {}
        if item["product_variant"] != "original" and not signature.get("assertion_locations"):
            problems.append(f"signature_locations_missing:{item['row_key']}")
    for item in rows["witness_rows"]:
        if not item.get("expected_original", {}).get("context_environment"):
            problems.append(f"witness_context_missing:{item['row_key']}")

    digest = build.package_digest({HERE / "S3_PRE_RUN_COMMITMENT_20261019.json"})
    if digest != commitment.get("package_digest"):
        problems.append("package_digest")
    if commitment.get("execution_authorized") is not False:
        problems.append("execution_flag")

    print(json.dumps({
        "ok": not problems,
        "problems": problems,
        "suite_rows": len(rows["suite_rows"]),
        "witness_rows": len(rows["witness_rows"]),
        "trees": len(trees),
        "products": len(seen),
        "labels_sha256": LABELS_SHA,
        "package_digest": digest,
        "candidate_executed": False,
        "installs": 0,
        "network": 0,
    }, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
