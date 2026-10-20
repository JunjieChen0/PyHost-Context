"""ClawsGO Science Agent：生成 S2 预跑承诺（不执行候选）。"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
WS = pathlib.Path(os.environ.get("PYHOST_PROJECT") or NEXT.parents[2]).resolve()
OUT = HERE / "S2_PRE_RUN_COMMITMENT_20261019.json"


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def aggregate(files):
    return hashlib.sha256("".join(f"{path}:{digest}\n"
                                  for path, digest in sorted(files.items())).encode()).hexdigest()


def rel(path):
    return pathlib.Path(path).resolve().relative_to(WS).as_posix()


def main():
    schedule = json.loads((HERE / "S2_SCHEDULE_20261019.json").read_text(encoding="utf-8"))
    paths = [
        HERE / "S2_UNITS_20261019.json",
        HERE / "S2_ROSTER_20261019.json",
        HERE / "S2_SCHEDULE_20261019.json",
        HERE / "S2_TOOLCHAINS_20261019.json",
        HERE / "S2_RULES_20261019.json",
        HERE / "S2_READ_CHAINS_20261019.json",
        HERE / "s2_adapter.py",
        HERE / "test_s2_adapter.py",
        NEXT / "state/s1b_20261019/S1B_ANALYSIS_20261019.json",
        NEXT / "s1b_20261019/S1B_ROSTER_20261019.json",
        NEXT / "s1b_20261019/S1B_TOOLCHAINS_20261019.json",
        NEXT / "s1b_20261019/S1B_SCHEDULE_20261019.json",
    ]
    files = {rel(path): sha(path) for path in paths}
    commitment = {
        "schema_version": "pyhost-context/S2-pre-run-commitment/1.0",
        "batch_id": "next-S2-20261019",
        "created_at_utc": "2026-10-19T00:00:00Z",
        "scope": ("五个 S1b 已确认 E2 单位的恢复复核：2 块 × 3 轮 × 5 单位 = 30 轮。"),
        "schedule_sha256": schedule["schedule_sha256"],
        "total_runs": 30,
        "blocks": 2,
        "units": ["typer/K07_COLUMNS", "typer/K05_NO_COLOR", "typer/K06_TERM",
                  "twine/K06_TERM", "rich-toolkit/K07_COLUMNS"],
        "authorization": {
            "S2_execution": False,
            "S3": False,
            "repro": False,
            "S1b_replay": False,
            "new_samples": False,
            "new_contexts": False,
            "semantic_mutations": False,
            "note": "执行须 Reviewer 001 另行签署放行件；本承诺本身不授权任何运行。",
        },
        "files": files,
    }
    commitment["pre_run_commitment_hash"] = aggregate(files)
    OUT.write_text(json.dumps(commitment, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                   encoding="utf-8")
    print(json.dumps({"path": str(OUT), "files": len(files),
                      "commitment": commitment["pre_run_commitment_hash"],
                      "schedule": commitment["schedule_sha256"],
                      "bytes": OUT.stat().st_size}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
