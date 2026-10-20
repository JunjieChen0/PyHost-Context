"""ClawsGO Science Agent：生成 D 档逐项差异与一次性尝试预算表。

对照 S1a 逐项记录与 D 锁/插件映射，只陈述可核对的字段差异；
不做准入率合并，不把差异归因为 pytest 版本。
"""
import argparse
import csv
import hashlib
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
STATE = NEXT / "state" / "d_toolchain"
LOCKS = STATE / "D_RESOLUTION_LOCKS_REV1.json"
PLUGINS = STATE / "D_PLUGIN_MAP_REV1.json"
S1A = NEXT / "S1A_ADMISSION_PER_CANDIDATE_20261018.csv"
OUT_MD = NEXT / "D_PROJECT_DIFFERENCES_REV1_20261018.md"
OUT_JSON = NEXT / "D_PROJECT_DIFFERENCES_REV1_20261018.json"
ORDER = ["ipython", "structlog", "rich-toolkit", "pylint", "twine",
         "rich-click", "argparse", "hatch", "pdm"]


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--locks", type=pathlib.Path, default=LOCKS)
    parser.add_argument("--plugins", type=pathlib.Path, default=PLUGINS)
    args = parser.parse_args()
    locks = json.loads(args.locks.read_text(encoding="utf-8"))
    plugins = json.loads(args.plugins.read_text(encoding="utf-8"))
    plugin_index = {p["project"]: p for p in plugins["projects"]}
    s1a = {row["project"]: row for row in
           csv.DictReader(S1A.open(encoding="utf-8"))}
    rows = []
    for project in locks["projects"]:
        name = project["project"]
        if name not in ORDER:
            continue
        s1 = s1a.get(name) or {}
        locked = project.get("status") == "locked"
        lock = project.get("lock") or {}
        pl = plugin_index.get(name) or {}
        rows.append({
            "project": name, "version": project.get("version"),
            "position": s1.get("position"), "group": s1.get("group"),
            "family": s1.get("family"),
            "s1a_class": s1.get("technical_class"),
            "s1a_refusals": s1.get("refusals"),
            "s1a_calls": s1.get("calls"),
            "d_status": project.get("status"),
            "d_stage": lock.get("stage"),
            "d_pytest": project.get("pytest_version"),
            "d_roots": project.get("root_count"),
            "d_packages": len(lock.get("rows") or []),
            "d_local": sum(1 for r in lock.get("rows") or []
                           if r.get("source") == "local"),
            "d_plugin_loads": pl.get("loads") or [],
            "d_plugin_count": len(pl.get("loads") or []),
            "d_plugin_conflicts": pl.get("conflicts") or [],
            "d_project_local_plugins": pl.get("project_local_plugins") or [],
            "derived_constraints": project.get("derived_constraints") or [],
            "candidate": (project.get("candidate_wheel") or {}).get("filename"),
            "pytest_swap": (None if not locked or not s1 else
                            f"7.2.0 -> {project.get('pytest_version')}"),
        })
    document = {"schema_version": "pyhost-context/d-project-differences/1.0",
                "locks_sha256": sha(args.locks), "plugin_map_sha256": sha(args.plugins),
                "s1a_csv_sha256": sha(S1A), "rows": rows}
    OUT_JSON.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    lines = [
        "# D 档逐项差异、一次性预算与事前说明", "",
        "本表只列 S1a 记录与 D 准备结果之间的可核对差异。D 档结果另表报告，",
        "不与 S1a 的 0/9、2/16 合并，也不把差异单独归因于 pytest 版本。", "",
        "| 位次 | 项目 | 组 | S1a 判定 | D 锁 | 阶段 | pytest | 根数 | 锁内分发 | 本地字节 | 显式插件 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append("| {position} | {project} {version} | {group} | {s1a_class} | "
                     "{d_status} | {d_stage} | {d_pytest} | {d_roots} | {d_packages} | "
                     "{d_local} | {d_plugin_count} |".format(**{
                         key: ("" if value is None else value) for key, value in row.items()}))
    lines += ["", "## 逐项说明", ""]
    for row in rows:
        if row["d_status"] == "locked":
            if str(row["d_pytest"]) == "7.2.0":
                pytest_line = "pytest 维持 7.2.0（R0）"
            else:
                pytest_line = f"pytest 由 7.2.0 改为 {row['d_pytest']}（R1）"
            lines.append(
                f"- **{row['project']} {row['version']}**：S1a 为 `{row['s1a_class']}`"
                f"（{row['s1a_calls']} 次调用）；D 在 {row['d_stage']} 下锁定 "
                f"{row['d_packages']} 个分发（{row['d_local']} 个使用本地既有字节），"
                f"{pytest_line}，显式加载 "
                f"{row['d_plugin_count']} 个 pytest11 入口"
                + (f"，项目自带插件 {row['d_project_local_plugins']}" if row["d_project_local_plugins"] else "")
                + "。测试选择参数仍取自冻结配置，未新增/删减测试。"
                + (f" 派生约束：{', '.join(item['requirement'] for item in row['derived_constraints'])}。"
                   if row["derived_constraints"] else ""))
        elif row["d_status"] == "resolution_failed":
            lines.append(
                f"- **{row['project']} {row['version']}**：具名工件缺口。R1 联合解析失败；"
                f"声明链中的 `tbump` 需要 `docopt>=0.6.2,<0.7.0`，官方 PyPI 仅有 "
                f"`docopt-0.6.2.tar.gz`（sdist），无兼容 wheel；本档禁止源码构建，"
                f"故不产出锁，也不以改需求或换版本绕过。")
        elif row["d_status"] == "static_excluded":
            lines.append(
                f"- **{row['project']} {row['version']}**：沿用 Reviewer 001 的静态排除，"
                f"不补位、不计运行阴性。")
        else:
            lines.append(f"- **{row['project']} {row['version']}**：{row['d_status']}。")
    lines += [
        "", "## 一次性尝试预算（待授权，不是已执行）", "",
        "| 项目 | 尝试次数 | A6 调用上限 | A7 调用上限 | A7 秒上限 | 外层超时 | 源码恢复 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        if row["d_status"] == "locked":
            lines.append(f"| {row['project']} | 1 | 2 | 1 | 60 | 240 | 2 |")
    lines += [
        "",
        "- 每项仅一次 D 准入尝试；失败即记录拒绝类别，不重试、不换版本、不换插件。",
        "- 家族上限 4、原名次、配额与不补位规则不变；D 结果与 S1a 分表。",
        "- 解析、元数据查询与工件获取的成本单列，不计入 A6/A7 或扰动轮。",
        "- 取得本轮字节后集中审签 D 准入；未获签署前不安装、不导入、不执行候选。",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"md": str(OUT_MD), "json": str(OUT_JSON),
                      "md_sha256": sha(OUT_MD), "json_sha256": sha(OUT_JSON),
                      "rows": len(rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
