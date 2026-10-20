"""ClawsGO Science Agent：打包 S2 关闭件接受与登记附记为单一 ZIP。"""
from __future__ import annotations

import hashlib
import json
import pathlib
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
WS = HERE.parents[3]
OUT = WS / "deliverables/PyHost_S2_Closure_Acceptance_20261020.zip"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    files = sorted(p for p in HERE.rglob("*")
                   if p.is_file() and p.name not in {OUT.name, "PACKAGE_MANIFEST.json"}
                   and "__pycache__" not in p.parts
                   and not any(part.startswith(".") for part in
                               p.relative_to(HERE).parts))
    members = []
    for path in files:
        data = path.read_bytes()
        members.append({"path": path.relative_to(HERE).as_posix(),
                        "bytes": len(data), "sha256": sha(data)})
    addendum = json.loads((HERE / "S2_REGISTRATION_ADDENDUM_20261020.json")
                          .read_text(encoding="utf-8"))
    manifest = {
        "schema_version": "pyhost-context/S2-closure-acceptance/1.0",
        "batch_id": "next-S2-20261019",
        "closure_sha256": "52abe6ecb3be2129e8b6fedd1dc0a1f399e2670a1bb6bb9c65d222218ef203e3",
        "decision": "S2_RESULTS_CLOSED_WITH_REVIEWER_PARSER_CORRECTION",
        "registered_levels": {u["unit_id"]: u["registered_level"]
                              for u in addendum["registrations"]},
        "review_required": False,
        "members": members,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("PACKAGE_MANIFEST.json", (2026, 10, 20, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, json.dumps(manifest, ensure_ascii=False, indent=2).encode())
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(HERE).as_posix(),
                                   (2026, 10, 20, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    raw = OUT.read_bytes()
    print(json.dumps({"path": str(OUT), "members": len(members),
                      "bytes": len(raw), "sha256": sha(raw)},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
