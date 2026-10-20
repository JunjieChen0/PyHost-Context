"""ClawsGO Science Agent：把权威快照哈希与逐条证据哈希封入 REV1 档案。

只读工作区文件；只写本档案目录内的 CLAIM_LEDGER.json 与 EVIDENCE_HASHES.json。
不执行候选、不联网、不修改任何实验原件。
"""
from __future__ import annotations

import hashlib
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent

BASE_REL = "pyhost-context/extension/next_phase/s3_consolidation_rev1_20261019/results/reviewed_accounting.json"
S2_REL = "pyhost-context/extension/next_phase/s2_closure_20261020/REVIEWER001_CLOSURE.json"


def find_workspace() -> pathlib.Path:
    for parent in (HERE, *HERE.parents):
        if (parent / "pyhost-context").is_dir():
            return parent
    raise SystemExit("workspace_not_found")


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    workspace = find_workspace()
    ledger_path = HERE / "CLAIM_LEDGER.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))

    for key, rel in (("base", BASE_REL), ("s2", S2_REL)):
        path = workspace / rel
        ledger["source_snapshots"][key]["sha256"] = sha(path)

    seen: dict[str, dict] = {}
    for claim in ledger["claims"]:
        for item in claim["evidence"]:
            rel = item["path"]
            path = workspace / rel
            if not path.is_file():
                raise SystemExit("missing_evidence:" + rel)
            data = path.read_bytes()
            if not data:
                raise SystemExit("empty_evidence:" + rel)
            entry = {"path": rel, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            if rel in seen and seen[rel] != entry:
                raise SystemExit("conflicting_evidence:" + rel)
            seen[rel] = entry

    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (HERE / "EVIDENCE_HASHES.json").write_text(
        json.dumps({
            "schema_version": "pyhost-context/evidence-hashes/1.0",
            "note": "Hashes bind the dossier claims to bytes in the author workspace; they are not part of the submitted manuscript.",
            "entries": [seen[key] for key in sorted(seen)],
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"ok": True, "evidence_entries": len(seen)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
