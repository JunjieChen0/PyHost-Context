"""ClawsGO Science Agent：打包 SQJ 投稿材料底稿为单一 ZIP。"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parents[2] / "deliverables/PyHost_SQJ_Submission_Materials_20261020.zip"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    process = subprocess.run([sys.executable, "-B", str(HERE / "verify_submission_materials.py")],
                             capture_output=True, text=True)
    verification = json.loads(process.stdout)
    if process.returncode != 0:
        raise SystemExit(json.dumps(verification, ensure_ascii=False))
    (HERE / "VERIFICATION_RESULT.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

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
        "schema_version": "pyhost-context/sqj-submission-materials/1.0",
        "target_journal": "Software Quality Journal",
        "article_type": "Original Paper",
        "author": "Junjie Chen",
        "submitted": False,
        "new_experiments": 0,
        "verification_checks": verification.get("checks"),
        "open_confirmations": [
            "corresponding_author", "sole_author", "postal_address", "orcid",
            "funding", "competing_interests", "data_availability_route",
            "ai_use_disclosure_scope", "suggested_reviewers", "preprint_or_prior_submission",
        ],
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
                      "sha256": sha(raw), "verification_checks": verification.get("checks")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
