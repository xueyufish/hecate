# Design: platform-entry-migration

## Context

step5a 把执行收敛到共享装配(`hecate_runtime.execution_assembly.assemble_execution`),平台侧 `WorkflowExecutionService` 是其上的平台 adapter(ORM 解析、定义加载、授权映射);step4 交付了 `TaskRunRegistry`(Task/Run/conversation 链接登记);#178 交付了聊天子图与 `CHAT_TOOL_LOOP_ENGINE_ENABLED`(默认 false,仅全局布尔)。当前缺口:入口各自内联构造执行服务且装配不一致(`tools/mcp/server.py` 只传 port+db,无 event_store/checkpoint);入口执行不产生 Task/Run 记录;引擎路径只有 mock 分支测试;开关无 workspace 维度。A2A executor 与定时任务 executor 完全绕过执行服务(`llm_service.chat` 直连),属另一组调用链。

## Goals / Non-Goals

Goals:

- 入口执行统一经一个平台入口服务,装配一致,Task/Run 可关联;
- G3 要求的真实入口证据(流式/非流式、多轮、审批拒绝、断线/恢复、取消)落在引擎路径上;
- workspace 级切流 + 放量记录,会话路径亲缘;
- 引擎事件到 `EventEnvelope` 的映射与 SSE 兼容边界;
- 分层约束(入口层禁引擎具体导入)自动化。

Non-Goals:

- Task 生命周期状态机、持久化提交、outbox、控制命令(step6);
- A2A/定时任务 executor 的迁移(登记绕过,后续切片);
- 全局默认开关翻转与放量执行(由记录与运维决策);
- `hecate-contracts` 独立拆包(维持随 `hecate-runtime` 发行,待 step8 第二消费者出现再拆);
- 独立宿主侧改动(5c 已交付只读预览)。

## Decisions

### D1: 入口服务位置与形态 — `src/hecate/execution/entry_service.py`

新 `EntryExecutionService` 组合两个既有件:`WorkflowExecutionService`(平台 adapter,负责解析与共享装配调用)与 `TaskRunRegistry`(关联登记)。入口不再构造前者。理由:执行域(`src/hecate/execution/`)已拥有 backend 与 registry,入口编排属同一责任;放 `core/composition` 会把业务逻辑塞进装配层。替代方案(把登记塞进 `WorkflowExecutionService` 构造)被否:评估等内部场景要不同的关联模式,且会把 step6 的任务语义提前耦合进平台 adapter。

接口按调用方需要保持窄面:`execute(...)`(非流式)+ `execute_stream(...)`(异步生成器)+ 会话续接入口。两者都先登记(Task/Run/conversation 链接),再委托 adapter 执行,并把返回/事件流经事件映射层(见 D4)包装。登记失败的处置:业务入口 fail-open(执行继续,结果显式带 `correlation: missing` 标记并记 WARNING 日志)——登记是责任记录不是执行前置;这避免 registry 故障瘫痪聊天,同时不静默。

### D2: 关联模式按入口分档

- 业务入口(HTTP/MCP/IM):每请求创建 Task + Run 0001 + conversation 链接(`record_conversation_link` 既有)。
- 评估入口:评估运行开始创建单一内部 Task,条目执行各建 Run 挂到该 Task(`create_run` 复用 task_ref),不逐条目建 Task。
- 定时任务/A2A:未迁移,登记为绕过路径(见 D6)。

身份链:HTTP 入口用请求的认证主体(`AuthContext`),MCP 用工具会话主体,IM 用渠道身份映射;`IdentityChain` 构造复用 step4 既有 helper。Run 的 deployment 绑定走默认 builtin deployment(step4 已有的默认部署语义),不新增部署概念。

### D3: workspace 切流的解析与亲缘

解析顺序:workspace 覆盖(存在则生效)> 全局 `CHAT_TOOL_LOOP_ENGINE_ENABLED` > 默认 false。覆盖存储复用 workspace 级设置存储(与现有 workspace 设置同表/同机制,不新建表);每次变更写放量记录(独立审计事件,复用 audit-logs 既有能力,字段:workspace、actor、old→new、timestamp)。会话亲缘的实现:会话的首个工具轮次把生效路径记到会话元数据(如 session 扩展字段或事件日志首事件),续接/`session_resume` 读取该值而非重新解析开关;进行中的请求在进入时绑定路径,流中不变。理由:G3 关闭标准明确"按 workspace 切流,保留活跃 Run 路由",会话粒度是聊天场景"活跃"的最小单位。

