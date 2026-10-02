# Spec Delta

## Purpose

定义内置执行后端(HecateExecutionBackend)对平台侧执行契约的实现行为与共享执行装配的边界:六方法语义的如实履行、平台查询与装配内核的隔离、包装路径与平台路径的装配同源,以及行为兼容样本的钉住,使 step3 契约获得首个内置实现并为 step5b/5c 的独立消费抽取提供稳定内核。

## ADDED Requirements

### Requirement: Builtin backend fulfills the six-method contract faithfully

`HecateExecutionBackend` MUST 实现 `AgentExecutionBackend` 全部六个必选方法,且仅通过契约类型(`ExecutionRequest`/`SubmitReceipt`/`RunStatus`/`EventPage`/`CancelReceipt`)交互,不向调用方泄漏 Graph、Channel、checkpoint 或 Pregel 内部类型。`submit` MUST 以 `idempotency_key` 幂等:同键同内容返回原始回执,同键异内容返回 `version_conflict`;契约版本超出支持窗口 MUST 拒绝。`get_run` MUST 如实映射引擎观察状态:执行后端不可用、结果未知时 MUST 报 `unknown`,MUST NOT 伪报 `succeeded` 或 `cancelled`。`request_cancel` MUST 只产生 `requested` 级回执,MUST NOT 报告 `applied`——引擎 interrupt 属 checkpoint 暂停,不是平台级取消;能力声明 MUST 与实际行为一致。可选能力(pause/resume/provide_input/resolve_approval/export_context)在本实现 MUST 声明 `unsupported`,请求时显式拒绝而非静默成功。

#### Scenario: Idempotent submit replays the original receipt

- **WHEN** 同一 `idempotency_key` 与同一请求内容被重复提交
- **THEN** 返回原始回执(run 引用不变),不产生第二次执行

#### Scenario: Same key with different content conflicts

- **WHEN** 同一 `idempotency_key` 携带不同请求内容再次提交
- **THEN** 以 `version_conflict` 拒绝,不启动新执行

#### Scenario: Cancel is requested, never applied

- **WHEN** 对运行中的 run 调用 `request_cancel`
- **THEN** 回执状态为 `requested`,任何查询路径都不会将其呈现为 `applied`;能力声明的取消等级与该行为一致

#### Scenario: Unknown outcome is not faked

- **WHEN** 执行调度后任务异常终止或结果不可观察
- **THEN** `get_run` 返回 `unknown` 或 `failed` 并携带原因,不返回成功

#### Scenario: Unsupported optional capability is explicit

- **WHEN** 对声明为 `unsupported` 的可选能力发起请求
- **THEN** 后端返回显式 `unsupported` 错误,不返回成功或静默忽略

### Requirement: Shared execution assembly is platform-agnostic

图编译、Worker 构造、上下文链与 guardrail 装配 MUST 作为共享装配存在于平台域之外(`runtime/` 域内),其模块 MUST NOT import studio 模块或 SQLAlchemy ORM 模型;平台定义解析(Agent/Workflow/Version 查询)与平台授权映射 MUST 留在平台 adapter(现有 `WorkflowExecutionService` 位置),转换为执行输入后调用共享装配。分层守卫 MUST 以测试钉住:装配模块出现 studio/ORM import 时校验失败。行为兼容 MUST 以既有执行服务测试不改断言全绿为证据。

#### Scenario: Assembly stays free of platform imports

- **WHEN** 分层测试扫描共享装配模块的 import 面
- **THEN** studio 与 ORM 模块均不在 import 闭包内,违规即测试失败

#### Scenario: Definition resolution happens before the assembly

- **WHEN** 平台路径执行一次 Agent 任务
- **THEN** Agent/Workflow/Version 的数据库解析发生在平台 adapter 内,共享装配收到的输入已不含平台 ORM 对象

#### Scenario: Existing behavior is unchanged

- **WHEN** 运行既有执行服务与入口回归测试(不含本 change 新增断言)
- **THEN** 全部通过,证明抽取未改变既有行为

### Requirement: Wrapper and platform path share one assembly

`HecateExecutionBackend` MUST 通过共享装配执行,MUST NOT 复制或旁路装配逻辑;内置后端与平台 adapter 调用同一装配函数集合,保证两条路径行为一致。内置后端 MUST NOT 查询平台 ORM——请求中的标识是逻辑引用,定义内容必须已由上游平台 adapter 解析并随请求传入(后端专属配置经 `backend_config_ns` 命名空间)。同步契约方法与异步引擎的桥接 MUST 以事件循环调度实现,回执先落 `pending`/`running`,终态经 `get_run` 观察;单事件循环假设 MUST 在模块文档声明。

#### Scenario: Backend delegates to the shared assembly

- **WHEN** 检查内置后端的执行路径
- **THEN** 其装配调用与平台 adapter 所用为同一函数集合,无平行实现

#### Scenario: Backend resolves nothing from platform tables

- **WHEN** 内置后端处理一个执行请求
- **THEN** 全程无平台 ORM 查询;缺失已解析定义的请求被显式拒绝并指明应由平台 adapter 提供

### Requirement: Compatibility samples pin builtin behavior

内置后端 MUST 固定兼容样本(请求、回执、状态、事件页、取消回执的标准样例),样本断言 MUST 针对契约结构(字段、状态语义、游标连续性),MUST NOT 依赖模型文本。后续切片(5b/5c) MUST 能以相同样本对比行为漂移;样本与实现不一致时测试失败。

#### Scenario: Sample drift is caught

- **WHEN** 内置后端的回执或事件结构发生变化
- **THEN** 对应样本断言失败并指出漂移字段

#### Scenario: Samples validate against the contract schemas

- **WHEN** 校验兼容样本
- **THEN** 每个样本通过契约层 schema 校验,不携带后端专属类型泄漏
