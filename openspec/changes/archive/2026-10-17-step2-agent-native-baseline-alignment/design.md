# Design

## Context

演进方案（#265）已固定 agent-native 目标架构，但现行 ADR、规范与规划数据仍指向"收敛到 Pregel + 外部 Runtime 认证"的旧方向。本 change 是纯文档/规范/规划数据对齐，不触碰运行时代码。受影响的事实基线（已核对 `origin/main` @ `1017abdf`）：

- `packages/hecate-runtime/src/hecate_runtime/execution_service.py` 的 `RuntimeExecutionRequest.runtime: PregelRuntime`——共享执行服务唯一绑定图执行，是待解除的实现现状；
- ADR-036（#264 刚更新过，活跃决策）Decision 段仍选定 Pregel 图为聊天收敛候选并禁止提取 Harness；其 Status/Consequences 段记录的双路径停止语义、持久审批续跑与真实模型基线证据仍然有效；
- `openspec/specs/unified-chat-execution/spec.md` Purpose 为"收敛到统一 Pregel 运行时"，5 个 requirement 全部以该开关/引擎路径为契约；
- `openspec/specs/dynamic-orchestration/spec.md` 记录 DYNAMIC/COORDINATOR 模式，计划历史仅存于 channel 与事件；
- `docs/features/roadmap.md` 与 `docs/features/feature-catalog.md` 的迭代/里程碑/contract-registry 区域均为 `feature-inventory:managed` 生成区，数据源在 `scripts/feature_inventory_tables.yaml`（I-D 行、M-A 行、contract_registry 的 "frozen at step8"）与 `scripts/feature_inventory_plan.yaml`（review-01 的 `step: 5/8/19` 与 milestone 表述）；
- `feature-inventory.yaml` 拥有 status/maturity/evidence 等治理字段（手工维护），`feature-catalog.md` 拥有 title/phase/category；生成器为 `scripts/feature_inventory.py`（extract/check/sync 三个子命令）。

## Goals / Non-Goals

**Goals:**

- 用一份新 ADR（ADR-037）固定六类执行职责（Harness、Agent Controller、动态 Plan、Workflow Executor/Pregel、共享持久化执行机制、可信动作执行），并对旧 ADR 给出精确到决策点的 supersedes 映射；
- 让 positioning/architecture/engine-design/Runtime `AGENTS.md` 的目标架构段落与演进方案一致，同时明确"当前实现"边界（哪些已交付、哪些待实现）；
- 规划数据源与生成文档撤销外部 Runtime 的当前承诺（I-D、M-A 的 step8/hosted harness 部分、contract_registry 的 step8 引用），近期目标统一 M-A/I-Cb；
- delta specs 把两个"普遍性图执行约束"规范改写为兼容契约 + 待实现登记。

**Non-Goals:**

- 不实现 Agent Controller、不解除 `PregelRuntime` 类型绑定、不改任何运行时行为/配置开关/入口路由；
- 不新增持久化表、接口或扩展点（无新 ABC/Protocol）；
- 不处理演进方案迁移清单中归属 step3/6 的规范（`durable-execution-contract`、`task-run`、`agent-environment`、sandbox 系列）——本 change 只动 `unified-chat-execution` 与 `dynamic-orchestration`；
- 不删除任何旧路径、契约、试点或历史证据；不改 `docs/design/execution-mechanism-matrix.md`（它记录实测现状，本 change 不改变行为）。

## Decisions

### D1: 新增 ADR-037 而非原地改写 ADR-036

ADR-036 承载大量已验收的交付证据（rubric v4 基线、持久审批续跑、Workflow 组合复核），原地改写会破坏证据链。ADR-037 以显式 supersedes 表替代其两个方向性决策点（Pregel 作为聊天收敛候选；不提取 Harness），其余（G3 证据门槛、默认路由不因 ADR 授权翻转、Runner 固定计划语义保留）继续有效并在 ADR-036 Status 段补一行指向 ADR-037。备选方案——只改 Status 为 Superseded——被否决：ADR-036 是部分替代，不是整体作废。

ADR-037 的 supersedes 范围表（精确到决策点，不整篇作废）：

| 旧决策 | 被替代/限定的点 | 保留有效的点 |
|---|---|---|
| ADR-001 图优先编排 | "graph orchestration as the primary paradigm"作为所有编排的默认前提 | 图作为 Workflow 的表达与执行机制；三层 Agent 模板 |
| ADR-007 全部多 Agent 模式统一为图模板 | "All orchestration patterns are pre-compiled Graph templates"的普遍性 | 六种静态模式 + DYNAMIC 作为结构化策略继续交付使用 |
| ADR-032 TaskDAG 动态编排 | TaskDAG→子图作为动态编排的唯一形态；"No new persistence"对可靠执行任务的适用 | fail-closed 校验、五隔离契约、预算与事件语义（已交付） |
| ADR-036 自主执行收敛 | "Reuse Pregel graph as chat convergence candidate"；"Do not extract a new harness" | 双路径停止/结构化输出/恢复修复、审批续跑证据、G3 默认启用门槛 |
| ADR-034/035 | 无决策点被替代；仅更新"内置 Runtime = Pregel"表述与 Consequences 中 step8 认证引用 | 治理语义、单写入方、包方向全部保留 |

ADR-032 的 code-as-plan 排除理由需复核记录：原排除基于"多租户下可审计性"，新架构下受控程序执行仍是条件性能力（按演进方案，需隔离与认证），排除理由在默认路径上继续成立，但不排除未来经认证的程序执行 profile。

