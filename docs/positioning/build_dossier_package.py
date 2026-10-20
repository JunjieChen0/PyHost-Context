"""ClawsGO Science Agent：校验并打包 REV1 定位档案为单一 ZIP。"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parents[2] / "deliverables/PyHost_CCFC_Dossier_REV2_20261020.zip"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(script: str) -> dict:
    process = subprocess.run([sys.executable, "-B", str(HERE / script)],
                             capture_output=True, text=True)
    payload = json.loads(process.stdout)
    if process.returncode != 0:
        raise SystemExit(f"{script} failed: {json.dumps(payload, ensure_ascii=False)}")
    return payload


def main() -> None:
    run("seal_evidence.py")
    verification = run("verify_dossier.py")
    selftest = run("selftest_checker.py")
    (HERE / "VERIFICATION_RESULT.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (HERE / "SELFTEST_RESULT.json").write_text(
        json.dumps(selftest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    files = sorted(
        path for path in HERE.rglob("*")
        if path.is_file()
        and path.name not in {"PACKAGE_MANIFEST.json", OUT.name}
        and "__pycache__" not in path.parts
        and not any(part.startswith(".") for part in path.relative_to(HERE).parts)
        and path.suffix != ".pyc"
    )
    members = []
    for path in files:
        data = path.read_bytes()
        members.append({"path": path.relative_to(HERE).as_posix(),
                        "bytes": len(data), "sha256": sha(data)})
    manifest = {
        "schema_version": "pyhost-context/submission-dossier-rev2/1.0",
        "target_journal": "Software Quality Journal",
        "ccf_rank": "C (third-party; official catalogue not re-verified)",
        "reviewer_decision": "REV1_ACCEPTED_WITH_REVIEWER_FACTUAL_ADDENDUM",
        "reviewer_package_sha256": "d835207e3c97f6001fef8a09324bc332e88e5ce013922a90139f9d9d4aa4e7d3",
        "supersedes": {
            "path": "PyHost_CCFC_Dossier_REV1_20261020.zip",
            "sha256": "bed0a011ce87d89c182dfdde1f976d3ca278c09aae0c0b024a29cbe4881a2ba3",
        },
        "new_experiments": 0,
        "verification_checks": verification.get("checks"),
        "selftest_ok": selftest.get("ok"),
        "members": members,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("PACKAGE_MANIFEST.json", (2026, 10, 20, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, json.dumps(manifest, ensure_ascii=False, indent=2).encode())
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(HERE).as_posix(), (2026, 10, 20, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    raw = OUT.read_bytes()
    print(json.dumps({"path": str(OUT), "members": len(members), "bytes": len(raw),
                      "sha256": sha(raw), "verification_checks": verification.get("checks"),
                      "selftest_ok": selftest.get("ok")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
