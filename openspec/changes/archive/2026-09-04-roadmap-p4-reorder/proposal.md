# Proposal: roadmap-p4-reorder

## Why

`docs/features/feature-catalog.md`（389 项；P1–P5）与 `docs/features/roadmap.md`（Sprint 1–10）
经过 2026-08-22 reclassification 后，P4 池被一次性吸收了 48 项 P3 延期项，但 Sprint 8 的**显式章节**
仍以「Self-Learning / Agentic RL / Ontology Actions」三块 L 级深度项领头；其中 6.20 / 6.22
自身就标注 *P5 deferred, rebase when triggered*——意味着 Sprint 8 里程碑里至少两项验收
无法在 Sprint 8 内关闭，是 roadmap 自身的内部矛盾。

同时存在三个与 AgentArts 对比无关、靠 grep 就能发现的结构性缺口：

1. **Agent 版本化是规划外空白**——5.9d（Skill Versioning）和 3.5.12（Ontology Versioning）都
   引用「Resource Versioning (14.x) mechanism」，但 catalog 中 `14.x` 仅包含 14.1（Agentic
   Resource Discovery）和 14.2（Webhook Callbacks），**这个被两处对齐、被 7.5 A/B Testing 假设
   的机制没有承载条目**。
2. **1.3.10 ⊕ 6.23 重复**——两行核心同构（atomic → workflow → session 三层意图），6.23 仅多
   caching/自进化两个增强件，是 catalog 卫生问题。
3. **编排页产品包装弱于数据层**——`AgentCreateSchema` 字段已含开场白 / 推荐问题 / guardrail，
   模板创建已交付（5 个内置 + instantiate API + TemplatePicker），但前端编排页未做一体化
   重组，体验与 AgentArts 差距集中在 UI 而非能力。

本 change 仅做文档与目录项调整（pool 不动，方向不动），为 Sprint 8 给出可关闭的队首 + 补齐
悬空机制 + 合并重复条目；后续每个新增条目（1.3.20 等）的实施在独立 change 中。

## What Changes

**A. feature-catalog.md**
- A1. 新增 `1.3.20 Agent Versioning & Channel Publishing` 条目（实现 = 14.x Resource
  Versioning 的具体化；可被 5.9d / 3.5.12 / 7.5 直接复用）。
- A2. 在 1.3.10 与 6.23 行尾分别加「⊕ duplicate; see merged target」指针，建议合并到 6.23
  （保留 6.23 的 caching/自进化件，1.3.10 的 LLM 依赖作为基础项入档）。
- A3. 在 5.9d 与 3.5.12 的「Aligns with Resource Versioning (14.x)」引用旁加注：
  `see 1.3.20`。
- A4. 在 catalog 头部「Deferred from P3」或对应位置加一节「P4 Sprint 8 Opening Queue」列表
  （与 roadmap.md 同步），不动 P4 池子，只标注开局的执行序。
- A5. *(2026-09-06 追加，源自 settings/models × AgentArts 开发配置对比)* 新增 `6.47 Model
  Service Publishing`（把已交付的 6.45 staging/promotion 机制接入 settings/models 管理面：
  model_registry 发布状态 draft→testing→published，未发布模型从应用引用面 /v1/models 隐藏）
  与 `6.48 Model Management Quick Wins`（列表搜索过滤 + 供应商调用次数接入，纯前端 + 聚合
  查询，搭车项）；6.13 行加防重复建设注记；P4/Total 统计行同步 148→150、389→391。
- A6. *(2026-09-06 追加，源自开发配置其余 5 子模块实测)* 新增 `6.49 Intent Package Asset`
  （意图包 = 意图分类 + 样例 utterance 资产，批量录入/导入导出，供 6.23/2.6a 意图识别做
  few-shot 证据；6.23 是引擎、6.49 是数据面）；4 处注记：6.14（路由策略作为可命名可绑定
  实体：模型组+总超时+重试次数，agent 模型配置选策略而非单模型）、6.7（双模型并排对比
  调测，纯前端；类型筛选已由 6.11 ✅ 覆盖）、14.2（工作流异常通知消息模板资产化，低优按需）、
  1.1.26（AgentArts 对象模板 = 轻量 schema 无 KG 依赖，建议 Phase 1 轻量提取 + Phase 2
  KG CRUD）；P4/Total 统计同步 150→151、391→392。