### D4: 事件映射与 SSE 边界

映射层做纯转换:引擎流事件(`{"type":"message"...}`/`{"{"type":"values"...}`/工具事件)→ `EventEnvelope`(task_ref/run_ref 从 D1 的登记结果注入,source_sequence 单调,payload_schema_ref 指向既有 schema);原始事件放 envelope 的后端详情字段。tool 配对校验在映射层做(内存配对表,流结束未配对→显式 WARNING + envelope 标记)。OpenAI/SSE 转换保持在 `channel/api/v1/chat.py` 的适配函数(_stream_with_workflow 演进),但转换输入改为映射后事件;chunk 字段集合以既有 OpenAI chunk 模型为准,引擎内部字段在映射层即被过滤。理由:适配层只认平台契约,backend 专属内容到不了协议层。

### D5: 分层测试 — 复用既有 AST/import 探针模式

在 `tests/` 增分层测试:扫描 `src/hecate/channel/api/`、`src/hecate/tools/mcp/`、`src/hecate/channel/im/` 的 import,禁止 `PregelRuntime`/`GraphCompiler` 具体符号(经 `hecate_runtime` 或 `hecate.runtime` 均拦截)。复用 runtime self-sufficiency 探针的实现模式,不做新机制。

### D6: 迁移顺序与绕过登记

切换顺序(每次一组调用链,组内带回归):HTTP chat/agents → MCP(agent_chat/session_resume,补装配缺口)→ IM 注入(DI 换注入对象)→ 评估 workflow 执行。A2A executor 与定时任务 executor 登记为未迁移绕过路径:登记位置放计划文档 step5 清单旁注与 `docs/refactor/standalone-consumption-baseline.md` §6 缺口表(既有机制),不新建登记表。

### D7: 升级支持窗口矩阵

在 `docs/design/` 下新增执行栈契约/依赖矩阵文档:行 = 组合(平台 + hecate-runtime + hecate-runner),列 = 兼容边界、支持窗口、退出条件;CI 断言矩阵声明的版本边界与 workspace/lock 实际一致(读 `pyproject.toml`/`uv.lock` 的依赖 bound 比对矩阵)。首版矩阵只含当前 lock 内组合(全部受支持)与边界声明,不虚构历史版本。

## Risks / Trade-offs

- [登记 fail-open 可能产生无 Task/Run 的执行] → 结果显式标记 + WARNING 日志 + 测试钉住标记存在;step6 转持久化提交时再收紧为 fail-closed。
- [会话亲缘元数据与既有会话数据不兼容] → 只在新会话/新轮次写入;存量会话续接按开关解析并记录决策,不回填。
- [MCP 补齐装配后行为变化(新增事件/commit point 产出)] → 属一致性修复,回归按"与 HTTP 等价"断言;如外部消费方依赖"无事件"的旧状,在变更说明中显式标注。
- [评估入口关联改造触碰评估引擎热路径] → 评估回归套件(`ops/evaluation`)全量跑;登记走同一 fail-open 语义。
- [workspace 覆盖存储引入新查询路径] → 复用既有 workspace 设置读取,缓存策略与现有一致,不为开关新增独立缓存。

## Migration Plan

1. 先落入口服务 + 事件映射 + 分层测试(无行为变化,HTTP 入口换调用点但直连路径默认不变);
2. 逐链切换:HTTP → MCP → IM → 评估,每链一组真实入口回归;
3. workspace 覆盖 + 放量记录 + 会话亲缘(默认仍全局 false);
4. G3 真实入口测试套件补齐并记录证据指针;
5. 文档同步:计划 5c 勾选(#208)、矩阵文档、绕过登记。

回滚:每步独立成 commit;入口服务调用点可按入口回退到内联构造(保留原构造代码路径至 step19 清理),开关维度回滚 = 删除 workspace 覆盖值(回落全局)。

## Open Questions

- 取消(cancel)在 HTTP 聊天入口的协议表达(现有 API 无取消端点):G3 测试先覆盖引擎侧取消语义经入口服务的表现,HTTP 取消端点若需要,随 step6 控制命令一并设计。
