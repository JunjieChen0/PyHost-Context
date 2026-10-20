"""ClawsGO Science Agent：核验 S3 结果包清单并运行离线复算。"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    problems = []
    manifest = json.loads((ROOT / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
    seen = set()
    for item in manifest["members"]:
        path = ROOT / item["path"]
        seen.add(item["path"])
        if not path.is_file():
            problems.append("missing:" + item["path"])
        elif path.stat().st_size != item["bytes"] or sha(path) != item["sha256"]:
            problems.append("payload:" + item["path"])
    actual = {str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
              if p.is_file() and p.name != "PACKAGE_MANIFEST.json"
              and "__pycache__" not in p.parts}
    if actual != seen:
        problems.append("manifest_names")
    if manifest["counts"] != {"calls": 240, "suite": 192, "witness": 48,
                              "completed": 220, "failed": 20}:
        problems.append("counts")
    with tempfile.TemporaryDirectory(prefix="s3-results-verify-") as tmp:
        command = [sys.executable, str(HERE / "recompute_results.py"),
                   "--state", str(ROOT / "evidence"),
                   "--inputs", str(ROOT / "inputs_snapshot"),
                   "--results", str(ROOT / "results"),
                   "--out", tmp]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            problems.append("recompute_failed")
        else:
            report = json.loads((pathlib.Path(tmp) / "recompute.json").read_text())
            if not report.get("ok"):
                problems.append("recompute_not_ok")
    print(json.dumps({"ok": not problems, "problems": problems,
                      "members": len(manifest["members"])},
                     ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
