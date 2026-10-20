"""ClawsGO Science Agent：打包回执接受件为单一 ZIP（确定性顺序与时间戳）。"""
from __future__ import annotations

import hashlib
import json
import pathlib
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUTPUT = ROOT / "deliverables/PyHost_EvidenceConsolidation_REV1_Receipt_Ack_20261019.zip"
EXCLUDE = {OUTPUT.name}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    files = sorted(p for p in HERE.rglob("*")
                   if p.is_file() and p.name not in EXCLUDE and p.name != "PACKAGE_MANIFEST.json")
    members = []
    for path in files:
        data = path.read_bytes()
        members.append({"path": path.relative_to(HERE).as_posix(),
                        "bytes": len(data), "sha256": sha(data)})
    manifest = {
        "schema_version": "pyhost-context/receipt-ack-package/1.0",
        "reviewer_receipt_sha256": "cebfa4fccf230c722ea89fbad04df657af5cdecd65011932155a3f5dbf09a549",
        "accepted_submission_sha256": "2e7ad39657d7e56439888218e53a9da4188cf8896ce9a866b4dc425d9d84b356",
        "reviewer_decision": "RECEIPT_ACCEPTED_CLOSURE_MAINTAINED",
        "members": members,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo("PACKAGE_MANIFEST.json", (2026, 10, 19, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(info, json.dumps(manifest, ensure_ascii=False, indent=2).encode())
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(HERE).as_posix(), (2026, 10, 19, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, path.read_bytes())
    raw = OUTPUT.read_bytes()
    print(json.dumps({"path": str(OUTPUT), "members": len(members),
                      "bytes": len(raw), "sha256": sha(raw)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
