# Design

## Context

代码检视确认的实现面:

- **恢复决策表**(`hecate-runtime/action_ledger.py::recovery_decision`):已是"事件日志 + 持久账本"双权威合流的单一决策表,回填顺序为通道历史 → `recorded_content`(账本真实内容)→ 显式标记;`[needs review]` 类标记经 `_checked_recovery_result` 抛 `ExecutionStoppedError` 停止,但 **`[reconciliation required]`(succeeded-无内容)作为普通工具消息返回,模型继续推理**——行为缺口所在。
- **真实传输缺口**:G3 现有断连证据来自 `test_stream_disconnect_persists_state_and_resume_is_clean`(ASGITransport,首段读取前已缓冲完整响应);`tests/test_execution/live_http.py` 是 TS pilot 的客户端 transport,不提供平台真实 socket 服务端。仓库已有真实 TCP 先例:SC 场景以 subprocess runner + 端口文件跑真实 HTTP。
- **入口接线**:MCP `agent_chat` 经 `tools/mcp/server.py::_chat_via_entry` 消费 `EntryExecutionService`;workflow 子任务经 `execution/task_control.py::WorkflowChildTaskAdapter`(step6e)。
- **PG 参数化**:`HECATE_STEP6_POSTGRES_URL` 双引擎模式已在 #257 交付为惯例(平台 ORM + 事件存储同库,每测试独立 schema)。

## Goals / Non-Goals

**Goals**

1. 真实 TCP/SSE 中途断连证据 + 断连后恢复的持久事实断言(PG)。
2. 恢复回执语义修复:真实内容回填 / 写类停止对账 / 只读安全重执行。
3. MCP 与 workflow(部署身份子任务)入口的引擎路径证据。
4. G3 决策记录逐入口更新;默认开关不动。

**Non-Goals**

- 独立 workflow 的 Run/部署身份(另开设计)。
- A2A per-agent 映射、入口安装/升级矩阵认证。
- 默认开关翻转及其放量(前置未齐,本轮只更新决策记录)。
- 真实模型质量验收(step5e 范畴;本轮全部用确定性模型/工具双打)。
- 直连回退路径行为变更。

## Decisions

### D1: 真实 TCP harness —— in-thread uvicorn + httpx 实连接

不引入子进程平台服务器(SC runner 模式面向宿主验收,平台入口用不上其 wheel 安装链)。harness 在测试进程内以 `uvicorn.Server`(config `loop="asyncio"`、随机端口)后台线程 serve `hecate.main.app`,依赖覆盖与 PG 栈复用 #257 的 `pg_stack`/`pg_client` 装配方式;客户端用 `httpx.AsyncClient` 真实 TCP 连接,SSE 中途断开通过**读到首个数据 chunk 后关闭响应**实现(`response.aclose()` 于流未耗尽时,服务端感知断连走既有 cancellation 路径)。

- Windows/CI 兼容:绑定 `127.0.0.1` 随机端口;`port_file` 模式沿用 SC harness 的原子发布经验(直接轮询 `server.servers[0].sockets[0].getsockname()` 免文件)。
- 断连时点选择:确定性 provider 双打先产出至少一个 tool 结果再阻塞后续产出(事件驱动 gate),保证"断开前已成功工具"存在且可断言零重复。
- SQLite 回归兜底:断连/恢复组在 SQLite 事件存储上同样运行(进程内持久);PG 变体参数化于 `HECATE_STEP6_POSTGRES_URL`,未配置显式 skip。

### D2: 回执恢复行为修复 —— succeeded-无内容按副作用类别分流

`recovery_decision` 的 SUCCEEDED 分支在 `recorded_content is None` 时:

- `SideEffectClass.READONLY` → 返回 `None`(允许重执行,与 CLAIMED 分支的只读语义对齐——只读重执行无副作用风险,且能重新获得真实内容);
- 写类 → 抛停路径:返回带专用停止类别的决策,由 `_checked_recovery_result` 升级为 `ExecutionStoppedError("reconciliation_required", ...)`(与 `[needs review]` 同机制),Run 以对账类停止,占位文本不再进入模型消息。

停止类别进入既有 `_stop_category` 词汇表(`reconciliation_required`),终态信封与投影携带之。既有 `[conflict]`/`[needs review]` 语义不变。向后兼容:行为收紧只影响"内容缺失 + 写类"这一原先即为坏证据的路径;不改任何已成功回填的路径。

