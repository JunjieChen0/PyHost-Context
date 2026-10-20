"""ClawsGO Science Agent：打包 S2 输入件为单一 ZIP（确定性顺序与时间戳）。"""
from __future__ import annotations

import hashlib
import json
import pathlib
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
WS = HERE.parents[3]
OUTPUT = WS / "deliverables/PyHost_S2_Inputs_20261019.zip"
EXCLUDE = {OUTPUT.name, "PACKAGE_MANIFEST.json"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    files = sorted(p for p in HERE.rglob("*")
                   if p.is_file() and p.name not in EXCLUDE
                   and "__pycache__" not in p.parts
                   and not any(part.startswith(".") for part in
                               p.relative_to(HERE).parts))
    members = []
    for path in files:
        data = path.read_bytes()
        members.append({"path": path.relative_to(HERE).as_posix(),
                        "bytes": len(data), "sha256": sha(data)})
    commitment = json.loads((HERE / "S2_PRE_RUN_COMMITMENT_20261019.json")
                            .read_text(encoding="utf-8"))
    manifest = {
        "schema_version": "pyhost-context/S2-inputs-package/1.0",
        "batch_id": "next-S2-20261019",
        "authorization_status": "NOT_AUTHORIZED_AWAITING_REVIEWER001_CLEARANCE",
        "pre_run_commitment_hash": commitment["pre_run_commitment_hash"],
        "schedule_sha256": commitment["schedule_sha256"],
        "members": members,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("PACKAGE_MANIFEST.json", (2026, 10, 19, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, json.dumps(manifest, ensure_ascii=False, indent=2).encode())
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(HERE).as_posix(),
                                   (2026, 10, 19, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    raw = OUTPUT.read_bytes()
    print(json.dumps({"path": str(OUTPUT), "members": len(members),
                      "bytes": len(raw), "sha256": sha(raw),
                      "commitment": commitment["pre_run_commitment_hash"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
