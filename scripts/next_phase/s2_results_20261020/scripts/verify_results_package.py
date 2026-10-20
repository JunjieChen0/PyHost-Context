"""ClawsGO Science Agent：S2 结果包清单自校验（不执行候选、不联网）。"""
from __future__ import annotations

import hashlib
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def main() -> int:
    problems = []
    manifest_path = ROOT / "S2_RESULTS_MANIFEST.json"
    if not manifest_path.is_file():
        print(json.dumps({"ok": False, "problems": ["manifest_missing"]}))
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    listed = {m["path"]: m for m in manifest["members"]}
    actual = {p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*")
              if p.is_file() and p.name != "S2_RESULTS_MANIFEST.json"
              and "__pycache__" not in p.parts}
    if set(listed) != actual:
        problems.append("member_set")
    for rel, member in listed.items():
        path = ROOT / rel
        if not path.is_file():
            problems.append("member_missing:" + rel)
        elif path.stat().st_size != member["bytes"] or sha(path) != member["sha256"]:
            problems.append("member_drift:" + rel)
    logs = [p for p in (ROOT / "raw/logs").glob("*") if p.is_file()]
    results = [p for p in (ROOT / "raw/results").rglob("*.json")]
    if len(logs) != 210:
        problems.append(f"raw_log_count:{len(logs)}")
    if len(results) != 60:
        problems.append(f"result_file_count:{len(results)}")
    if manifest.get("pre_run_commitment_hash") != \
            "5f85571e834db8e62907ab8c28949743eb84e0b496c80c482c4978196c1c9a52":
        problems.append("manifest_commitment")
    print(json.dumps({"ok": not problems, "problems": problems,
                      "members": len(listed), "raw_logs": len(logs),
                      "result_files": len(results),
                      "candidate_or_tool_calls": 0, "installs": 0,
                      "network_calls": 0}, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