**B. roadmap.md**
- B1. Sprint 8 章节改写为「Opening Queue + Absorption Pool」两层结构：
  - Opening Queue：5.4a MCP Gateway / 7.2x 评估族 / 2.6a+1.1.21+1.3.10⊕6.23 控制器族 /
    1.3.20 Agent 版本化（新增）——按市场验证+底座✅ 排序；
  - Absorption Pool：原 Sprint 8 三块（Self-Learning / Agentic AI / Memory Intelligence）
    按原章节列出，标注「可在 Opening Queue 进展后择机启动」。
- B2. Sprint 9 中 2.6a / 1.1.21 移除（已迁 Sprint 8）；其余保持。
- B3. M8 里程碑增补一行诚实标注：6.20 / 6.22 验收条件依赖 P5 KG integration 触发，按触发
  条件回填；里程碑本身**不**删除这两项（与计划一致），但说明其 closure 不阻塞 Sprint 8
  其他交付。
- B4. 在「Critical Path Analysis」段补注 1.3.20 是 P4 内三处（5.9d / 3.5.12 / 7.5）的
  共同前置。
- B5. *(2026-09-06 追加)* Sprint 8 Absorption Pool 末尾新增「Model Management」小节
  （6.47/6.48，发布语义与 Opening Queue 的 1.3.20 成对）；M8 验收清单与 M9 里程碑计数
  （96/96→98/98）、Milestone Summary、roadmap 统计表（P4 148→150）、Critical Path 追加
  6.45→6.47 与 traces→6.48 两条依赖链同步更新。
- B6. *(2026-09-06 追加)* Opening Queue 控制器族行扩为 2.6a+1.1.21+1.3.10⊕6.23(+6.49)；
  M8/M9 验收与计数同步（98/98→99/99，103 features）；统计表 P4 150→151；Critical Path
  追加 6.23→6.49 依赖链。

**C. docs/research/agentarts-comparison.md**（新建）——把
`docs/research/2026-09-agentarts-product-comparison.md` 中可公开的部分沉淀为正式 research
note；gitignored 原始对比稿保留为个人草稿（实现者参考）。本次 change **不**做此项
（research 文件本就不入库；待用户后续指令决定是否要派生正式 research doc）。

## Capabilities

### New Capabilities

（无）本 change 不引入新的产品能力，仅目录与排期文档调整。

### Modified Capabilities

（无）本 change 不修改产品级行为。

> 因 `.openspec.yaml` 设置 `skip_specs: true`，本 change 不创建 `specs/` 下的 delta 文件。

## Impact

- **Affected files**：`docs/features/feature-catalog.md`、`docs/features/roadmap.md`。
- **Affected change scope**：仅文档；不改 Python / TypeScript / SQL / Docker / pyproject。
- **External deps**：无。
- **下游 change**：每个被显式化的 Opening Queue 项（5.4a / 7.2x / 2.6a+1.1.21 / 1.3.20）
  在后续 change 中按本次 roadmap 排期分别实施；本 change 不实施任何条目。
- **Verification gate**：
  - `openspec validate --change roadmap-p4-reorder` 必须通过；
  - `git grep "1.3.20"` 命中至少 3 处（catalog 新条目 + 5.9d/3.5.12 注 + roadmap 引用）；
  - `git grep "⊕"` 在 1.3.10 / 6.23 两行尾命中；
  - `git grep "P5 KG"` 在 6.20 / 6.22 / M8 段落命中。
- **Rollback**：纯 git revert，无运行时回滚成本。
