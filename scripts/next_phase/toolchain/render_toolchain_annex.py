"""ClawsGO Science Agent：把声明工具链 JSON 渲染为提案附件（Markdown 表）。

只读；不执行候选、不安装、不联网。
"""
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
NEXT = HERE.parent
SOURCE = NEXT / "DECLARED_TOOLCHAINS_20261018.json"
OUT = NEXT / "TOOLCHAIN_PROPOSAL_ANNEX_20261018.md"

STATUS_LABEL = {
    "pinned_from_existing_wheel": "既有 wheel 可钉",
    "source_artifact_only": "仅既有 sdist",
    "no_local_artifact": "无既有工件",
    "local_wheels_do_not_satisfy": "既有 wheel 不满足",
}


def main():
    doc = json.loads(SOURCE.read_text(encoding="utf-8"))
    lines = ["# 附件：九项机制候选的声明工具链静态清单", "",
             "本附件由 `analyze_declared_toolchains.py` 从冻结 sdist 机械生成。"
             "“既有 wheel 可钉”只表示本机既有工件中存在满足声明约束的 wheel，"
             "不表示已安装或已验证兼容。", ""]
    for project in doc["projects"]:
        lines += [f"## {project['project']} {project['version']}", ""]
        lines.append(f"- sdist：`{project['sdist']['filename']}`，"
                     f"{project['sdist']['observed_bytes']} 字节，"
                     f"SHA-256 `{project['sdist']['observed_sha256']}`（与请求一致："
                     f"{'是' if project['sdist']['match'] else '否'}）")
        lines.append(f"- `Requires-Python`：{project['requires_python'] or '未声明'}")
        channels = sorted({d["channel"] for d in project["declared_test_dependencies"]})
        lines.append(f"- 声明渠道：{', '.join(channels) if channels else '无'}")
        if project["dependency_groups"]:
            lines.append(f"- 依赖组：{', '.join(sorted(project['dependency_groups']))}")
        if project["group_includes"]:
            includes = "; ".join(f"{k} ⊃ {', '.join(v)}"
                                 for k, v in sorted(project["group_includes"].items()) if v)
            if includes:
                lines.append(f"- 组包含：{includes}")
        if project.get("self_extra_expansions"):
            for item in project["self_extra_expansions"]:
                lines.append(f"- 自引用展开：`{item['reference']}` → "
                             f"{', '.join(item['expanded']) or '（空）'}")
        lines.append(f"- pytest 声明：{project['declared_pytest_specifier'] or '未声明版本'}"
                     f"；本地 wheel：{', '.join(project['pytest_local_wheels']) or '无'}"
                     f"；按声明可钉：**{project['proposed_pytest_pin'] or '无'}**")
        if project["required_plugins"]:
            plugin_bits = []
            for row in project["required_plugins"]:
                entries = project["plugin_entry_points"].get(row["plugin"], {})
                names = ", ".join(sorted(entries)) if entries else "入口点未核实"
                plugin_bits.append(f"{row['plugin']}（{names}）")
            lines.append(f"- 必需插件：{'；'.join(plugin_bits)}")
        if project["local_plugins"]:
            lines.append(f"- 项目自带插件（addopts `-p`）：{', '.join(project['local_plugins'])}")
        rows = project["pin_table"]
        if rows:
            lines += ["", "| 声明依赖 | 约束 | 作用域 | 既有 wheel | 状态 | 可钉版本 |",
                      "|---|---|---|---|---|---|"]
            for row in rows:
                scopes = ",".join(row["scopes"])
                lines.append(
                    f"| `{row['name']}` | {'; '.join(s or '未限版本' for s in row['specifiers'])} | "
                    f"{scopes} | {', '.join(row['local_wheel_versions']) or '—'} | "
                    f"{STATUS_LABEL.get(row['status'], row['status'])} | "
                    f"{row['proposed_exact_pin'] or '—'} |")
        else:
            lines += ["", "（sdist 内没有可解析的测试依赖声明。）"]
        if project["classification"]:
            lines += ["", f"- 静态分类：{', '.join(project['classification'])}"]
        lines.append("")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT), "bytes": OUT.stat().st_size}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
