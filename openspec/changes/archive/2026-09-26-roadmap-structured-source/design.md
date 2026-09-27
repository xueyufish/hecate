# Design: roadmap-structured-source

## Context

- **目录结构**：`feature-catalog.md` 1069 行。行格式：`| <id> | <名称>[✅] | <分类> | <描述（含 Planned enhancement / Status / Trigger 注记）> | <竞品列> |`。✅ 标在名称列内。汇总表（行 13-17）每阶段一行：`| **P4 Intelligent** | 154 | <特性名列表> | Months 15-18 | 47/154 (31%) |`。已有再分类注记先例（2026-08-22 P3→P4 迁移 48 项、2026-08-12 P5 deferred 均以"Status (date)"行内注记 + 汇总表注释落地，未删行）。
- **#15 七项落点**：6.15（行 641，P4 正文）、2.12（行 938）、6.38（行 957）、6.39（行 958）、6.20 Ontology Actions（行 643）+ 6.22 OAG（行 644）——"完整 Ontology/OAG 产品线"指二者的闭环组合（6.20 动作执行 + 6.22 写回，单独的轻量 schema 用途如 1.1.26 注记不受影响）；Global Branching / Embedded Ontology 为 P5 汇总行内清单项（正文行在 P5 区段，apply 时定位）。
- **CI**：`docs-check.yml` 与 `ci.yml`、`pr-lint.yml` 并存；docs-check 是文档检查的天然宿主。
- **YAML 依赖**：解析/校验用 Python 标准库（re/json）+ PyYAML——dev 依赖里需确认 PyYAML 可用，否则 YAML 改为 JSON 或手写受限解析（实现时定，倾向 PyYAML 入 dev extra）。

## Goals / Non-Goals

**Goals:**

- YAML 状态源覆盖全部 id（脚本提取）+ 六项拉前主线的 dependencies/evidence/acceptance + 七项降级的 research-candidate 标记。
- `check` 子命令五类校验（重复 ID / 失效依赖 / 循环依赖 / production 缺 evidence / YAML↔目录 ID 集合漂移）进 CI。
- 七项入研究候选池（行内标记 + 池区段 + 重启条件）；Next Phase 主线区段落地。
- 汇总表计数与池标记一致（沿用再分类注记先例，不删行）。

**Non-Goals:**

- 不把 390 项的描述文本迁入 YAML——YAML 只拥有**状态与治理字段**，markdown 保留"这个特性是什么"的人读职责（避免大规模有损迁移）。
- 本期不强制全部条目填 evidence/acceptance（390 项回填是数周工作）——check 对缺失先警告；强制化（警告→失败）作为独立后续 PR，在 tasks 里留占位。
- 不重写 roadmap.md 历史区段/旧时间线（评审点名的"旧统计并存"以 Next Phase 区段 + 顶部指向声明收敛，历史注记保留为审计记录）。
- 不动 6.20/6.22/1.1.26 的轻量 schema 用途（Ontology 降级仅指完整产品线闭环）。
- 不建 web 看板/生成 HTML——生成物就是校验通过的 YAML + markdown 区段。

## Decisions

### D1: YAML 拥有治理字段，markdown 拥有内容

字段：`id`、`title`（提取自目录，仅作索引）、`phase`（P1-P5）、`category`、`status`（planned/delivered/research-candidate/dropped）、`maturity`（experimental/beta/production，仅 delivered）、`dependencies`（id 列表）、`evidence`（change 名或测试路径）、`acceptance`（验收要点）。理由：390 项描述的 YAML 化是有损大迁移且双写必漂移；治理字段机器可查才是 #13 的验收本体。备选（否决）：YAML 全面替代目录——阅读体验断裂且迁移成本失控。

### D2: `extract` 以正则解析目录表格行，fail-loud

行正则 `^\| (\S+) \| (.+?) \| (.+?) \|`（id 含 `1.3.21④`/`11.9 (D/T)` 等形态 → id 列取首段空白前 token）；✅ 检测 `✅` 在名称列。解析失败的行**报错并列出**（不静默跳过），保证提取器与目录格式强耦合——目录格式漂移立即被 CI 发现，这正是 #13 想要的约束方向。

### D3: `check` 五类校验 + 两档严格度

`--strict` 前：重复 ID、失效依赖、循环依赖（dependencies 图 DFS）、production 缺 evidence、YAML↔目录 ID 集合漂移 = **ERROR**；evidence/acceptance 缺失（非拉前/降级项）= WARNING。CI 先以非严格模式跑（警告不红）；回填完成后一个后续 PR 翻 `--strict`。

### D4: #15 池标记 = 行内注记 + 池区段，不删行

七项目录行尾加 `**🔬 Research Candidate (2026-09-26)**: 移入研究候选池（见 Research Candidate Pool 区段）。重启条件见池区段。`；池区段（目录尾部新 `## Research Candidate Pool`）逐项列重启条件四要素；roadmap.md 同步池指引。汇总表：P4 行清单去掉 6.15、P5 行清单去掉 AP2/Self-Planning/Tool Auto-Creation/Global Branching/Embedded Ontology/Ontology 闭环表述，计数调整并在表下加一行再分类注记（对齐 2026-08-22 先例格式）。

### D5: Next Phase 区段 = 手写内容 + YAML dependencies 兜底

区段列六项（11.16→11.17→13.1b→13.17→8.10→9.16a）各一行：目标、复用资产（evaluation/versioning/approval 既有能力）、依赖 id。YAML 中这六项的 `dependencies` 字段编码同序链；`check` 校验链完整。区段顶部声明"本区段由 feature-inventory.yaml 治理字段驱动；历史 P1-P5 表保留为审计记录"。

### D6: CI 挂 docs-check.yml（不进 ci.yml）

ci.yml 是安装全工作区 + 全量测试的重 job；inventory check 只需 Python 标准库 + PyYAML。docs-check.yml 已是文档检查宿主，加一步 `python scripts/feature_inventory.py check`（uv 环境复用或独立 setup-python，实现时按 docs-check 现状定）。

## Risks / Trade-offs

- [目录行格式多样性] id 列含空格形态（`11.9 (D/T)`、`11.2 (full)`）与子 ID（`1.3.21④`、`3.5.4`）→ 正则取首 token + 失败行报错清单，人工核对一次后固化。
- [汇总表手工同步漂移] 池标记改动后汇总表靠手工调整 → check 增加"汇总表计数 vs 区段行数"校验作为 WARNING（本期），后续可升 ERROR。
- [PyYAML 依赖] dev extra 需含 PyYAML（大概率已有传递依赖）→ 实现时确认，缺失则入 dev extra。
- [七项降级的产品争论] 相关方可能有异议 → 池区段明示重启条件与流程，PR review 即调整窗口。

## Migration Plan

1. 合入后 CI 即执行 check（非严格模式）。
2. 390 项 evidence/acceptance 回填按阶段推进（先拉前主线六项与池七项，已在本 change 填写）。
3. 回填完成 → 后续 PR 翻 `--strict`。
4. 回滚 = revert（无代码、无数据）。

## Open Questions

无——#15 清单按评审建议执行、可在 PR review 调整（用户已确认默认）。
