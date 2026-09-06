# Design: roadmap-p4-reorder

## Context

- `docs/features/feature-catalog.md` 当前 389 项（195 ✅，P1 19 + P2 65 + P3 87 + P4 148（25 ✅）+ P5 71（0））。
- `docs/features/roadmap.md` 已按 2026-08-22 reclassification 把 P4 拆到 Sprint 8/9，Sprint 10 = P5。
- 本 change 是纯文档调整：不改 Python / TS / SQL / pyproject；不改 P4 池子构成（条目数 + 完成态 + 优先级都不动）；不改任何 .py / .ts 接口；不改 CI / pre-commit / pre-push。

约束（按仓库既定规范）：
- 文档类 change 用 OpenSpec 流程时显式设 `skip_specs: true`（已在 `.openspec.yaml` 中设置）。
- 不创建新 P4 条目编号体系外的「未编号纯前端搭车项」——若需前端搭车，挂在某 Opening Queue 条目下作为子任务。
- catalog 表格格式严格保持 `<编号> | <标题> | <分类> | <Description> | <References>` 五列 + `| P4/P5/... |` 段落归属，markdown 表格不破列。
- roadmap Sprint 章节标题与 M8/M9/M10 里程碑列表的编号、格式风格保持一致。

## Goals / Non-Goals

**Goals：**
- Sprint 8 给出可关闭的开局队列（Opening Queue）：4 项均底座 ✅、纯产品化或 P 级缺口修复，无 L 级被 P5 依赖阻塞的项占首位。
- 补齐 catalog 中悬空的「Resource Versioning (14.x)」承载体（新增 1.3.20）。
- 标记并解决 1.3.10 ⊕ 6.23 重复条目。
- M8 里程碑对 6.20 / 6.22 增加诚实标注（依赖 P5 KG 触发），不删除验收项。
- 每个 Opening Queue 条目在 catalog 与 roadmap 间交叉引用，便于后续 change 实施时直接定位。

**Non-Goals：**
- 不实施任何 P4 条目的代码改动（每个 Opening Queue 项的实施在后续独立 change）。
- 不改 catalog 总体统计（195/389 维持）。
- 不动 P1/P2/P3（已完成的 195 项一律不动）。
- 不改 ADR（1.3.20 的新条目暂不要求新建 ADR——它是 1.1.9 workflow versioning 机制在 agent 维度的延伸，等实施时再决定是否补 ADR）。
- 不修改 `docs/research/2026-09-agentarts-product-comparison.md`（gitignored 个人草稿）。
- 不修改 `.github/`、`.zcode/`、`.agents/`、`scripts/`、`.envrc`、`.gitignore`。
- 不动 P5 池（ADR-029 冷冻规则保留，Opening Queue 不从 P5 借条目）。

## Decisions

### D1. 1.3.20 Agent Versioning & Channel Publishing 编号与定位

**选 1.3.20 而非另开 14.3 章节**：catalog 中 `1.3.x` 是 Agent Runtime 子族，1.3.10–1.3.19 均为运行时机制（意图识别、Hook 子类、Self-Learning 子项），1.3.20 接续最自然；`14.x` 现有 14.1（ARD）/ 14.2（Webhook）均为生态/运营面，跳到 14.3 会切断语义关联。

**复用 1.1.9 workflow versioning 机制**：1.1.9 已交付（workflow.version 字段 + 版本化发布 + 渠道绑定），1.3.20 = 同一机制在 agent 维度的实例化。避免重复设计存储/版本号格式。

**dependsOn 标注**：依赖 `1.1.9 ✅` + `channels 体系（channel/api/v1/ + channel/slack/ + channel/feishu/ + channel/embed/）✅`。

### D2. 1.3.10 ⊕ 6.23 合并方向：保留 6.23，吸收 1.3.10 基础项

保留 6.23 的原因：
- 6.23 描述包含 1.3.10 全部三层意图 + caching + 控制器自进化，**超集**；
- 6.23 描述中标明 `2.6a` 是其前置，合并后 controller family 在 Sprint 8 一处；
- 1.3.10 没有任何独占信息。