### D2: 文档更新采用"目标架构 vs 当前实现"双栏表述

positioning/engine-design/Runtime `AGENTS.md` 的更新不直接替换旧段落，而是：目标职责表（引用演进方案第一节六类职责）+ 当前实现状态（共享装配已交付、Controller 待实现、Runner 固定计划预览、执行服务仍绑定 Pregel）。这样文档读者不会把目标误读为已交付，也避免后续验收追授。Runtime `AGENTS.md` 的自足约束（零外部域依赖、subprocess 探针、sanctioned imports 清单）一字不动，只在术语段补 Controller/Harness/共享持久操作的目标边界。

### D3: 规划数据只改数据源，生成区一律再生成

I-D 处置为"已撤销"：iterations 表 I-D 行保留编号，scope 改为标记 revoked/已撤销（外部 Runtime 接入移出演进方案，未来需要时另行评估），不再描述 step8 交付物；M-A 行 Covers 裁为 "step1—7 with minimal step10/11/14 and staged step16"，exit conditions 删除 hosted harness/heterogeneous 部分、聚焦内置受管闭环（I-Cb）；contract_registry Runtime 行 "frozen at step8" 改为按能力独立冻结的表述；plan.yaml review-01 的 `step: 5/8/19` 改为 `5/19`，acceptance 文本去掉 external backend 表述。修改后运行 `feature_inventory.py sync` 再生成受管区域，`check` 验证零漂移、人工字段零丢失。若需要在 `feature-inventory.yaml` 补充条目（如登记 Agent Controller 为待实现的新增验收条目），先核对空闲 ID 再追加后缀，不重编号。

备选方案——手工直接编辑 roadmap.md 受管区——被否决：违反生成区治理，下次 sync 即被覆盖。

### D4: delta specs 用"兼容契约保留 + 新增目标要求"而非改写全部 requirement

`unified-chat-execution` 仅 MODIFIED 第一个 requirement（加"现行引擎路径/兼容路径"限定语，7 个场景原文保留），新增"收敛目标为统一执行应用服务及语义"要求登记 Controller 为待实现；其余 4 个 requirement（流式静默、跨入口一致性、真实入口证据、跨轮恢复回执）描述现行引擎路径行为契约，不动。`dynamic-orchestration` 不修改任何既有 requirement（COORDINATOR/TaskDAG 行为契约不变），新增两个 requirement：结构化策略限定 + channel 计划历史的持久化边界。主 specs 的 Purpose 段在归档同步时按 delta 语义重写（unified-chat-execution 的"统一 Pregel 运行时"表述必须去掉）。

### D5: 分层为 spec-driven

虽是纯文档变更，但改变架构决策记录与规范语义（收敛目标、能力边界），属于 AGENTS.md 风险分层中"架构/扩展、规范语义"类，必须走完整 proposal→design→specs→tasks 流程；不能降级 lightweight。

## Risks / Trade-offs

- [ADR-037 与 ADR-036 的部分替代关系被误读为整体作废] → ADR-037 的 supersedes 表精确到决策点；ADR-001/007/032 只在文件头 Status 加一行"partially superseded by ADR-037（见其范围表）"，正文不改。
- [生成区再生成覆盖人工内容] → 只改 YAML 数据源；`feature_inventory.py check` 验证增量不丢字段、冲突显式失败；diff 审查受管区变更仅限预期行。
- [文档"目标架构"被当成已交付] → 双栏表述 + Controller 一律标注"待实现，交付前不得宣称支持"；验收矩阵引用演进方案第八节既有行，不新增已通过断言。
- [I-D/M-Aa 撤销被理解为删除历史] → 编号保留、状态改为已撤销，历史证据与映射行保留；feature-inventory.yaml 中相关条目 status 不因撤销改写为未实现。
- [规划 YAML 手工编辑引入格式漂移] → 修改后先 `check` 再 `sync`，sync 幂等性以二次运行零 diff 验证。

## Migration Plan

无部署/回滚（纯文档与数据）。评审顺序：ADR-037 → 旧 ADR 状态行 → delta specs → positioning/architecture/engine-design/AGENTS.md → 规划 YAML + 再生成 diff。任一环节评审拒绝则仅回退该环节文件，不影响其余工件。

## Open Questions

（无——遗留的 step3/6 规范迁移范围已在演进方案迁移清单中登记，属后续 change，不影响本设计。）

## 复核修正

- 规划对齐覆盖 I-Ca/I-Cb/I-F 与 M-S/M-A/M-C 的相关门槛，避免只撤销 I-D 而仍要求跨 Runtime 团队认证。I-Ca 不作为 I-Cb 的整体前置；技术交付证据不等于新 Controller 已通过。
- 新条目 1.3.11l 归入 core-04a（platform-guarantee/core-contract/builtin），与 review-01 的内置实现映射区分；旧条目 status 及非空成熟度不变。修改到的历史债务条目补齐严格增量准入字段，使用 unverified/pending 而非授予新支持。
- Delta 提供目标 Purpose，归档同步仍需实际更新主 spec 的 Purpose；本次不提前同步或归档。可靠计划修订要求只对后续声明支持的 profile 生效，既有 DYNAMIC 不追授。
- ADR-032 的审计/多租户理由不能证明代码不可审计；程序执行仍按隔离认证条件交付。接受决策的语义 owner 是 Controller/Workflow，持久化机制负责其提交与恢复操作。新 Controller 尚无可用于比较的实测结果。
