# Hecate 平台实施基线(platform-evolution-baseline)

> 性质:代码事实快照,固定方案文档启动实施时的可核验基线;不承担实时状态源,不跟随后续提交更新。后续 change 各自对照本文件声明增量。
> 事实标记:【已证实】= 本文件核验方法可直接复核;【未核验】= 证据不足,禁止当作事实引用。
> 输入关联:治理方案见 [enterprise-agent-platform-evolution-plan.md](enterprise-agent-platform-evolution-plan.md)(下称"方案");功能状态面板见 `docs/features/feature-inventory.yaml`(机器可读,与本文件职责不同)。

## 1. 基线声明

| 项 | 值 | 备注 |
|---|---|---|
| 基线分支 | `feat/platform-evolution-baseline` | 由用户决策以主目录分支承载,未使用 `opsx-flow.sh` worktree,属对 `scripts/opsx-flow.sh` 头注约定的一次已记录偏离 |
| 基线提交 | `b8fd926`("docs: add enterprise agent platform evolution plan (#183)") | 位于 `8a8a84c`(#182)之上,即"方案文档已合入后的 main";文中全部 `file:line` 以该提交为准 |
| 快照日期 | 2026-09-27 | 仅事实记录 |
| 在途 change | 无(`openspec list --json` 为空) | 本 change `platform-evolution-baseline` 是方案启动后的第一个 change |
| 核验方法 | 读码 + `grep` + `openspec list`/`git diff`;未做运行时验证、压测或第三方接入 | 运行时行为断言以测试文件存在性为证据,不宣称已执行 |

## 2. 执行入口清单

清单来源(bottom-up):`src/hecate/main.py` 的 `include_router` 全量挂载(main.py:404—447、470—480、556—569)与条件挂载(main.py:401—413 auth/budget、449—458 hecate-memory、460—469 hecate-ops、482—502 hecate-llm hub、504—518 SSO/SCIM、520—531 tenant、533—548 MCP、550—554 A2A、571—578 sandbox environment)。管理类 API(agent CRUD、模板、提示词等)不是执行入口,登记为排除项,不逐条展开。

### 2.1 八类执行入口

| 类 | 入口 | 实际执行服务 | 安全入口 | 事件存储 | 取消 | 缺口 |
|---|---|---|---|---|---|---|
| Agent chat | `channel/api/v1/agents.py`(复用 chat 处理器,agents.py:30;挂载 main.py:429) | `WorkflowExecutionService`(chat.py:452—473);`CHAT_TOOL_LOOP_ENGINE_ENABLED=false` 时走旧工具循环(chat.py:794—936) | `get_auth_context`(`core/deps_workspace.py`)注入 `AuthContext` | `EventStore`(`runtime/eventstore.py:133`;组装根按 `EVENT_STORE_BACKEND` 装配,wiring.py:85,默认 `memory`,config.py:90) | 无(见 N1) | 旧循环工具调用不产生引擎事件,chat.py:942—943 自述为已知边界 |
| 普通 chat | `channel/api/v1/chat.py` `/v1/chat`(chat.py:152 起;挂载 main.py:427) | 同上(同一执行服务与旧循环双路径) | 同上 | 同上 | 无 | 同上 |
| Workflow | `studio/api/workflows.py`(`WorkflowService`,workflows.py:63 起;`WorkflowTestRunner` workflows.py:39) | `WorkflowExecutionService` → `PregelRuntime`(`runtime/pregel.py:91`) | 同上 | 引擎事件 + checkpoint(`runtime/checkpoint.py`) | 引擎内 interrupt/resume 为 checkpoint 暂停(pregel.py:16—18、96—99),非外部取消 API | 同 N1 |
| MCP | `/mcp` mount(main.py:533—548,fastmcp http_app + `MCPAuthMiddleware`) | `tool_execute`(tools/mcp/server.py:707)自建 `BuiltInToolExecutor` 直连 `ToolRegistry`(server.py:726、742—744);`agent_create/update/delete`(server.py:344、396、446) | `MCPAuthMiddleware` 传输认证(tools/mcp/auth_middleware.py:43) | 无引擎事件(直接调用) | 无 | G1:无角色检查、无服务端资源上下文(见 §7) |
| A2A | `channel/a2a/server/app.py` `handle_jsonrpc`(app.py:43;条件挂载 main.py:550—554) | `A2ARequestHandler` + 独立 `task_store.py`(A2A 任务状态自成一体) | `server/auth.py` 签名校验(方案 §二:#176 已合并失败关闭) | A2A task_store(与平台 Run 无关联) | 协议级 `handle_cancel_task` 已实现(handler.py:90):终态校验后置 CANCELED,仅改 task_store,与底层执行无联动 | task_store → 平台 Task/Run 映射不存在(方案 step4 范围) |
| 定时任务 | `ops/api/schedules.py`(`ScheduleManager`/`ScheduledTaskService`,schedules.py:34—35) | `AgentExecutor` **直接调 `llm_service.chat`**(ops/scheduling/executors.py:41—87;executors.py:46—48 注释明说因缺 request-scoped 句柄而不经引擎);`WorkflowExecutor`(executors.py:93) | 管理入口同上;执行体不构造 `AuthContext`(executors.py:57—87) | 无引擎事件 | 无 | 定时执行完全绕过引擎、guardrail 与回执(见 B3) |
| IM | `packages/channels/*`(slack:`hecate_channel_slack/channel.py`;feishu 同构)+ `channel/im/message_bus.py` + `channel/adapter.py` | `IMMessageBus` worker → 挂载的 `WorkflowExecutionService.execute`(message_bus.py:8、65、134) | 消息→平台身份映射【未核验】(TODO-I1) | 引擎事件(经 WorkflowExecutionService) | 无(N1) | N4 |
| 评估调用 | `ops/api/evaluation*.py` 七个 router(main.py:419—426) | `ops/evaluation/engine.py:793—796` 直接构造 `WorkflowExecutionService` | `get_auth_context`(evaluation.py:191—192) | 引擎事件 | 无 | 与 chat 链组装不同源,独立装配(见 B4) |

### 2.2 入口层缺口清单

- **N1 无平台级取消 API**:在 `runtime/pregel.py`、`runtime/command.py`、`runtime/worker.py` 及 `studio/api/`、`channel/api/` 中 grep `cancel` 均无命中【已证实】。引擎 interrupt(pregel.py:96—99)是 checkpoint 暂停,不能对外承诺"任务已取消"。
- **N2 事件存储所有权分裂与默认易失**:ABC 在 runtime(`runtime/eventstore.py:133`),唯一持久化实现 `PostgresEventStore` 归 studio 域(`studio/event_state/postgres_store.py:64`,经 `studio/event_state/__init__.py:13` 导出);组合根按 `EVENT_STORE_BACKEND` 装配(wiring.py:85、93),默认 `"memory"`(config.py:90)即进程内易失。平台级治理事件存储(跨入口、含控制命令回执)不存在(step6 范围)。
- **N3 执行装配多源**:chat 链(chat.py:456 `create_runtime_port`,core/composition/runtime_port_adapter.py:385)、评估链(engine.py:793—796)、定时链(executors.py:41—87)各自组装,入口行为不一致(step5 收敛对象)。
- **N4 IM 消息→平台身份映射未核验**(TODO-I1):执行链已核实——IM 包收消息后经 `IMMessageBus` 进程内队列与 worker(message_bus.py:42、72、134)调用挂载的 `WorkflowExecutionService.execute`(message_bus.py:8、65);Slack 包只做收发与投递委托(channel.py:5、207)。消息处理是否构造 `AuthContext`、workspace 如何解析,未核验。
- **N5 排除项登记(已核验为配置 CRUD)**:`tools/api/gateway.py` 仅 /targets 增删改查(gateway.py:52—128);`runtime/api/hooks.py` 仅 hook 配置 CRUD(hooks.py:24—67);`tool_policies_router`、`tool_cache_router` 等同属配置面(main.py:565—569)。均不属执行入口;其变更不触发本清单更新。

## 3. 后端能力清单

判断标准:契约以 `openspec/specs/` 下存在对应 spec 为"已声明";实现以代码文件为证;替换能力以"另一实现通过同一验收"为"已认证"(当前全部未认证)。

| 后端 | 契约证据 | 内置实现 | 认证/缺口 |
|---|---|---|---|
| Runtime | 平台→Runtime 方向的执行后端契约**不存在**(方案 step3 交付);反向的 `RuntimePort`(runtime/ports.py)是引擎调用外部的接口 | `PregelRuntime`(pregel.py:91)+ `WorkflowExecutionService`(studio/workflows/execution_service.py) | 无第二实现;方案 step8 前不可能宣称可替换 |
| Memory | `memory-provider-contract`、`memory-api`、`memory-isolation`、`memory-lifecycle`、`cross-thread-memory-store`、`session-memory` 等 spec 已存在 | `packages/hecate-memory`(`rag/vector_store.py`、`embedding.py`、`chroma_store.py`、`milvus_store.py` 等)+ `core/composition/memory_provider.py` | 有契约与多向量库 adapter;第三方 provider 替换未认证(方案 step9b) |
| Knowledge | `knowledge-memory` spec 存在 | `hecate_memory.api.knowledge`(main.py:452—455 条件挂载)+ packages/hecate-memory | 组件化检索(Loader/Chunker/Embedding/Retriever/Reranker)公开契约不存在(方案 step9d) |
| Evaluation | `agent-evaluation`、`evaluation-framework`、`builtin-evaluators` 等 spec 存在 | `ops/evaluation/`(engine.py 等)+ `packages/hecate-ops`(`otel_setup.py`、`span_adapter.py`、`monitoring.py`) | `EvaluationBackend` 抽象不存在;外部 evaluator 未认证(方案 step10) |
| Observability | `monitoring` router 由 hecate-ops 提供(main.py:464—469);traces/OTLP 基础在 hecate-ops | hecate-ops 包 | 外部观测目标适配未核验(方案 step10) |
| Sandbox | `agent-environment` spec 存在 | `packages/hecate-sandbox`(`environment/environment.py`、`manager.py`、`docker_environment.py`、`network_policy.py`、`credential_scope.py`);environment_router 条件挂载(main.py:571—578) | 进程外 SandboxProvider 契约不存在;云端/远程环境未接入(方案 step3/8) |

## 4. 信任与数据流拓扑

标准形态:人类客户端 →(认证)→ 控制面 API → 执行服务 →(RuntimePort)→ LLM/工具/Memory;Sandbox 为旁挂执行环境;网络层边界属部署配置。

已证实的绕过授权或审计的直连路径(全部在应用层):

| 编号 | 路径 | 证据 | 当前阻断 |
|---|---|---|---|
| B1 | MCP `tool_execute` → 自建 executor → `ToolRegistry.execute`(registry.py:62),不传服务端上下文、用全局 `settings.WORKSPACE_ROOT` | tools/mcp/server.py:707—751 | 无(仅传输层认证);G1 修复对象 |
| B2 | chat 旧工具循环在 API 进程内直接执行工具:构造 `tool_context`(session/agent/workspace,chat.py:920—926)后调 `tool_registry.execute`(chat.py:926) | chat.py:794—936 | 端点有 `AuthContext`,但 Registry 不执行统一策略/审批,工具副作用发生在 API 进程;flag 关闭时为默认路径 |
| B3 | 定时任务执行直调 `llm_service.chat`,不经引擎、无 guardrail/回执 | ops/scheduling/executors.py:41—87 | 无 |
| B4 | 评估引擎独立组装执行服务 | ops/evaluation/engine.py:793—796 | 读路径,风险低;登记以备入口收敛 |
| B5 | Docker sandbox 到宿主网络/凭据的直连 | 部署层【未核验】(TODO-D1);代码侧有 `network_policy.py`、`credential_scope.py`,实际部署是否启用未验证 |

强制执行点现状:认证边界(`MCPAuthMiddleware`、`get_auth_context`)已在入口;动作策略能力存在于 `tools/policy/policy_pipeline.py`、`tools/gateway/authz.py`,但 B1—B3 均未强制经过(G1 的实质)。

## 5. 托管执行组合数据流

当前代码**不存在** Deployment/后端绑定模型(无对应 ORM 表与登记字段),本节为前瞻登记格式,落地在方案 step4;格式依据方案 §一"执行组合"表:

每个执行组合登记:`harness 所有方` × `Sandbox/环境所有方` × `工具与数据访问执行点` 三轴,附数据驻留地区、保留/删除条件、凭据与网络出口、控制等级(unsupported/cooperative/enforced)、来源证据与核验时间。

样例(引方案 §一已核验事实,作为格式演示而非当前能力):OpenAI Agents API 当前仅美国数据驻留、不支持 Zero Data Retention,自托管 Sandbox 不改变这两项。该条件会变化,接入前必须重新核验。

## 6. 七能力域归属清单

现维护边界:`tests/test_layering_domain.py:59—66` 以 AST 扫描钉住六个域(runtime/tools/enterprise/channel/studio/ops)的跨域 import,辅以 `tests/test_runtime/test_runtime_self_sufficiency.py` 的运行时探针。方案七能力域 → 现目录映射及归属证据:

| 方案能力域 | 现代码位置(候选) | 已证实证据 | 违规/缺口 |
|---|---|---|---|
| Agent Engineering | `studio/`(agents、workflows、templates、prompts) | 分层测试覆盖 studio 域 | 工程工作台与发布制品接口未分离 |
| AgentOps | `ops/`(health、costs、traces、alerts、quotas) | 同上 | Task/Run 状态尚不存在,AgentOps 无权威查询源 |
| Agent Control Plane | 无(分散于 studio/channel) | §2 清单即证据 | Deployment/Task/Run 模型不存在(step4) |
| Agent Governance | `enterprise/`、`ops/api/audit.py`、`tools/policy/` | 分层测试覆盖 | 审批记录与 Runtime 暂停实现未分离(方案 §二) |
| 安全 | `enterprise/auth/`、`enterprise/vault/`、`tools/gateway/`、`tools/policy/` | 目录结构【已证实】 | 强制执行点未覆盖 B1—B3 |
| 评测 | `ops/evaluation/` | engine.py | EvaluationBackend 未抽象 |
| MCP/A2A 接入层 | `tools/mcp/`、`channel/a2a/` | §2 证据 | 协议身份→平台授权映射不完整(G1) |

跨域数据访问已证实样本(ORM 模型共享于 `models/`,按"每表一个领域负责读写"规则,以下为跨域**读** AgentModel 的实例):`ops/ops_center/overview.py`、`ops/api/traces.py`、`ops/evaluation/annotation/service.py`、`ops/prompt_optimization/service.py`、`ops/scheduling/executors.py`、`tools/mcp/server.py`、`tools/skill/loader.py`、`tools/skill_registry/registry.py`。跨域**写**路径未系统核验(TODO-O1)。

## 7. G1—G5 门槛记录

| 门槛 | 代码证据(本次复核) | 复现指针 | owner | 目标 change |
|---|---|---|---|---|
| G1 统一动作授权 | MCP `tool_execute` 无角色检查、无服务端资源上下文(server.py:707—751);`agent_*` 仅身份/workspace(server.py:344—446) | 以 viewer 身份经 MCP 调用写工具,观察与 REST 入口授权结果不一致 | 待指定 | `g1-mcp-action-enforcement` |
| G2 副作用回执与恢复 | `get_tool_receipt` 只认 TOOL_RESULT、读取失败返回 None(tool_worker.py:81—99);`prior_status` 为空即放行重试(tool_worker.py:404—422);超时/异常兜底写 TOOL_RESULT(tool_worker.py:618—630) | 方案 §二"本轮验证范围"最小复现(同 session/call_id 双调用);`tests/test_runtime/test_tool_receipts.py` | 待指定 | `g2-tool-receipt-recovery` |
| G3 入口与引擎收敛 | `CHAT_TOOL_LOOP_ENGINE_ENABLED: bool = False`(core/config.py:613);收敛测试存在但部分依赖 mock(`tests/test_runtime/test_chat_engine_convergence.py`) | 真实 HTTP/SSE 多轮 + 断线恢复测试缺失,见方案 G3 | 待指定 | step5 |
| G4 可解释用量 | 统一常量单价 `_COST_PER_TOKEN = 0.00001`(core/composition/runtime_port_adapter.py:33),估算在 runtime_port_adapter.py:216 | 缺 reported/estimated/reconciled 区分,见方案 G4 | 待指定 | step7/step10 |
| G5 规划数据权威来源 | `cmd_extract` 全量重建覆盖手填数据(scripts/feature_inventory.py:206、56);`check_inventory` 非严格模式宽松比对(feature_inventory.py:146、230) | 运行 extract 前后 diff YAML 手填字段丢失即复现 | 待指定 | step2 |

owner 均留待用户指定;指定前对应修复 change 不得启动(方案 §七:人员由用户安排)。

## 8. P01—P08 映射

从方案 §一映射表细化,增加 fixture 占位(编号指向 §9 场景)与未支持项:

| 问题组 | 平台保证 | 可选实现/由场景提供 | 责任 step | fixture 占位 | 未支持项 |
|---|---|---|---|---|---|
| P01 检索失败诊断 | 固定知识/检索配置快照并比较;能力允许时关联阶段证据 | 语料、切片策略、Embedding、向量库、Reranker | step1/9d/10 | S1 变体 + 检索配置快照 | 外部黑盒 RAG 的内部阶段诊断 |
| P02 企业知识接入 | 来源/版本/ACL/索引作业状态、变更删除传播 | 解析器、同步器、存储 | step7/9c/16 | S4 变体(同步中途故障注入) | 无法回执删除传播的后端 |
| P03 RAG 评测 | 数据集/证据版本、逐样本结果、分组、门禁 | 数据集内容、grader、指标 | step1/10/11 | S1 + 评测数据集 | 不同评估器同名分数直接比较 |
| P04 GraphRAG/KG | 图制品来源/版本记录;图检索按同一契约参与比较 | Ontology、抽取、图库 | step9d/10(有场景再启用) | 暂缓 | 无真实场景时不承诺 |
| P05 工具调用可靠性 | schema 校验、幂等、回执、错误分类 | 业务 API、选工具模型 | step3/6—7/10 | S2、S5 | 供应商内部工具的强制控制 |
| P06 身份/权限/安全 | 委派身份、资源 ACL、审批、攻击负例 | 企业 IdP、策略引擎 | step4/7/9c/16 | S2、S3 | 无法封闭的直连旁路(降级标注) |
| P07 流程与人工介入 | 持久任务、等待/恢复、审批/接管、补偿关联契约 | 业务状态机、补偿逻辑 | step3/6/13—14 | S3、S4 | 不支持暂停后端的"已暂停"状态显示 |
| P08 评测/观测/定位 | 事件追溯、失败分类、版本化回归、成本来源 | trace UI、告警后端 | step6/10—11/16 | S1、S4 | 采样 trace 作为治理证据 |

## 9. 架构评测包规格(规格冻结;实现归 `platform-evolution-scenario-pack`)

### 9.1 场景集与断言

五个场景,全部使用合成数据与测试企业工具,不接触生产写入:

- **S1 正常执行**:授权输入 → Agent 产出摘要产物并写入测试工单。断言:产物符合 expected/ 中 schema;工具调用有 TOOL_CALL/TOOL_RESULT 配对;副作用计数恰为 1。
- **S2 拒绝动作**:调用禁止动作矩阵中的工具/资源。断言:副作用发生前拒绝;拒绝有证据记录;工单服务零新增记录。
- **S3 等待审批**:高风险动作进入等待;断言:审批绑定参数摘要,批准前无副作用;发起者与审批人职责分离(固定不同主体);批准后执行且回执关联审批引用。
- **S4 后端失联**:故障注入点(LLM 网关、事件存储写入、审批回调、工单服务,四选一)触发失败。断言:任务状态显式 failed/未知,不伪报成功;重试不产生重复副作用。
- **S5 重复提交**:同幂等键重复请求。断言:仅一次执行;重复请求返回同一任务关联或显式冲突。

断言分工:权限、副作用计数、状态转换、事件配对用**确定性断言**;内容质量(摘要忠实度)用明确 rubric + 记录 evaluator 版本,**不进入**门禁断言,不允许以平均分抵消安全负例。

### 9.2 fixture 目录结构与引用规则

```
tests/fixtures/evalpack/
  inputs/      # 合成材料,front-matter 携带 ACL/数据分类
  tools/       # 测试工单服务等合成工具 schema 与调用记录
  expected/    # 每场景产物 schema 与断言清单
  policies/    # 允许/禁止动作矩阵、审批人设定、职责分离规则
  scenarios/   # 场景编排:故障注入点、复位说明
```

`platform-evolution-scenario-pack` 的 proposal 必须按编号引用本节(S1—S5 与目录名);目录改名、场景增删须先修订本节并在该 change 中注明"基线 §9 变更",防止规格与实现漂移。

## 10. 旧分支归档

`origin/docs/platform-evolution-plan` 与 `origin/main` 经 `git diff` 核实**无内容差异**(该分支的方案文档已随 #183 squash 合入),无未归档的调查材料。历史失败调查以方案 §二"本轮验证范围"为权威记录(G2 双调用复现、分层测试跳过说明、feature_inventory 校验结果),本文件 §2/§7 已建立对应指针。该远程分支可在合并后删除。
