# Proposal

## Why

演进方案（`docs/refactor/enterprise-agent-platform-evolution-plan.md`，#265 修订）已固定 agent-native 目标架构：开放任务默认由不依赖 Pregel 的 Agent Controller 推进；Workflow Executor 保留确定控制规则，Pregel 专注图执行；二者共享持久化执行机制与可信动作管线。外部 Runtime 接入（step8/I-D/M-Aa）已移出方案。但现行实施依据仍指向旧方向，后续 Controller 实施若按现行依据执行，会继续沿"一切收敛到 Pregel"扩展：

- ADR-036 仍将 Pregel 图作为聊天收敛候选，并禁止提取新 Harness；
- `unified-chat-execution` 规范的 Purpose 仍要求"收敛到统一 Pregel 运行时"；
- `docs/features/roadmap.md` 仍保留 I-D"Real external execution certification"迭代，M-A 里程碑仍覆盖 step1—8 与 hosted harness 范围；
- 共享执行服务（`packages/hecate-runtime/src/hecate_runtime/execution_service.py`）的类型直接绑定 `PregelRuntime`，解除绑定的迁移缺少一致的目标边界描述。

按演进方案第七节规定的顺序（先完成 step2/3 的接缝与规范迁移，再交付可靠自主循环），本 change 把新方案落成一致、可审查的开发与验收依据。这是演进方案 step2 待办"同步本次定位、Agent/Workflow 边界、step5e 与 I-Ca/I-Cb 验收、近期 M-A 目标及外部 Runtime 工作包撤销"的具体交付。

## What Changes

1. **新增 ADR-037（Agent 原生执行模型与图执行职责边界）**：固定 Harness、Agent Controller、动态 Plan、Workflow Executor/Pregel、共享持久化执行机制、可信动作执行六类职责；明确对 ADR-001/007/032（图优先编排、多 Agent 图模板、TaskDAG 动态编排）的 supersedes 范围——将其限定为结构化执行策略而非自主执行的必经路径；替代 ADR-036 中"Pregel 图作为聊天收敛候选"与"不提取 Harness"两个方向性决策点（ADR-036 已交付的安全修复与真实模型基线证据保持有效）；复核 ADR-032 的 "No new persistence" 前提与 code-as-plan 排除理由在新模型下的适用边界。ADR-034/035 的治理语义与包方向保持有效，仅更新其中"内置 Runtime = Pregel"与"step8 认证"的表述。
2. **同步定位与架构文档**：更新 `docs/design/positioning.md`（agent-native 执行平台定位、内置自主 Agent/Workflow 一等执行模型、外部 Runtime 接入移出当前承诺）、`docs/design/architecture.md`、`docs/design/engine-design.md`（区分图执行内核、Agent Controller 与共享持久操作的目标职责）和 `packages/hecate-runtime/AGENTS.md`（术语与目标边界，保留自足约束）。所有更新区分"目标架构"与"当前实现"，不追授未交付能力。
3. **同步规划数据源并再生成**：更新 `scripts/feature_inventory_plan.yaml`（review-01 等映射去掉 step8、调整 milestone/acceptance 表述）与 `scripts/feature_inventory_tables.yaml`（iterations 的 I-D 行改为已撤销、milestones 的 M-A 行裁掉外部 Runtime 范围、contract_registry 去掉"frozen at step8"），运行 `feature_inventory.py` 再生成 `feature-catalog.md` 与 `roadmap.md` 受管区域。近期目标统一为 M-A/I-Cb；I-Ca 独立验收；I-D/M-Aa 撤销当前实施承诺，保留历史编号、状态与证据。`feature-inventory.yaml` 仅按严格增量规则补充必要字段，人工证据、成熟度与支持范围不被生成过程覆盖。
4. **提交 delta specs**：`unified-chat-execution` 的收敛目标从"统一 Pregel 运行时"改为"统一执行应用服务及语义"——现行开关路由、会话亲缘、放量记录与直连回退场景继续作为兼容路径契约；Agent Controller 能力登记为待实现，默认启用仍需逐入口 G3 证据与决策记录。`dynamic-orchestration` 的 TaskDAG/COORDINATOR 编排限定为结构化执行策略之一；开放任务的动态 Plan 是可修订意图（Todo、直接行动、局部 TaskDAG、已发布 Workflow 可组合），不强制生成完整 DAG 或经图编译；Coordinator 子图的 channel-only 计划历史不满足新的已接受决策持久化语义，由 step6 共同执行语义扩展。

**边界**：本 change 不实现 Controller、不新增持久化表或接口、不迁移入口、不翻默认开关、不删除旧路径；不改变任何运行时行为。ADR-036 等历史决策的交付证据全部保留，只更新方向性决策与状态标注。

## Capabilities

### New Capabilities

（无——本 change 不引入新运行时能力。）

### Modified Capabilities

- `unified-chat-execution`: 收敛目标从统一 Pregel 运行时改为统一执行应用服务及语义；Pregel chat 图与直连循环保留为现行/兼容路径契约；新增"收敛目标与 Controller 待实现登记"要求，默认启用门槛不变。
- `dynamic-orchestration`: TaskDAG→Pregel 编排限定为结构化执行策略；新增"动态 Plan 与结构化策略的边界"要求，明确 channel-only 计划历史不构成已接受决策的持久化语义、由后续步骤扩展。

## Impact

- **文档**：`docs/design/adr/`（新增 037、更新 001/007/032/034/035/036 的 Status 或关联段、`INDEX.md`、`docs/design/README.md` ADR 时间线）；`docs/design/positioning.md`、`docs/design/architecture.md`、`docs/design/engine-design.md`；`packages/hecate-runtime/AGENTS.md`。
- **规范**：`openspec/specs/unified-chat-execution/spec.md`、`openspec/specs/dynamic-orchestration/spec.md`（经本 change delta 在归档时同步）。
- **规划数据**：`scripts/feature_inventory_plan.yaml`、`scripts/feature_inventory_tables.yaml`；再生成 `docs/features/feature-catalog.md`、`docs/features/roadmap.md` 受管区域；`docs/features/feature-inventory.yaml` 按需严格增量。
- **验证**：`feature_inventory.py check`（增量/漂移）、`feature_inventory.py sync`（生成幂等）、`scripts/feature_inventory.py` 相关测试；`openspec validate`；适用共享检查（ruff/mypy 不涉及 `src/` 变更，预期无代码影响）。
- **不涉及**：运行时代码、数据库 schema、入口路由、配置开关、CI/release 门禁。
