"""ClawsGO Science Agent：S2 输入件的只读复算（不执行候选、不联网）。

从 S1b 已关闭的原始结果重新推导五个单位的注册签名、30 行调度与读取链，
与交付文件比较；并核对预跑承诺的逐文件哈希与聚合值。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_s2_inputs as builder  # noqa: E402


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    checks = []

    def check(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    units, analysis_sha = builder.build_units()
    schedule = builder.build_schedule(units)
    toolchains = builder.build_toolchains(units)
    rules = builder.build_rules()
    chains = builder.build_read_chains()

    def compare(name, path, value):
        actual = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        check(name, actual == value, None if actual == value else "content_mismatch")

    compare("units_recomputed", HERE / "S2_UNITS_20261019.json",
            {"schema_version": "pyhost-context/S2-units/1.0", "batch_id": builder.BATCH,
             "source_analysis": {"path": "pyhost-context/extension/next_phase/state/"
                                         "s1b_20261019/S1B_ANALYSIS_20261019.json",
                                 "sha256": analysis_sha},
             "units": units})
    compare("schedule_recomputed", HERE / "S2_SCHEDULE_20261019.json", schedule)
    compare("toolchains_recomputed", HERE / "S2_TOOLCHAINS_20261019.json", toolchains)
    compare("rules_recomputed", HERE / "S2_RULES_20261019.json", rules)
    compare("read_chains_recomputed", HERE / "S2_READ_CHAINS_20261019.json", chains)

    commitment = json.loads((HERE / "S2_PRE_RUN_COMMITMENT_20261019.json")
                            .read_text(encoding="utf-8"))
    problems = []
    for rel, digest in commitment["files"].items():
        full = builder.WS / rel
        if not full.is_file() or sha(full) != digest:
            problems.append(rel)
    check("commitment_files", not problems, problems or None)
    check("commitment_aggregate",
          commitment["pre_run_commitment_hash"] == builder.sha_text(
              "".join(f"{p}:{d}\n" for p, d in sorted(commitment["files"].items()))))
    check("commitment_schedule_matches",
          commitment["schedule_sha256"] == schedule["schedule_sha256"])
    check("commitment_authorization_false",
          commitment["authorization"]["S2_execution"] is False)

    for unit in units:
        reg = unit["registered"]
        check("flip_count:" + unit["unit_id"],
              reg["flip_node_count"] == len(reg["flip_nodes"]))
        check("signature_count:" + unit["unit_id"],
              len(reg["failure_signature_sha256"]) == reg["flip_node_count"])

    total = len(checks)
    failed = [c for c in checks if not c["ok"]]
    result = {"ok": not failed, "checks": total, "failed": len(failed),
              "failures": failed, "commitment": commitment["pre_run_commitment_hash"],
              "schedule_sha256": schedule["schedule_sha256"],
              "candidate_or_tool_calls": 0, "installs": 0, "network_calls": 0}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"ok={result['ok']} checks={total} failed={len(failed)}")
        for item in failed:
            print("FAIL", item["check"], item["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
