# CCF-C 投稿定位档案 REV2（2026-10-20）

本档案落实 Reviewer 001 对 REV1 的限定接收
`REV1_ACCEPTED_WITH_REVIEWER_FACTUAL_ADDENDUM`
（复核包 `d835207e3c97f6001fef8a09324bc332e88e5ce013922a90139f9d9d4aa4e7d3`），
落实 D1—D3 三组事实补充，并保留 R1—R5、N1—N2 的既有更正。

**不新增实验、不重跑封存实验、不改写任何已签件。**
REV1 包（`bed0a011…`）、首版包（`1b33dd72…`）与全部实验原件保持原样。

## 文件

| 路径 | 说明 |
|---|---|
| `CCFC_POSITIONING_AND_EVIDENCE_AUDIT_REV2.md` | 主件：D1—D3 与 R1—R5/N1—N2 落点、期刊事实、文献增量、贡献、S2 后口径、选样偏差、冻结时序、审稿风险、英文贡献段 |
| `LITERATURE_MAP.csv` | 六篇最接近文献；恢复范围限定为“选定合格单位” |
| `CLAIM_LEDGER.json` | 十条主张（1.2 版）：措辞、分母、数字断言、允许/禁止表述 |
| `SELECTION_BIAS_FACTS.json` | 10 / 4 / 11 身份选择偏差口径、具名清单与裁定原件哈希 |
| `SOURCE_VERIFICATION.json` | 文献与期刊元数据、原源核验层级、逐条限制 |
| `EVIDENCE_HASHES.json` | 17 条证据路径的字节数与 SHA-256（由 `seal_evidence.py` 生成） |
| `verify_dossier.py` | 从两份签署快照推导数字，核对证据字节、主张措辞、10/4/11 口径与文献结构 |
| `selftest_checker.py` | 六个合成反例：空证据、伪造计数、正确新计数、旧 14 项口径、恢复范围删除、作者自检夸大 |
| `seal_evidence.py` | 把快照哈希与证据哈希封入档案 |
| `build_dossier_package.py` | 生成清单并打包单一 ZIP |
| `REV2_CORRECTION_MAP.md` | D1—D3 与 R1—R5、N1—N2 的逐项落点 |

## 核心口径（S2 后）

- 登记研究调用 **3,086** = 3,014 套件 + 72 见证；覆盖合计 **3,455**。
  3,056 / 2,984 / 3,425 只作为 **S2 前快照**出现。
- 30 个项目名 = 主队列 25（探索 18 + 验证 7）+ 补充 S1b 5；**240 是筛查单位**。
- 恢复块只对**选定合格单位**执行；两块分别执行，**不构成统计独立样本**。
- 主筛查 8 个键；`COLUMNS`、`TERM`、`NO_COLOR` 是阳性案例聚焦，不是全部干预；
  原生对照另测 ORDER/PARALLEL。
- 最高等级：**E4 4 / E3 1 / E2 3**，8 个单位各登记一次，不是机制数。
- 工件身份选择偏差按 **10 / 4 / 11**：10 个未入选候选有 A6 错误源码记录，
  4 项停在解析阶段，加 1 个入选项目共 11 项受限重验；15 个潜在名称遮蔽不等于
  15 次错误执行。
- S3 原批及 56 次纠错采集**先于** S2 的 30 轮；阶段编号不是实际时间顺序。

## 核验

```bash
python3 -B seal_evidence.py
python3 -B verify_dossier.py --summary
python3 -B selftest_checker.py
# 解压副本中工作区不在上层目录时：
python3 -B verify_dossier.py --workspace /path/to/project-20261013-1
python3 -B selftest_checker.py --workspace /path/to/project-20261013-1
```

**作者本机记录**：`verify_dossier.py` 通过（检查数见 `VERIFICATION_RESULT.json`）；
六个自测期望 A、B、D、E、F 被拒绝、C 被接受。

**复核方独立核验（2026-10-20）**：从已归档签署件验证两份快照绑定，**37 个数字**
推导一致；16 条引用证据中 **13 条**可字节验证，**3 条未随交付件提供**；
原样复跑作者脚本得到 **290 项检查、3 项缺件、退出码 1**。作者本机 296 项与自测
只保留为**作者记录**，未在本轮独立复现。

**校验器能证明什么**：登记哈希、数字推导、证据字节、措辞与 10/4/11 结构一致。
**不能证明什么**：它**不核验 Reviewer 签署件**；不能证明文献语义、绝对新颖性、
录用概率或人工外审。

## 边界

- 期刊事实：hybrid、Original Paper、sn-jnl、摘要 150–250 词、关键词 4–6；
  CCF C 仍为第三方来源，官方目录待核验。
- Reviewer 001 是 AI 审核角色，不是独立人工复核者或期刊外审；AI 使用按指南说明。
- 不认证投稿档次或录用概率；不写生态比例、功能缺陷或独立机制数。
- 无新执行授权；S2/S3/repro/S1b 重放/新样本均未新增授权。