操作：在两行表格尾各加一行内指针（不新增列，不破坏 markdown）：
- 1.3.10 行尾 Description 末追加 `⊕ Duplicate with 6.23; superseded by 6.23 (see Sprint 8 Opening Queue 2.6a+1.1.21 family).`
- 6.23 行 Description 中「Layered recognition with caching/self-evolution」前置补「Merges 1.3.10 (multi-level intent recognition).」

### D3. 5.9d / 3.5.12 悬空引用修正

行内文本追加指针：`(see 1.3.20 Resource Versioning carrier)`。不重写整行 Description。

### D4. Sprint 8 章节重组为 Opening Queue + Absorption Pool 两层

现有 Sprint 8（roadmap.md:662–718）三块 Self-Learning / Agentic AI / Memory Intelligence 不删，迁入 **Absorption Pool** 子节，**保持原表格原编号原依赖**。

Opening Queue 子节放 4 项：
- 5.4a MCP Gateway（单条）
- 7.2b–e + 7.3 + 7.4 + 7.4a 评估任务族（合并 1 行表格 + 子表说明）
- 2.6a + 1.1.21 + 1.3.10⊕6.23 控制器族（合并 1 行表格 + 子表说明）
- 1.3.20 Agent Versioning（单条）

排序依据：底座 ✅ 程度 + 与 AgentArts 等商业产品对齐的市场验证信号（5.4a 网关独立产品卡、评估族完成度最高、控制器范式收敛、Agent 版本化是唯一规划空白）。

### D5. M8 里程碑诚实标注

不删 `Ontology Action System with writeback` 与 `OAG complete` 两项（与 catalog 一致），但在 M8 段落开头加一行：

> M8 内 6.20 / 6.22 的 closure 条件 = P5 KG integration 触发；当前 Sprint 8 进度以「机制就绪、6.20 占位加载」为可交付，closure 转移至触发节点。这不阻塞 Sprint 8 其他 Opening Queue 项交付。

### D6. Critical Path Analysis 补充

在现有 P4 critical path 段（roadmap.md:1052+）末尾追加 1 行：

> 1.3.20 是 5.9d / 3.5.12 / 7.5 三条引用链的共同前置——Sprint 8 实施时同步评估它们的解锁。

### D7. 不做的事（防止 scope creep）

- 不补 P5 条目；
- 不动 ADR 文件；
- 不重写 catalog 头部统计段；
- 不增加新 capability 章节（skip_specs 已声明）；
- 不修任何 P3 阶段历史延期项（保留 2026-08-22 注释原文）。

## Risks / Trade-offs

- **R1: 改 Sprint 8 章节可能误导后续 change 实施者把 Opening Queue 当成「必做四件」** → 在 Opening Queue 段开头明确「顺序优先非强制；任何 change 仍按 `/opsx:propose` 独立审批」。
- **R2: 1.3.20 编号固定后若实施时发现更适合 14.x 章节** → 后续 change 可用 ADR 调整（catalog 编号在内部约定下可重排，roadmap 引用的编号同步更新）。
- **R3: 1.3.10 ⊕ 6.23 合并是文档标记合并，不是代码合并** → 实施 change 中再统一 grep 两编号代码入口并实际去重。
- **R4: docs/research/2026-09-agentarts-product-comparison.md 是个人草稿（gitignored），未被本 change 引用** → 不构成耦合，但 reviewer 可能问「结论从哪来」——回答：「见 gitignored 个人研究笔记，本 change 不要求入库」。

## Migration Plan

无运行时回滚（纯文档）。Git revert 即回滚。

部署：merge to main → AGENTS.md 已在 §「OpenSpec workflow」中要求 docs 类 change 也走完整流程，已合规。

## Open Questions

无（关键设计点均已在 Decisions 中给出选项与理由；任何后续变体在实施 change 中再细化）。
