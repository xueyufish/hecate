# Proposal

## Why

Step6e 的生产发起端缺失:平台已有工作流回调的完整消费端(子任务事实核实、一次性 token、幂等回执),但没有任何确定性入口真正提交子任务并持久等待——现有验证全部依赖 monkeypatch 替换 `PlatformTaskDispatcher._execute`(只证明原语)。父-子等待/回调原语因此从未在生产路径上闭环。

## What Changes

- 具名 adapter(`WorkflowChildTaskAdapter`,execution 层):平台任务的输入载荷可声明确定性子任务步骤序列(`workflow.steps`,非 LLM 决定);dispatcher 在执行时经 adapter 逐步驱动——每步通过真实 `TaskControlService.submit` 提交子任务(系统发起,载荷加盖父任务引用),随后以 `TaskWaitingSignalError` 挂入持久等待(契约 `await_task_ref` 绑定该子任务)。
- 子终态自动回调:子任务到达终态时,dispatcher `_finish` 读取子任务载荷中的父引用元数据,自动经真实 `submit_workflow_callback`(平台 issuer)发起核实回调——父任务被唤醒、子结果摘要骑 `provide_input` 进入下一步;无外部调用者参与,HTTP 回调端点保留给外部编排器。
- 多步序列:第 N+1 步仅在第 N 步的核实结果随唤醒进入后才提交;任一步失败按既有等待/失败语义收敛(声明 `on_failure` 为 `fail` 时父任务失败,`await` 时挂待对账/等待)。
- 进程重启验收:父等待事实、子终态事实、跨进程发出的自动回调各自跨重启保持;父平台进程与子平台进程重启组合下链条仍收敛。
- 明确不做:studio 工作流 DSL 的节点类型扩展(编排产品入口属后续)、跨 workspace 子任务、并行子任务扇出。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `platform-task-control`:将占位的"具名 adapter"需求具体化——声明式子任务步骤的提交/等待/自动回调语义、多步序列与重启组合的验收场景。

## Impact

- `src/hecate/execution/task_dispatcher.py`:`_execute`/`_finish` 接入 adapter 与子终态回调钩子;`TaskWaitingSignalError` 契约复用(`await_task_ref`)。
- 新增 `src/hecate/execution/workflow_child.py`(具名 adapter:步骤解析、子任务提交、父引用元数据盖章)。
- `src/hecate/execution/task_control.py`:`submit_workflow_callback` 复用(平台 issuer);`submit` 允许系统发起路径携带编排元数据。
- 测试:`tests/test_execution/` 新增 adapter 集成测试(真实 dispatcher,无 `_execute` monkeypatch):单步/多步/失败收敛/双端重启;既有 monkeypatch 原语测试保留为契约层验证。