### D3: 断连恢复断言依赖 D2 的顺序

tasks 中 D2 行为修复先于真实断连场景落地:断连恢复的"零重复副作用"断言在内容缺失注入下依赖停止语义正确;正常路径(内容已持久)断言真实回填。两个用例都保留,分别钉住两条分支。

### D4: MCP 与 workflow 入口证据 —— 同一双打,三个入口

复用 G3 HTTP 测试的确定性 `StubLLMService`/`StubToolExecutor` 模式:

- MCP:经 `tools/mcp/server.py` 的真实会话入口跑 `agent_chat` 多轮、策略拒绝、取消恢复;断言事件序列与 HTTP 等价(复用 `_tool_events` 配对核对的思路,事件来自共享事件存储)。
- Workflow:经平台任务控制面提交声明 `workflow.steps` 的任务(step6e `WorkflowChildTaskAdapter` 路径),子任务为**有部署身份的 Agent**,执行多轮工具与拒绝场景;断言子任务工具事件与父任务收敛经持久事实可查。
- 三入口等价断言以事件序列(tool_name/execution 配对)与结束原因为准,不比较传输层渲染。

### D5: G3 决策记录与文档收尾

`docs/refactor/step5d-g3-default-enable-review.md` 追加逐入口证据更新表(通过/失败/未支持 + 测试引用),结论栏维持"默认关闭";若全部入口证据齐备,只登记"翻转前置已满足前两条,放量记录仍缺",不翻转。演进方案 step5d 剩余项按实际验收结果更新;顺带把 345 行复核链接改为归档后路径。

## Risks / Trade-offs

- **uvicorn in-thread 与 pytest-asyncio 事件循环共存**:服务端跑在独立线程自己的 loop;客户端 loop 与之经真实 TCP 交互,无共享对象。依赖覆盖作用于全局 app 单例,测试结束必须还原(#257 的 fixture 还原模式)。
- **D2 行为收紧可能暴露既有用例依赖占位文本**:宽回归若见断言失败,按新语义修正测试而非保留坏证据;不排除个别场景需要把真实内容写入账本的测试补丁(生产代码不改)。
- **真实 TCP 断连在 Windows 的确定性问题**:断开感知依赖服务端写失败/读半关闭;通过确定性 gate(先产出工具结果再断开)控制时序,避免依赖竞态。若 Windows 本地偶发不稳定,记录环境限制并以 CI(Linux)为准,不得静默降级为 ASGI。

## Migration Plan

D2 为行为收紧,无 schema/契约迁移;`reconciliation_required` 为停止类别词汇表新增成员,投影/信封消费方按既有未知类别容忍。回退即还原代码。

## 复核后的实施细节

- 快照合并在 store 提供的 session lock 内读取和保存；完整保留 channel_state、intent、metadata 和其 replay anchor。读故障不写空状态；Agent 状态更新不能将尚未折叠的日志尾部标记为已消费。现有 store 的跨进程锁能力不因本 change 扩展。
- 会话日志回填排除 `is_error` 消息，避免旧版本写入的对账占位被去掉错误标志后当成真实结果。显式停止通过现有 `ExecutionStoppedError.reason_category` 传递，不新增平行停止类别映射器。
- 实际 TCP 采用原始 socket RST；仅认证该断开类型，普通 FIN/代理超时另需证据。服务器线程的连接使用 NullPool，读侧独立池；建表不预热另一事件循环的连接。PG 层 ORM、事件和快照均持久化，内容丢失直接在同一隔离 schema 删除内容载体，保留成功回执，不改用内存存储。
- MCP 用 FastMCP Client 的协议会话调用注册工具，覆盖多轮、策略/审批拒绝与请求取消恢复。Workflow 子任务覆盖多轮成功、策略/审批拒绝和核实回调后的父任务收敛；独立 workflow 身份、子任务中途传输断连不由该用例认证。
- PostgreSQL CI 显式执行 TCP、MCP 和 Workflow 子任务模块。SQLite 默认层保留；拒绝审计与独立终态写入组合、带工具 Workflow 子任务不授予 SQLite 并发写支持。

## Open Questions

无——四项范围、D2 语义与"证据不齐不翻转"已在立项时与用户确认。
