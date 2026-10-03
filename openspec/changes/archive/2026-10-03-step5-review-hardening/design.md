# Design

## Context

step5 五个交付落地后,入口侧执行已收敛到 `EntryExecutionService`,但入口装配(事件存储、工具注册/加载)仍分散在各入口模块:`channel/api/v1/chat.py` 定义 `_build_tool_registry`/`_load_agent_tools`,`core/composition/im_entry.py` 与 `tools/mcp/server.py` 各自维护模块级事件存储单例,`channel/a2a/server/executor.py` 与 `ops/scheduling/executors.py` 跨模块导入这些私有函数。默认 `EVENT_STORE_BACKEND=memory` 下进程内存在三个互不共享的存储实例(wiring 的 `app.state`、im_entry、mcp server)。A2A executor 以 `select(AgentModel).where(deleted.is_(False)).limit(1)` 全局取第一个 agent,无 workspace 过滤。G3 门禁要求"断线/恢复"真实入口测试,现有 5 项 G3 入口测试未覆盖。复核报告需按 step2/step4 复核轮的既有形态交付。

## Goals / Non-Goals

**Goals:**

- 单进程内引擎事件存储只有一个共享实例,五个入口与 HTTP lifespan 读写在同一实例上。
- 入口侧共享装配(事件存储访问、工具注册、工具加载)有唯一公开来源,入口模块不再复制装配块或跨入口导入私有函数,并由分层测试钉死。
- A2A 执行在无显式 workspace 作用域配置时拒绝而非全局选取;失败响应不泄露内部错误文本。
- G3 断线/恢复路径有真实入口测试证据(或连同阻塞证据与最小修复一并交付)。
- 复核报告与方案 step5 清单处置映射成文,5d 剩余项显式归属 step6/后续条目。

**Non-Goals:**

- 不实现 step6 调度器与 executor registry 接线、不做 Task/Run 持久化订阅视图。
- 不做 A2A per-caller/per-agent 身份完整方案(调用方→agent 映射、agent card 级鉴权),只交付过渡拒绝语义与缺口登记。
- 不迁移定时任务 WorkflowExecutor 的 studio 测试入口,不改变 hecate-runtime/hecate-runner 包边界与升级矩阵。
- 不引入新扩展点(ABC/Protocol)——共享装配为具体函数,无第二实现诉求。

## Decisions

### D1: 共享事件存储采用"lifespan 注册 + 模块级唯一访问器",不做依赖注入容器

新增 `core/composition/entry_assembly.py`:模块级 `_shared_event_store` 与 `get_shared_event_store()`(惰性,`create_event_store(settings)` 工厂),以及 `register_shared_event_store(store)`(幂等,wiring 在 app lifespan 创建 `app.state.event_store` 后调用,使 HTTP 与后台入口读到同一实例)。入口模块只调用 `get_shared_event_store()`。
理由:MCP tool body 与 IM bus 回调运行在请求上下文之外,FastAPI 依赖注入不可达;"同一工厂+单一模块级槽位"是现状代码的最小收敛,不引入新抽象。备选"全部改为请求作用域注入"需重造 MCP/IM 的执行上下文,超出复核轮范围;备选"保留三处单例但改为同配置"已被证伪(memory 后端下实例即边界)。

### D2: 工具装配函数移入 composition 层公开化,chat 模块保留薄转发

`_build_tool_registry`/`_load_agent_tools` 从 `channel/api/v1/chat.py` 迁至 `entry_assembly.py`(公开名 `build_tool_registry`/`load_agent_tools`,不加 `Port/Base` 后缀——具体函数非扩展点),chat 内部引用改为新位置。五个入口统一从 `entry_assembly` 导入;`im_entry`/`mcp server` 的私有单例与各自装配块删除。
理由:入口协议模块(channel/api、tools/mcp)互相不可依赖,composition 根是两侧共同的上游,符合既有 `guardrail_platform.assemble_guardrails`、`runtime_port_adapter.create_runtime_port` 的装配函数布局。

### D3: 分层测试扩展为"禁自带单例 + 禁跨入口私有装配导入"

在 `tests/test_layering_entry_imports.py` 增加断言:入口目录(channel/api、channel/im、channel/a2a/server、tools/mcp、ops/scheduling)内 (a) 不得出现模块级 `_shared_event_store`/`_shared_stores` 存储单例定义;(b) 不得 `from hecate.channel.api.v1.chat import _build_tool_registry|_load_agent_tools` 或 `from hecate.core.composition.im_entry import _get_shared_event_store`。存量引擎具体类(PregelRuntime/GraphCompiler)扫描保持不变。
理由:私有导入无法被运行时拦截,静态扫描与既有分层测试同机制、同失败报告形态。

### D4: A2A agent 解析加配置作用域,拒绝语义落在协议层

新增配置项(如 `A2A_AGENT_WORKSPACE_ID`,空为默认):非空时 executor 以 `workspace_id == 配置值 AND deleted=false` 解析,零命中或多命中均返回 `TaskState.FAILED` 的 Task(协议内失败,消息为稳定文案);为空时同样拒绝(配置缺失文案),不再执行全局查询。异常分支统一改为稳定失败文案 + 服务端 `logger.exception`。
理由:拒绝比"猜一个 agent"安全且行为可预期;多命中拒绝而非取第一个,因为作用域配置的正确语义是唯一解析。备选"默认取第一个非删除 agent 且仅当跨 workspace 时告警"保留了跨租户执行面,被否决。

### D5: G3 断线/恢复测试以"同一 session 两段执行"定义断线

在 `tests/test_channel/` 增加真实入口测试:第一段 HTTP 流式执行中断(消费部分事件后放弃生成器),第二段以同一 `session_id` 经 HTTP 入口恢复执行;断言第二段结果包含会话状态延续(checkpoint 读取)、事件流 tool 配对完整、无静默重复执行。若实现存在阻塞(如生成器放弃未触发 checkpoint 落盘),以最小修复(如显式 checkpoint 触发点)一并交付并在测试旁注明语义边界。
理由:G3 门禁原文即"断线/恢复";HTTP 是当前唯一有真实入口回归的会话型入口,恢复语义经 checkpoint store 已具备可行性,不依赖 step6 持久化。

### D6: 复核报告与清单处置沿用 step4 复核轮形态

`docs/refactor/step5-review-report.md`:复核范围与证据 → 发现与处置表(逐项:发现、证据、修正)→ 剩余登记项处置映射(调度器接线/A2A 身份/studio 测试入口 → step6 或后续条目;事件映射尾项、订阅视图 → step6)→ 验证记录。方案 step5 清单 5d 条目补处置注记,保持未勾选。

## Risks / Trade-offs

- **A2A 拒绝语义是破坏性行为变化**:现有部署若无 `A2A_AGENT_WORKSPACE_ID` 配置,A2A 执行将从"总能跑第一个 agent"变为拒绝。这是有意为之(跨租户风险 > 可用性),需在 README/配置注释与复核报告中显式标注迁移动作。
- **memory 后端仍是进程内状态**:单实例收敛修复跨入口可见性,但多进程部署(多 worker)下事件仍不共享——该边界属既有事实,由 EVENT_STORE_BACKEND=postgres 解决,报告如实记录,不在本 change 扩大战线。
- **D5 测试依赖生成器放弃的确定性**:若底层引擎对"消费者放弃"的处理存在未定义行为,测试可能暴露比预期更深的问题;此时按 D5 的"阻塞证据 + 最小修复"路径处理,不为测试而放宽断言。
- **分层静态扫描存在绕过面**(动态 import 等):与既有分层测试同限,不追求完备;报告记录该边界。
