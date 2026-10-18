# Proposal

## Why

DecisionStep 已实现单步决策的接受与恢复；后续决策动作派发需要复用现有单次工具调用治理链。该实现位于 ToolWorker 内，独立调用会受到 Worker/workers 包和图执行机制的传递依赖影响。本 change 提取具体类 ToolCallExecutor，使共享工具调用不要求构造图或 Worker，同时保持既有图执行路径的行为。

## What Changes

- 新增 hecate_runtime.tool_call_executor：迁入单次工具参数解析与摘要、恢复预检、权限/审批、pre/post middleware 与 legacy hooks、sandbox 路由、ActionLedgerHook claim/outcome、事件回执、证据和未知结果停止。
- ToolWorker 保留消息提取、并发批次调度、排空后上抛、顺序、citation 标记和 WorkerResult 包装，委托共享组件；构造签名、兼容导入和既有单次私有委托保留。
- sandbox_router 原具体实现移至 runtime 根，workers 旧路径转发同一模块，避免独立初始化反向加载所有 Worker 与 Pregel。
- 独立调用只需提供稳定 session/tool-call 标识及可信治理上下文；图事件元数据 superstep 缺省为 0，既有图路径提供的值原样保留。事件格式和身份生成规则不变。
- 双入口经 ToolCallExecutor.execute_call 与真实 ToolWorker.execute 测试；补充全新进程禁用图机制、真实 SQL 账本重建、回执丢失与受控并发证据。

## Capabilities

### New Capabilities

无独立新 capability。

### Modified Capabilities

- runtime-pluggability：单次受治理工具调用可脱离图与 Worker；直接调用与 Worker 委托保持既有治理和恢复语义。

## Impact

- runtime 新模块、ToolWorker 委托、sandbox router 归属与兼容转发；无数据库 schema 或配置迁移。
- runtime package 的双入口测试，平台装配测试的内部观察/注入位置随字段迁移更新；保留原行为断言。SQL 账本仅新增集成消费测试，不改 durable 实现。
- 风险级别：完整 spec-driven。涉及运行时架构边界和治理链复用，需相关测试、共享检查、CI 与 PR review；本地记录不代替后两者。
- 不含已接受动作到稳定执行身份的映射、决策动作派发、多轮 Controller、持久审批等待、聊天切流或第二套动作服务/账本。
