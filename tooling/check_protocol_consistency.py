"""ClawsGO Science Agent：核对最终矩阵协议草案的**文档与机器文件是否一致**。

动机：五轮独立审计反复指出"文档与实现/记录不一致"这类发布问题。协议草案有 Markdown 与 JSON
两份表述，必须逐项对齐，否则签署时会出现两个互相矛盾的来源。

只读，不修改任何文件。用法：``python3 check_protocol_consistency.py``
一致性不通过时以非零码退出。
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent  # pyhost-context/
DOC = ROOT / "最终矩阵协议冻结草案_v0.3待校正.md"
MACHINE = ROOT / "protocol_freeze_v0.3待校正.json"


def main():
    doc = DOC.read_text(encoding="utf-8")
    machine = json.loads(MACHINE.read_text(encoding="utf-8"))
    problems = []
    checked = []

    def check(label, condition, detail=""):
        checked.append(label)
        if not condition:
            problems.append(f"{label}: {detail}")

    # 样本：六个项目名必须同时出现在两份文件中
    for item in machine["final_sample"]["selected"]:
        name, version = item.rsplit("-", 1)
        check(f"sample:{item}", name in doc and version in doc,
              f"{item} 未在文档中找到")

    # P 与预算
    budget = machine["budget"]
    check("budget:P", str(budget["P"]) in doc, "P 未在文档中")
    check("budget:270", str(budget["core_suite_executions"]) in doc, "270 次未在文档中")
    check("budget:formula", budget["core_formula"] in doc, "预算公式未在文档中")

    # 八个条件 ID 与关键取值
    for condition in machine["core_conditions"]:
        check(f"condition:{condition['id']}", condition["id"] in doc,
              f"{condition['id']} 未在文档中")
        value = condition["b"].get("value")
        if value:
            check(f"condition:{condition['id']}:value", value in doc,
                  f"{condition['id']} 的扰动值 {value!r} 未在文档中")

    # 授权位：两份文件必须一致
    flags = machine["authorization"]
    for flag in ("admission_rules_frozen", "admission_phase_authorized", "admission_started",
                 "experiments_started", "final_matrix_frozen", "matrix_phase_authorized",
                 "matrix_started", "formal_protocol_frozen", "old_no_color_in_denominator"):
        expected = "true" if flags[flag] else "false"
        pattern = rf"{flag}\s*=\s*{expected}"
        check(f"flag:{flag}", re.search(pattern, doc) is not None,
              f"文档中未找到 {flag} = {expected}")

    # 未授权项不得被写成已授权（代码块内有对齐空格，用正则匹配）
    check("matrix_not_authorized_marker",
          re.search(r"matrix_phase_authorized\s*=\s*false", doc) is not None
          and "矩阵授权：**未授权**" in doc,
          "矩阵授权状态表述不一致")

    # 恢复块与 RQ3
    check("recovery:order", "A1→B→A2" in doc, "块内顺序未写明")
    check("recovery:max_blocks", str(machine["recovery"]["max_blocks_per_target"]) in doc,
          "每目标块数上限未写明")
    check("standardization:Z_equals_B0", "Z_equals_B0 = true" in doc or "`Z_equals_B0 = true`" in doc,
          "Z 等于 B0 未写明")
    check("standardization:safe_elimination_disabled",
          "禁用" in doc and "safe_elimination" in doc, "safe_elimination 禁用未写明")

    # 待决事项：ID 与标题都须出现在文档中，且不得被标为已决
    for decision in machine["pending_user_decisions"]:
        check(f"pending:{decision['id']}:marker", f"[{decision['id']}]" in doc,
              f"{decision['id']} 未在文档中标注")
        check(f"pending:{decision['id']}:title", decision["title"] in doc,
              f"{decision['id']} 的标题与文档不一致")
        check(f"pending:{decision['id']}:open", decision["decided"] is False,
              f"{decision['id']} 被标为已决，但未经用户决定")

    # 网络与状态控制的适用性声明必须存在
    check("network:scope_statement", "被覆盖的 Python 调用范围内" in doc,
          "网络范围限定语缺失")
    check("state:applicability_caveat", "尚未直接测量" in doc,
          "修订执行器影响未直接测量的声明缺失")

    result = {"doc": DOC.name, "machine": MACHINE.name, "checked": len(checked),
              "problems": problems, "consistent": not problems}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
