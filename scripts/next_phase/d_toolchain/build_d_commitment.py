"""ClawsGO Science Agent：生成 D 档一次性尝试的事前承诺（待签署）。"""
import datetime
import hashlib
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
STATE = NEXT / "state" / "d_toolchain"
LOCKS = STATE / "D_RESOLUTION_LOCKS_REV1.json"
PLUGINS = STATE / "D_PLUGIN_MAP_REV1.json"
VALIDATION = STATE / "D_LOCK_VALIDATION_REV1.json"
DEFAULTS = NEXT / "D_PLUGIN_DEFAULTS_20261018.json"
OUT_JSON = NEXT / "D_PRE_RUN_COMMITMENT_REV1_20261018.json"
OUT_MD = NEXT / "D_PRE_RUN_COMMITMENT_REV1_20261018.md"


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def main():
    locks = json.loads(LOCKS.read_text(encoding="utf-8"))
    plugins = json.loads(PLUGINS.read_text(encoding="utf-8"))
    validation = json.loads(VALIDATION.read_text(encoding="utf-8"))
    plugin_index = {row["project"]: row for row in plugins["projects"]}
    defaults = json.loads(DEFAULTS.read_text(encoding="utf-8"))
    entries = []
    for row in locks["projects"]:
        if row.get("status") != "locked":
            continue
        plugin = plugin_index.get(row["project"]) or {}
        extra = ((defaults.get("projects") or {}).get(row["project"]) or {}).get(
            "extra_pytest_args") or []
        entries.append({
            "project": row["project"], "version": row["version"],
            "candidate_wheel": row["candidate_wheel"]["filename"],
            "candidate_sha256": row["candidate_wheel"]["sha256"],
            "pytest_version": row["pytest_version"], "stage": row["lock"]["stage"],
            "lock_rows": len(row["lock"]["rows"]),
            "plugin_loads": plugin.get("loads") or [],
            "project_local_plugins": plugin.get("project_local_plugins") or [],
            "extra_pytest_args": extra,
            "attempts": 1, "a6_max_calls": 2, "a7_max_calls": 1,
            "a7_seconds_max": 60, "outer_timeout_seconds": 240,
            "source_restorations": 2,
        })
    document = {
        "schema_version": "pyhost-context/d-pre-run-commitment/1.0",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": "proposed_not_authorized",
        "locks_sha256": sha(LOCKS), "plugin_map_sha256": sha(PLUGINS),
        "validation_sha256": sha(VALIDATION),
        "validation_ok": validation["ok"],
        "entrypoint": {"path": "tools/run_d_admission.py",
                       "sha256": sha(HERE / "run_d_admission.py")},
        "plugin_defaults": defaults,
        "scope": {
            "projects": [entry["project"] for entry in entries],
            "excluded_static": ["argparse"],
            "named_gap": ["pylint: docopt 0.6.2 has no wheel; sdist builds forbidden"],
            "separate_from_s1a": True,
            "pool_order_family_cap_quotas_no_replacement": "unchanged",
        },
        "rules": [
            "每项仅一次 D 准入尝试；失败即登记，不重试、不换版本、不改需求、不换插件。",
            "A6≤2 次调用、A7 1 次、A7≤60 秒、外层≤240 秒、源码恢复 2 次。",
            "B0 三态与 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 不变；插件仅按本锁的 -p 列表显式加载。",
            "D 结果与 S1a 分表；不合并准入率，不把差异单独归因于 pytest 版本。",
            "任何锁、哈希、插件或版本与承诺不符即停止该项，不降级执行。",
        ],
        "entries": entries,
    }
    OUT_JSON.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    lines = ["# D 档一次性尝试预算与事前承诺（待 Reviewer 001 签署）", "",
             f"- 状态：**未授权**（{document['status']}）",
             f"- 锁集 SHA-256：`{document['locks_sha256']}`",
             f"- 插件映射 SHA-256：`{document['plugin_map_sha256']}`",
             f"- 锁校验：{'全部通过' if document['validation_ok'] else '存在失败'}",
             "",
             "| 项目 | 版本 | 阶段 | pytest | 分发 | 插件 | 尝试 | A6 | A7 | 60s | 240s |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for entry in entries:
        lines.append(
            f"| {entry['project']} | {entry['version']} | {entry['stage']} | "
            f"{entry['pytest_version']} | {entry['lock_rows']} | "
            f"{len(entry['plugin_loads'])} | 1 | 2 | 1 | 是 | 是 |")
    lines += ["", "## 停止与记录规则", ""]
    lines += [f"- {rule}" for rule in document["rules"]]
    lines += ["", "## 签署", "",
              "本文件由 ClawsGO Science Agent 生成，不代替 Reviewer 001 的签署；",
              "签署前不得安装、导入或执行任何候选。", ""]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"json": str(OUT_JSON), "md": str(OUT_MD),
                      "json_sha256": sha(OUT_JSON), "md_sha256": sha(OUT_MD),
                      "projects": len(entries)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
