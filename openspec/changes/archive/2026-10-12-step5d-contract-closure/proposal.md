# Proposal: step5d 收口——入口契约映射、事件持久视图与遗留路径(step5d-contract-closure)

## Why

演进计划 step5d 剩余两项:

1. "完成错误/产物到平台契约的映射,保留安全的后端详情;聊天事件接入持久存储,同步与流式成为同一 Task/Run 的视图。"
2. "收口定时 WorkflowExecutor 经 WorkflowTestRunner 的路径、A2A per-agent 身份及入口安装/升级支持矩阵。"

现状勘察(证据见 design):

- **入口失败无终态回写**:`TaskRunRegistry` 只有 `create_task`/`create_run`/`update_projection`,无终态写入;执行失败(停止类别或内部异常)时 Task/Run 停留在登记态,平台侧无从查询失败。`contracts/execution/errors.py` 的 `BackendError` 契约未被聊天/A2A 入口使用。
- **平台信封只存内存**:`RunEventMapper` 构造 `EventEnvelope` 但从不落库,分页读取只查进程内 `_envelopes`;持久后端(`EVENT_STORE_BACKEND=postgres`)已可用但聊天信封不进入。同步路径完全不经映射器。
- **流式不对称**:非流式以 `hecate_correlation` 暴露关联缺失,流式只记服务端日志;终止事件不携带 Task/Run 引用。
- **定时 workflow 走测试运行器**:`ops/scheduling/executors.py` 的 `WorkflowExecutor` 直接实例化 `WorkflowTestRunner.run_test`——不经入口服务,无 Task/Run 关联、无身份链、无治理/事件;同一运行器还被 studio 测试端点复用(那处是有意的)。
- **A2A 解析歧义**:per-agent 主体身份已接通(actor=agent principal),但 agent 解析仍是"作用域 workspace 内恰好一个 agent",多 agent 即拒绝;`a2a-protocol` 规格自declared退出条件为"显式的调用方→agent 映射"。
- **入口×安装/升级矩阵不存在**:现有 `execution-stack-compatibility-matrix.md` 只覆盖 wheel 栈版本窗口,没有逐入口 × 消费模式(平台内置/独立宿主/受管宿主)的支持矩阵。

## What Changes

1. **终态与错误映射**:`TaskRunRegistry` 增加终态写入(成功:结果摘要;失败:停止类别 + 安全摘要);`EntryExecutionService` 在执行成功/失败/停止后回写终态;聊天停止响应(409/SSE 终止事件)与非流式 `hecate_correlation` 一致携带 Task/Run 引用;内部异常细节保留在服务端日志,契约面只承载类别与引用(安全后端详情边界)。
2. **信封持久化与同视图**:映射后的平台信封写入所装配的事件存储(run 作用域键);分页读取先查存储、未持久化执行回退内存保留;同步路径把已排空的引擎事件经同一映射器落信封,使同步与流式对同一 Task/Run 产生可分页的一致视图。
3. **定时 workflow 收口**:`WorkflowExecutor` 改经 `EntryExecutionService`(与 scheduled-agent 执行器同构,agent_mode=workflow),获得 Task/Run 关联、身份链、治理与事件;`WorkflowTestRunner` 收缩为 studio 测试端点专用;`ScheduledTaskExecution.result_summary` 绑定语义不变。
4. **A2A 解析收口**:作用域 workspace 内按**默认部署**显式指定目标 agent(部署表为显式登记面),多 agent 无默认部署仍拒绝;拒绝语义与"不泄露内部细节"保持;调用方→agent 的协议级映射继续排除(登记为缺口)。
5. **入口支持矩阵**:新增 `docs/design/entry-support-matrix.md`——逐入口(HTTP/MCP/A2A/IM/定时 agent/定时 workflow/评测/studio 测试)× 消费模式(平台内置安装、独立宿主、受管宿主)× 安装/升级支持,附 CI 证据指针与诚实缺口;与 wheel 栈矩阵交叉引用;演进计划 step5d 剩余两项随之关闭。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `platform-entry-execution`:"入口执行关联统一 Task/Run"补充终态回写与流式/非流式关联对称场景;"引擎事件映射到平台契约"补充聊天信封持久化与同步路径信封场景;新增"入口安装/升级支持矩阵"需求。
- `scheduled-tasks`:新增"定时 Workflow 执行经平台入口路径"需求(结果绑定需求不变)。
- `a2a-protocol`:"A2A 执行的 agent 解析显式且按 workspace 界定"的解析语义从"恰好一个 agent"收口为"默认部署显式指定",保留拒绝与不泄露边界。

## Impact

- `src/hecate/execution/`:task_run_registry(终态写入)、entry_service(终态回写 + 同步事件收集)、entry_events(信封落库 sink + 分页读存储)。
- `src/hecate/channel/api/v1/chat.py`:停止响应携带关联引用;流式对称呈现。
- `src/hecate/ops/scheduling/executors.py`:WorkflowExecutor 重写为入口服务路径。
- `src/hecate/channel/a2a/server/executor.py`:默认部署解析;`docs/how-to/enable-a2a-server.md`、`docs/reference/env-vars.md` 语义更新。
- `docs/design/entry-support-matrix.md`(新)+ `docs/design/execution-stack-compatibility-matrix.md`(交叉引用)+ 演进计划。
- 测试:入口契约/信封持久化/定时 workflow/A2A 解析各组新增;studio 测试端点行为不变回归。

## 排除项(不冒充完成)

- A2A 调用方身份→agent 的协议级映射(卡片级多 agent 服务面)——独立协议演进。
- 引擎层 G2 持久恢复与跨轮回执内容收敛(已在 g3 决策记录登记为翻转前置)。
- IM/评测入口行为改动(它们已收敛;本片只在矩阵中如实登记)。
- 信封 schema 版本化与跨进程分页续读的分布式一致性(以单写者 + 存储有序追加为界)。
