# Design

## Context

- 契约已就绪:`AgentExecutionBackend`(`src/hecate/execution/backend.py:207`,六方法,可选能力走声明;契约 0.x 草案,step8 冻结)、`ExecutionRequest`(逻辑引用 + `idempotency_key` + `backend_config_ns`)、`StubExecutionBackend`(`execution/stub.py:49`,能力声明模式参照)与契约测试 `tests/test_execution/test_backend_contract.py`。
- 抽取对象:`WorkflowExecutionService`(studio/workflows/execution_service.py:197,`__init__` 注入 17 项依赖:port/hooks/environment_manager/checkpoint_store/event_store/access_policy/approval_callback/tool_policy_rules/middleware_chains/denial_tracker/grounding_scoring 等)。平台职责点位:模块级 ORM import(:24)、`AgentModel` 查询(:391—421)、`execution_mode` 读取(:884—890)、版本解析(:935—942)。
- 边界守卫已存在:`tests/test_layering_domain.py` 把 `execution`/`runtime` 作为独立域(:66—72);runtime 自足性由 subprocess 探针 + `runtime/AGENTS.md` 懒加载清单守护;step4 登记表单写者约束已有测试(:434—496)。
- 约束:契约 spec(`execution-backend-contract`)"接收方不查平台 ORM""最小接口六方法"不可违背;方案红线"只包装旧服务不算完成证据""平台查询不进内核"。

## Goals / Non-Goals

**Goals**:装配下沉 runtime 域且行为零变化;契约获得首个内置实现(幂等/如实状态/取消不虚报);两条路径共用装配;样本钉住;分层守卫扩展。

**Non-Goals**:包/wheel 抽取(5b)、宿主(5c)、入口迁移与 G3(5d)、持久化 run 记录与取消强制(step6)、审批回调契约化、事件语义的完整治理 envelope(step6 治理事件是另一回事,这里只做引擎事件→契约事件的映射)。

## Decisions

### D1: 装配落点 `src/hecate/runtime/execution_assembly.py`,不落 `execution/`

`execution/` 是平台侧控制面域(登记表所在),放装配会让未来 5b 抽包时逻辑与打包混在一个 PR;放 `runtime/` 使 5b 成为纯打包动作(runtime 域已有自足性守卫,装配依赖 port/workers/guardrail 全在域内)。代价:需把 `runtime/AGENTS.md` 懒加载清单核对一遍,确保新模块零新增跨域懒加载。`WorkflowExecutionService` 原地瘦身为平台 adapter(定义解析 + 调装配),公共方法签名不变——调用方(chat/评估/定时/IM)零改动。

*备选*:先留 studio 内抽函数、5b 再移动。否决——双次移动、5b 混合逻辑与打包变更,违背"一个 PR 一个目的"。

### D2: 装配函数形态——纯函数 + 显式输入对象

抽出 `assemble_execution(...)` 系列纯函数:输入为已解析的执行参数(模板/模式/模型配置/工具 schema/策略规则),输出为装配产物(compiled graph、workers、guardrail bundle、context chain factory)。`WorkflowExecutionService` 的 ORM 段(模板名→WorkflowVersion 解析、AgentModel 参数补全)保留在服务内,解析后传函数。禁止装配函数接触 `AsyncSession`——`db` 参数从装配路径移除,仅平台 adapter 持有。这是"平台查询不进内核"的机械判据。

### D3: `HecateExecutionBackend` 放 `src/hecate/execution/builtin.py`,经装配执行

六方法映射:`submit` = 解析 `backend_config_ns["builtin"]`(模板/模式/模型/工具配置,由平台 adapter 预解析)→ 调装配 → `asyncio` 调度 → 存 run 记录 → 回 `SubmitReceipt(pending)`;`get_run` 查内存 run 表;`read_events` 从注入的 `EventStore` 读引擎事件→ `EventEnvelope`(source=builtin、序号游标、原始事件进 `detail_ns`);`list_artifacts` 首版返回空元组并在能力声明标注(制品引用链归 5c/step6);`request_cancel` 记录请求、回执恒 `requested`(引擎无平台级取消,基线 N1)。契约测试参数化复用:同一套断言跑 Stub 与 Builtin 双实现。

### D4: 同步-异步桥——单事件循环调度,不引入线程

契约方法同步(为进程外 HTTP 设计)。内置实现运行在平台进程内,假设平台事件循环可用:`submit` 内 `asyncio.get_running_loop().create_task(...)`(无运行循环时 `RuntimeError`→ 契约错误 `backend_unavailable`,不静默);任务异常/循环关闭 → run 置 `unknown`(符合"结果未知不伪报")。不做线程池桥(无需求方,避免第二实现前抽象)。单循环假设写入模块 docstring 与能力声明 detail。

### D5: run 记录与幂等状态——进程内,声明非持久

`_runs: dict[idempotency_key, RunRecord]` + run 引用索引;重启丢失回执属已知限制,持久化归 step6(`standalone-durable-actions`/`durable-task-control-plane`)。能力声明 `verification` 字段如实标注"in-process, non-durable, 0.x draft"。*备选*:复用 step4 `task_run_registry` 表。否决——那是平台侧投影,后端本地执行事实持久化语义(领取/意图/回执)是 step6 交付,现在写入会预支其字段所有权。

### D6: 事件游标——序号即游标,缺口显式

`EventStore` 事件天然有序(会话级);游标 = 已读序号(字符串化),`next_cursor`/`has_more` 语义照契约;引擎事件类型不逐一规约(0.x 允许 detail 承载),但 envelope 的 `event_id/source/source_sequence` 必填——契约测试已有断言复用。

### D7: 样本与漂移钉住

`tests/test_execution/samples/` 增 `builtin_*` 样本(submit 回执、状态迁移、事件页、取消回执),断言结构不断言文本;`test_backend_contract.py` 参数化覆盖 Builtin 后,样本漂移即失败。

## Risks / Trade-offs

- [1325 行服务重构引回归] → 硬门:既有测试零断言修改全绿;抽取分 PR(先纯函数抽取,再接 backend),每 PR 独立可回退。
- [异步桥边界(循环关闭、任务取消语义)] → 未知态显式 `unknown`;契约测试补"调度后异常"负例。
- [装配抽取不彻底,残留 ORM 触点] → D2 的机械判据(装配签名无 `AsyncSession`)+ 分层 AST 守卫双向钉住。
- [范围滑向 5b/5c] → Non-Goals 成文;样本与能力声明只覆盖六方法。

## Migration Plan

纯代码重构 + 新增模块,无 schema/API/数据迁移。回退 = revert PR(两个 PR 独立)。旧入口路径全程保持可用(装配抽取后旧服务仍是对外唯一入口,backend 尚无平台调用方——接入是 step6 控制面的事)。

## Open Questions

(无——装配落点、backend 位置、取消语义、持久化边界均已按方案与基线证据定死;`list_artifacts` 首版空集的决定在验收括注中记录即可。)
