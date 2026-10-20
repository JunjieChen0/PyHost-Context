# SQJ 投稿材料底稿（2026-10-20）

本件把作者提供的元数据落成投稿底稿：标题页、cover letter、声明与检查清单。
**尚未投稿，未运行任何新实验，也没有替代作者做任何确认。**

## 已确认

- 作者：**Junjie Chen**，**唯一作者兼通讯作者**
- 单位：**南京理工大学计算机科学与工程学院**
  （School of Computer Science and Engineering, Nanjing University of Science and Technology）
- 地址：**No. 200, Xiaolingwei Street, Xuanwu District, Nanjing, Jiangsu Province, China, 210094**
- 邮箱：**junjie_chen@njust.edu.cn**
- ORCID：**0009-0002-3499-3621**
- 基金：**无特定资助**
- 利益冲突：**无**
- 数据与代码：**Zenodo + GitHub**（GitHub 已上线：https://github.com/JunjieChen0/PyHost-Context；Zenodo DOI 待补）
- 预印本/他刊：**无预印本，未投他刊**
- 目标期刊：**Software Quality Journal**（Springer，hybrid），稿件类型 **Original Paper**
- 摘要 **150–250 词**、关键词 **4–6**、LaTeX 模板 **sn-jnl**

## 仍需确认

1. **Zenodo DOI**：投稿前必须填入。
2. 可选：电话、推荐审稿人。
3. **作者贡献（CRediT）**：草稿列出单作者角色，需你确认。

## 文件

| 路径 | 说明 |
|---|---|
| `AUTHOR_METADATA.json` | 作者元数据；逐项标注已确认/待确认 |
| `TITLE_PAGE_DRAFT.md` | 标题页草稿（工作标题、作者块、摘要/关键词占位） |
| `COVER_LETTER_DRAFT.md` | Cover letter 草稿；数字与限定语按 REV2 口径 |
| `DECLARATIONS_DRAFT.md` | 基金、利益冲突、数据/代码、贡献、伦理、AI 使用、预印本占位 |
| `SQJ_SUBMISSION_CHECKLIST.md` | A—H 八组投稿清单与状态 |
| `verify_submission_materials.py` | 只读结构与措辞检查 |

## 核验

```bash
python3 -B verify_submission_materials.py
```

当前 **108 项检查通过**：作者字段、标题页要素、cover letter 的关键数字与限定语、
声明占位、清单完整性、以及“无首创/无保证录用”等禁止措辞。

它只检查结构与措辞；不判断稿件质量、期刊适配或录用概率。

## 边界

- 没有向任何期刊提交，也没有联系编辑。
- 没有新实验；S2/S3/repro/S1b 重放/新样本仍未授权。
- 工作标题为暂定，可在英文稿完成后调整。
