## Purpose

执行回放与调试面板：在 1.3.19 事件日志（Log-as-Truth）之上提供只读回放产品——按 session 读取事件日志、按 trace 分段组装执行时间线，支持 time-travel 状态检查与 DAG 步进回放，为 Pregel 路径执行提供排障与审计能力。词汇模型为 `session`（多轮容器）→ `trace`（一次执行，回放锚点）→ `event`（记录）；不引入 "run" 概念。

## Requirements

### Requirement: 回放时间线 API

系统 SHALL 提供只读回放端点：给定 session_id，返回该 session 的执行时间线，按 trace（一次执行的 `Event.trace_id` 分片）分段。每个 trace 分段 SHALL 包含其事件序列（superstep 边界、`NODE_START`/`NODE_END`、`CHANNEL_WRITE`（含值）、`TOOL_CALL`/`TOOL_RESULT`、`LLM_REQUEST`/`LLM_RESPONSE`、`ERROR`、`INTERRUPT`、`RESUME`、`SUBGRAPH_START`/`SUBGRAPH_END`），并 SHALL 关联 traces 表中的 timing（latency、ttft）与 usage（tokens/cost）数据（可用时）。

#### Scenario: 按会话获取回放时间线
- **WHEN** 调用回放端点并指定一个存在事件日志的 session_id
- **THEN** 系统 SHALL 返回按 trace 分段的时间线，段内事件按 version 升序，每条事件携带 event_type、superstep、node_id、timestamp、payload、version、trace_id

#### Scenario: 多轮执行的 trace 分段
- **WHEN** 一个 session 的日志包含多次执行（多个非零且互不相同的 trace_id）
- **THEN** 时间线 SHALL 按执行分段呈现，段顺序按首个事件的 version 升序

#### Scenario: trace_id 缺失或退化的事件归属
- **WHEN** 事件因历史数据或 identity 退化导致 trace_id 为空或全零
- **THEN** 该类事件 SHALL 归入明确的"未分段"（unattributed）区，不得被静默丢弃

#### Scenario: 大日志分页
- **WHEN** session 的事件数超过单页上限（默认 500，可调）
- **THEN** 回放端点 SHALL 支持 version 游标分页，保证跨页事件顺序稳定

#### Scenario: 不存在的会话
- **WHEN** 指定的 session_id 不存在
- **THEN** 系统 SHALL 返回 404

### Requirement: LLM 与工具事件的正文关联

回放时间线 SHALL 将 `LLM_RESPONSE` / `TOOL_RESULT`（payload 仅含长度）与紧随其后写入 messages channel 的 `CHANNEL_WRITE` 值关联，使时间线能展示"LLM 实际说了什么 / 工具实际返回了什么"。关联规则 SHALL 与引擎的 fold 语义一致（同一 `ChannelBehavior.write` 投影）。

#### Scenario: LLM 响应正文展示
- **WHEN** 时间线包含 LLM_RESPONSE 事件且其后存在 messages channel 的 CHANNEL_WRITE
- **THEN** 回放数据 SHALL 提供该次写入的消息列表，供 UI 在 LLM_RESPONSE 明细中展示正文

#### Scenario: 工具结果正文展示
- **WHEN** 时间线包含 TOOL_RESULT 事件且其后存在 messages channel 的 CHANNEL_WRITE
- **THEN** 回放数据 SHALL 提供对应的 tool role 消息内容（含 is_error 标记）

### Requirement: guardrail 结果推导呈现

Phase 1 系统 SHALL 从 messages channel 中guardrail 拦截产生的合成消息（如 `Tool blocked: ...`、`Tool denied by access policy`）推导 guardrail 拦截项，并在时间线中以独立维度标注。推导 SHALL 为只读投影，不写入事件日志。1.3.5i E3 waterfall 中间件的 stage 事件落地后，本维度 SHALL 升级为直接消费事件，API 形状保持不变。

#### Scenario: 拦截推导
- **WHEN** messages channel 中存在 is_error 的 tool role 合成消息且内容匹配拦截标记
- **THEN** 时间线 SHALL 在对应位置标注 guardrail 拦截项，含原因文本

