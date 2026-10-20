"""ClawsGO Science Agent：用三个合成反例自测 REV1 校验器。

反例对应 Reviewer 001 对旧校验器的三项探测：
A 空证据文件被接受；B 伪造计数被接受；C 正确的新计数被旧 marker 拒绝。
本次期望：A、B 被拒绝，C 被接受。

全部探针只在临时目录内构造；证据为空占位或字节副本，不写回工作区，
不修改任何实验原件，也不冒充真实证据。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
BASE_REL = "pyhost-context/extension/next_phase/s3_consolidation_rev1_20261019/results/reviewed_accounting.json"
S2_REL = "pyhost-context/extension/next_phase/s2_closure_20261020/REVIEWER001_CLOSURE.json"


def find_workspace(explicit: pathlib.Path | None = None) -> pathlib.Path:
    if explicit is not None:
        return explicit
    for parent in (HERE, *HERE.parents):
        if (parent / "pyhost-context").is_dir():
            return parent
    raise SystemExit("workspace_not_found")


def run(dossier: pathlib.Path, workspace: pathlib.Path, evidence: pathlib.Path) -> dict:
    process = subprocess.run(
        [sys.executable, "-B", str(dossier / "verify_dossier.py"),
         "--workspace", str(workspace), "--evidence-root", str(evidence)],
        capture_output=True, text=True,
    )
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError:
        payload = {"stdout": process.stdout, "stderr": process.stderr}
    return {"returncode": process.returncode, "payload": payload}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=pathlib.Path)
    args = parser.parse_args()
    workspace = find_workspace(args.workspace)
    ledger = json.loads((HERE / "CLAIM_LEDGER.json").read_text(encoding="utf-8"))
    hashes = json.loads((HERE / "EVIDENCE_HASHES.json").read_text(encoding="utf-8"))
    evidence_paths = [entry["path"] for entry in hashes["entries"]]

    with tempfile.TemporaryDirectory(prefix="pyhost-rev2-selftest-") as tmp:
        root = pathlib.Path(tmp)
        dossier = root / "dossier"
        fake_workspace = root / "workspace"
        evidence_full = root / "evidence-full"
        evidence_empty = root / "evidence-empty"

        shutil.copytree(HERE, dossier, ignore=shutil.ignore_patterns(
            "__pycache__", "VERIFICATION_RESULT.json", "*.pyc"))
        for rel in (BASE_REL, S2_REL):
            destination = fake_workspace / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((workspace / rel).read_bytes())
        selection_facts = json.loads((HERE / "SELECTION_BIAS_FACTS.json").read_text(encoding="utf-8"))
        authority_rel = selection_facts["authority"]["path"]
        authority_path = fake_workspace / authority_rel
        authority_path.parent.mkdir(parents=True, exist_ok=True)
        authority_path.write_bytes((workspace / authority_rel).read_bytes())
        for rel in evidence_paths:
            source = workspace / rel
            for base in (evidence_full, evidence_empty):
                destination = base / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
            (evidence_full / rel).write_bytes(source.read_bytes())
            (evidence_empty / rel).write_bytes(b"")

        # A：空证据 + 与空文件匹配的哈希，旧校验器会接受；新校验器必须以“非空”拒绝。
        empty_hashes = json.loads(json.dumps(hashes))
        for entry in empty_hashes["entries"]:
            entry["bytes"] = 0
            entry["sha256"] = hashlib.sha256(b"").hexdigest()
        (dossier / "EVIDENCE_HASHES.json").write_text(
            json.dumps(empty_hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        case_a = run(dossier, fake_workspace, evidence_empty)

        # B：把 C7 改成 999999 个调用（措辞与数字断言同时改），必须与推导值冲突。
        (dossier / "EVIDENCE_HASHES.json").write_text(
            json.dumps(hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tampered = json.loads(json.dumps(ledger))
        for claim in tampered["claims"]:
            if claim["id"] == "C7":
                claim["statement"] = claim["statement"].replace("3,086", "999999")
                claim["numeric_assertions"]["post_research_calls"] = 999999
        (dossier / "CLAIM_LEDGER.json").write_text(
            json.dumps(tampered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        case_b = run(dossier, fake_workspace, evidence_full)

        # C：恢复原档案；正确的新计数必须被接受。
        (dossier / "CLAIM_LEDGER.json").write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        case_c = run(dossier, fake_workspace, evidence_full)

        # D：把 10/4/11 改回错误的 14 项口径，必须以计数/集合/总和冲突拒绝。
        selection_original = (HERE / "SELECTION_BIAS_FACTS.json").read_text(encoding="utf-8")
        selection = json.loads(selection_original)
        selection["wrong_source_unselected"]["count"] = 14
        selection["wrong_source_unselected"]["names"] += selection["resolver_only"]["names"]
        (dossier / "SELECTION_BIAS_FACTS.json").write_text(
            json.dumps(selection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        case_d = run(dossier, fake_workspace, evidence_full)
        (dossier / "SELECTION_BIAS_FACTS.json").write_text(selection_original, encoding="utf-8")

        # E：删除“恢复只对选定合格单位”的限定，必须被 C1 措辞检查拒绝。
        unit_scope_removed = json.loads(json.dumps(ledger))
        for claim in unit_scope_removed["claims"]:
            if claim["id"] == "C1":
                claim["statement"] = claim["statement"].replace("只对选定合格单位", "对所有单位")
        (dossier / "CLAIM_LEDGER.json").write_text(
            json.dumps(unit_scope_removed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        case_e = run(dossier, fake_workspace, evidence_full)
        (dossier / "CLAIM_LEDGER.json").write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # F：把作者自检写成“全部通过、已独立复现”，必须被禁止措辞拒绝。
        doc_path = dossier / "CCFC_POSITIONING_AND_EVIDENCE_AUDIT_REV2.md"
        doc_original = doc_path.read_text(encoding="utf-8")
        doc_path.write_text(doc_original + "\n296 项检查全部通过，已独立复现。\n", encoding="utf-8")
        case_f = run(dossier, fake_workspace, evidence_full)
        doc_path.write_text(doc_original, encoding="utf-8")

    cases = [
        {"id": "A_empty_evidence_rejected", "expected": "rejected",
         "returncode": case_a["returncode"], "problems": case_a["payload"].get("problems", [])},
        {"id": "B_fabricated_count_rejected", "expected": "rejected",
         "returncode": case_b["returncode"], "problems": case_b["payload"].get("problems", [])},
        {"id": "C_correct_post_s2_counts_accepted", "expected": "accepted",
         "returncode": case_c["returncode"], "problems": case_c["payload"].get("problems", [])},
        {"id": "D_old_14_selection_count_rejected", "expected": "rejected",
         "returncode": case_d["returncode"], "problems": case_d["payload"].get("problems", [])},
        {"id": "E_recovery_scope_removed_rejected", "expected": "rejected",
         "returncode": case_e["returncode"], "problems": case_e["payload"].get("problems", [])},
        {"id": "F_author_checks_overclaimed_rejected", "expected": "rejected",
         "returncode": case_f["returncode"], "problems": case_f["payload"].get("problems", [])},
    ]
    ok = (case_a["returncode"] != 0
          and any(problem.startswith("evidence_nonempty") for problem in cases[0]["problems"])
          and case_b["returncode"] != 0
          and any(problem.startswith("claim_number:C7") or problem.startswith("claim_requires:C7")
                  for problem in cases[1]["problems"])
          and case_c["returncode"] == 0
          and case_d["returncode"] != 0
          and any(problem.startswith("selection_wrong_count") or problem.startswith("claim_number:C6")
                  for problem in cases[3]["problems"])
          and case_e["returncode"] != 0
          and any(problem.startswith("claim_requires:C1") for problem in cases[4]["problems"])
          and case_f["returncode"] != 0
          and any(problem.startswith("doc_no_overclaim:296") for problem in cases[5]["problems"]))

    print(json.dumps({
        "ok": ok,
        "scope": ("Synthetic probes only. Evidence files are empty placeholders or byte copies; "
                  "no real evidence, authorization or candidate execution is fabricated."),
        "cases": cases,
        "candidate_or_tool_calls": 0,
        "new_experiments": 0,
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
