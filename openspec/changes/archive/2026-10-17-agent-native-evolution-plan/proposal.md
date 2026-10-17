# Proposal

## Why

现行演进方案仍将自主 Agent 是否经过 Pregel 作为待定实现选择，入口规范又以收敛到 Pregel 为目标，无法清楚指导下一阶段。根据已确认的讨论，本次固定面向模型自主能力增强的目标架构，并将现有执行与治理资产纳入渐进迁移路径。后续三方对比确认还需将单 Agent 的最小交付、证据和验收前移，并补充文件状态与执行状态的恢复对应关系。

## What Changes

- 固定 Harness、Agent Controller、Workflow Executor、Pregel、共享持久化执行机制和可信动作执行的逻辑职责；动态 Plan 作为可修订的执行意图，不另建第三套运行时。
- 改写 step5e、step6 和入口切换依赖，补充已接受决策、计划修订、Child Run、持久等待及跨执行方式的共同恢复要求。
- 区分运行内计划与发布型 Workflow，明确代码、图形和语义定义入口及条件性程序执行；同步评测、发布、人类干预和符合性验收。
- 保留编号、历史交付和生产认证限制，列明现行 specs/ADR 的后续迁移工作；修复失效链接与实践对照中的过期实施建议。

- 补充执行结束、结果交付、任务验收与业务效果确认的责任边界；目标和验收标准受控修订，最小完成判断、受控续跑和稳定证据引用随首次自主闭环交付。
- 区分平台 Workspace、执行文件工作区与 Sandbox，明确产物版本、部分提交和旧 Worker 写入约束；文件任务按启用范围验证，不要求所有 Agent 安装沙箱。
- 将子执行调用方式、恢复保证与批量组织分轴；以研究交付和受控业务操作牵引端到端验收，完整 Batch 和企业 Team 保持后续范围。

- 移除 Problem_Lab 工作簿驱动的需求映射、专项待办及验收矩阵；用户将其定位为 step1—step19 完成后的独立验证，不作为平台交付依赖。
- 将目标定位收敛为企业级 Agent 原生、持久化、强治理智能执行平台；内置且可独立交付的 Runtime 是核心产品能力，业务 App 可独立集成或将执行宿主纳入控制面管理。

## Capabilities

### New Capabilities

无。本次产物为规划文档，不实现新产品能力。

### Modified Capabilities

无。本次不修改现行 runtime/API/持久化契约或主 specs；相关规范冲突在方案中明确登记为后续工作的前置。使用完整 `spec-driven` 记录架构推导与迁移风险，设置 `skip_specs: true`，不通过虚构 delta spec 宣称运行时要求已经变更。

## Impact

修改 `docs/refactor/enterprise-agent-platform-evolution-plan.md` 与 `docs/refactor/agent-platform-practice-review.md`，并创建本 change 的 proposal/design/tasks/review。当前定位文档、功能清单、roadmap、ADR、主 specs、代码和数据库按所属后续工作包更新；本次不切默认路由，不升级已有证据等级。

## Acceptance Criteria

- Agent Controller 默认不依赖 Pregel，Pregel 保留图执行职责；共享的是持久化执行与治理语义，非单一图模型。
- step5e/6/7 形成可靠自主循环的共同切片，入口切流依赖其验收；最小 Child Run 不依赖完整企业 Team。
- 语义定义与执行确定性分轴；计划提案、已接受版本和实际完成事实分离。
- 新增故障与混合执行验收具有所属步骤；M-A/I-Cb 与 I-Ca 分别认证，既有勾选和证据不被重置或扩大。
- 最小产物、证据及验收不依赖企业 Team；目标修订、续跑、文件恢复与委派保证均有责任步骤、首次交付范围及故障断言。
- 演进计划不再引用 Problem_Lab/P01—P08 或要求迁移其映射；保留独立成立的平台契约和安全/恢复验证，不删除历史测试资料。
- 定位突出 Agent-Native、Durable Execution 与 Governance；开发、发布、协作等作为围绕执行核心的平台能力，不把业务 App 表述为控制面管理对象。
- 文档内部链接、编号、历史证据与关联裁决一致，记录实际验证结果及未执行检查。