#### Scenario: 无拦截时静默
- **WHEN** 执行中无 guardrail 拦截
- **THEN** guardrail 维度 SHALL 为空，不产生占位噪音

### Requirement: time-travel 状态检查

系统 SHALL 提供检查点查询：给定 session_id 与目标 version，从日志 fold 到该点并返回当时的状态投影，其中 SHALL 包含 `derive_messages()` 的模型可见消息列表。检查点 SHALL 只读，不产生任何写入。fold SHALL 复用引擎的 fold 语义；遇到 `log_schema_version` 低于当前版本的事件 SHALL 返回明确的不可回放前缀错误（含停止位置），不得返回错误状态。

#### Scenario: 检查"当时模型看到了什么"
- **WHEN** 请求指定 session_id 与一个有效 version N（某个 `STEP_END` 的 version）
- **THEN** 系统 SHALL 返回 fold 到 N 之后的 channel 状态摘要与模型可见消息列表

#### Scenario: 指向非提交点
- **WHEN** 请求的 version 落在未闭合的 superstep 中间（无对应 STEP_END）
- **THEN** 系统 SHALL 回退到不大于 N 的最后一个提交点，并在响应中标注实际使用的 version

#### Scenario: 不可回放前缀
- **WHEN** 日志包含 `log_schema_version` 低于当前版本的事件
- **THEN** 系统 SHALL 返回 422 与不可回放的停止位置信息

### Requirement: 子图链接跳转

回放时间线中的 `SUBGRAPH_START` / `SUBGRAPH_END` 事件 SHALL 携带 child_session_id 并在 UI 中呈现为可跳转链接，点击后进入子 session 的独立回放视图。子图回放与父图使用同一回放能力，不引入新的 API 形状。

#### Scenario: 跳转到子图回放
- **WHEN** 用户在时间线中点击 SUBGRAPH_START 事件
- **THEN** UI SHALL 导航到该 child_session_id 的回放视图，且该视图与父图回放功能一致

#### Scenario: 子 session 无权限或不存在
- **WHEN** 目标 child_session_id 不可访问
- **THEN** 跳转 SHALL 失败并显示原因，不影响父图回放

### Requirement: 覆盖范围如实标注

回放能力 SHALL 如实呈现覆盖边界：覆盖范围 = Pregel 执行路径（workflow/graph 执行、增强聊天）。回放视图中 SHALL 展示持久横幅，说明 path A（agent-tools 直连循环）与 path C（纯文本透传）的调用不在日志内。

#### Scenario: 覆盖横幅
- **WHEN** 用户打开任一回放视图
- **THEN** UI SHALL 展示覆盖范围横幅（Pregel 路径覆盖说明）

#### Scenario: 空日志会话隐藏入口
- **WHEN** 会话的事件日志为空（version 为 0，如 path A/C 会话）
- **THEN** UI SHALL 不渲染"执行回放" tab，而非渲染空视图

### Requirement: DAG 回放视图

回放视图 SHALL 提供 DAG 步进回放：以 agent/workflow 的当前图定义绘制拓扑，按时间线的 superstep 前进/后退高亮已执行节点与活跃 channel 写入。视图 SHALL 标注"拓扑取自当前定义版本"——图定义在执行后被编辑时，拓扑可能与日志不一致（version binding 属 Phase 2）。

#### Scenario: 步进高亮
- **WHEN** 用户在时间线中移动到某个 superstep
- **THEN** DAG SHALL 高亮该 superstep 内 NODE_START→NODE_END 的节点，以及该步的 CHANNEL_WRITE 目标 channel

#### Scenario: 拓扑版本标注
- **WHEN** DAG 视图渲染
- **THEN** UI SHALL 展示拓扑来源为当前定义版本的说明

#### Scenario: 日志引用了拓扑中不存在的节点
- **WHEN** 事件携带的 node_id 不在当前图定义中
- **THEN** DAG SHALL 以"未识别节点"占位呈现，不得报错中断回放

### Requirement: 回放访问的租户隔离

回放端点 SHALL 按租户（org/workspace）过滤数据，用户只能回放其有权访问的会话；跨租户访问 SHALL 返回 404。

#### Scenario: 越权访问
- **WHEN** 用户请求不属于其租户的 session 的回放
- **THEN** 系统 SHALL 返回 404
