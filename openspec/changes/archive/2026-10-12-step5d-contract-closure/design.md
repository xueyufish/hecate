# Design: step5d-contract-closure

## D1 终态回写的单写者与词汇

`TaskRunRegistry` 仍是 Task/Run 唯一写入者:新增终态写入方法(成功:终态 + 结果摘要;失败:终态 + 停止类别 + 安全摘要),内部走既有 `update_projection` 的投影更新通道,不绕过注册表直写 ORM。终态词汇复用平台任务生命周期的既有终态(`succeeded`/`failed`),本能力不新增状态语义(spec 明确 MUST NOT 引入 step6 状态机语义——只引用既有终态词)。结果摘要为契约级:最终内容摘要(截断文本)与声明的产物引用;内部异常字符串只入服务端日志,注册表里的失败 detail 为类别 + 安全摘要(`str(exc)` 的截断形式与既有 `SubmitResult.reason` 同界)。

## D2 错误映射的类别面

聊天入口已交付的停止类别(`needs_review`/`budget_exhausted`/`cancelled`/`invalid_output`/`iteration_limit`)即平台错误类别面;本片把该类别从"响应呈现"扩展到"Task 投影回写":`EntryExecutionService.execute` 以 try/except 包裹执行——停止类异常映射类别回写失败终态,内部异常映射为 generic 类别(安全摘要固定为稳定文案),成功路径回写成功终态与结果摘要。响应/事件契约面统一携带类别 + task_ref/run_ref(已登记时);`hecate_correlation` 语义保持(缺失时仍显式)。`contracts/execution/errors.py` 的 `BackendError` 是 durable 后端契约的封闭错误分类,不强行套用于聊天入口——两者类别面不同,矩阵文档中说明关系。A2A 的稳定失败文案语义已有,入口服务的终态回写使其 Task 投影同步反映失败。

## D3 信封持久化的存储路径

`RunEventMapper` 增加可选 sink:每个映射出的 `EventEnvelope` 追加到所装配的 `EventStore`,以 run 作用域键(`session_id=f"run:{run_ref.id}"`)、`EventType.CUSTOM` + payload 携带信封 dict 落库——不改 `EventStore` ABC(EventType 枚举已含 CUSTOM),Postgres/in-memory 后端同构生效。分页读取(`page_events`)先按 run 键查存储,查无再回退进程内 `_envelopes`(内存后端/未持久化执行)。内存后端装配时行为等价于现状(进程内保留),持久后端装配时聊天信封跨请求可读——"持久后端 → 落库"由后端实例类型决定,不新增开关。

## D4 同步路径的信封对等

`WorkflowExecutionService._non_stream_execute` 内部 `async for event in runtime.execute(...)` 排空引擎事件——在该处收集原始事件并挂到返回结果(或经回调交给入口服务),入口服务以同一 `RunEventMapper` + sink 落信封。同步与流式由此走同一映射与持久化路径,分页视图一致。实现取最小侵入:收集以开关参数或在结果 dict 中附带 `engine_events`(后端详情保留语义已有先例),入口服务消费后剥离,不进入客户端响应。

## D5 定时 workflow 经入口服务

`ops/scheduling/executors.py::WorkflowExecutor` 重写:按 `workflow_id` 装载 workflow 定义,经 `EntryExecutionService`(agent_mode=workflow,entry_name 沿 scheduled 语义)执行,`CorrelationInput` 取任务创建者的 workspace/user;`result_summary` 绑定保持(输出摘要写入 `ScheduledTaskExecution`)。参照同文件 scheduled-agent 执行器的既有结构,不引入新装配面。`WorkflowTestRunner` 仅剩 studio `/workflows/{id}/test-run` 端点消费(有意保留);分层与回归确认定时路径不再 import 它。

## D6 A2A 默认部署解析

`_resolve_agent` 解析顺序:作用域 workspace 内未删除 agent 恰一个 → 沿用;多个 → 查 `AgentDeploymentModel.is_default` 于该 workspace 的部署,恰一个默认部署 → 其 agent;零个或不唯一 → 协议内拒绝(稳定文案"解析不唯一")。部署表已是显式登记面(`with_deployment=True` 的测试 fixture 已有默认部署行)。per-agent 主体身份已交付(actor=agent principal),本片不改。`docs/how-to/enable-a2a-server.md`、`docs/reference/env-vars.md` 的 `A2A_AGENT_WORKSPACE_ID` 语义补默认部署说明。

## D7 入口支持矩阵

`docs/design/entry-support-matrix.md`:行=入口(HTTP chat、agents chat、MCP agent_chat、A2A、IM、定时 agent、定时 workflow、评测、studio 测试),列=消费模式(平台内置安装、独立宿主、受管宿主),单元格=受支持/不受支持/未认证 + 证据指针(CI 作业/测试文件)。诚实缺口登记:A2A/IM 在独立/受管宿主的未认证、定时 workflow 独立宿主未认证、评测仅平台内置等。与 `execution-stack-compatibility-matrix.md` 交叉引用(版本窗口 vs 入口覆盖两个维度)。遵守 writing-style(不用数字/日期作描述性标记)。

## D8 不改动/排除

A2A 调用方→agent 协议映射、引擎 G2 恢复、IM/评测入口行为、`WorkflowTestRunner` 的 studio 语义、`EventStore` ABC 与 EventType 枚举、durable `BackendError` 分类、既有 `hecate_correlation` 字段语义。`update_projection` 的既有调用者(task dispatcher 投影)不受影响——终态写入是入口服务侧的新调用点。

## Risks

- 同步事件收集的内存开销——聊天单次执行事件量有界(既有事件日志),不做裁剪;超大执行的边界与引擎日志策略一致。
- run 作用域键复用 events 表——与 session 键无碰撞(前缀 `run:`),Postgres 索引按既有 session 索引生效;分页按时间序(append 顺序)读取。
- 定时 workflow 经入口服务后执行时间可能变长(治理/事件开销)——与 scheduled-agent 同界,调度器已有并发控制。
