# Design

## Context

父-子等待/回调的两端原语均已存在:父端 `TaskWaitingSignalError` 挂等待(契约 `await_task_ref`),子端 `submit_workflow_callback` 核实平台记录后以 `provide_input` 唤醒(一次性 token、幂等回执、拒绝路径完整)。缺口只在发起端:没有任何生产代码提交子任务并挂入该等待——现有验证靠 monkeypatch 替换 `PlatformTaskDispatcher._execute`。

子任务提交面已知可用:`TaskControlService.submit(workspace_id, user_id=None(系统发起), goal, agent_id, input)`(原型第一步)。自动回调面:`submit_workflow_callback` 校验完全基于平台持久记录,平台自身作为 issuer 调用在语义上与外部调用者等价(HTTP 端点即它的一层薄壳)。

## Goals / Non-Goals

**Goals:**

1. 具名 adapter 提供确定性多步子任务编排:声明式步骤、真实 submit、持久等待、自动核实回调、顺序推进。
2. 双端重启验收:父等待、子终态、跨进程回调各自跨重启,链条收敛且零重复提交。
3. 全程真实路径:测试不 monkeypatch `_execute`;monkeypatch 原语测试保留为契约层验证。

**Non-Goals:**

- studio 工作流 DSL 的节点类型扩展(编排产品入口,后续 change)。
- 跨 workspace 子任务、并行扇出、动态(非声明式)步骤。
- 回调 HTTP 端点的行为变更(外部调用者契约不变)。

## Decisions

1. **发起端形态:任务输入声明式步骤 + dispatcher 接入 adapter**,而非平台 builtin 工具。理由:工具层无法触达 TaskControlService 的 store/session_factory(注册表只有 db),而 dispatcher 是唯一同时持有提交面、等待信号与租约边界的组件;声明式步骤(非 LLM 决定)满足"确定性 workflow 节点或具名 adapter"的门,且把编排事实留在平台持久载荷里(重启可恢复)。载荷形态:`input["workflow"] = {"steps": [{"goal": str, "input": {...}}, ...], "on_failure": "fail" | "await"}`。第一版步骤只含 goal/input(子任务沿用父任务的 agent 与主体);扩展字段(每步 agent 覆盖)留待需要时加。
2. **自动回调挂在子任务终态路径(`_finish`)**:子任务输入载荷被 adapter 盖章 `workflow_parent` 元数据(parent task_ref);`_finish` 落终态后读自身载荷发现父引用 → 经 TaskControlService.submit_workflow_callback(issuer="workflow-callback",command_id=`wf-callback-<child_id>`)发起。幂等由既有 command 语义保证;回调失败(如父已不在等待)记录日志不阻断子任务终态。
3. **步骤推进 = 唤醒后重新进入 `_execute`**:父被 PROVIDE_INPUT 唤醒 → requeue → dispatcher 重新 `_execute`;adapter 从持久输入读"已完成步数"(唤醒载荷携带 `child_outcome`),提交下一步或(最后一步)返回成功结果。等待契约精确绑定当前步的子任务,天然排除跨步错配。
4. **失败收敛**:`on_failure=fail`(默认):回调核实的 child_state=failed 时 adapter 不再提交下一步、返回失败结果,父任务失败;`on_failure=await`:父任务转 `reconciliation_required`(人工裁决)。子任务自身的重试语义沿用平台既有 dispatcher 行为,不在 adapter 内重试。
5. **agent 归属**:子任务沿用父任务的 agent_id(workspace 一致性由平台记录核实);系统发起 `user_id=None`。

## Risks / Trade-offs

- 声明式步骤使单任务承载多子任务,重入路径(唤醒后 `_execute` 重入)必须幂等:推进计数以持久输入为准,不落在进程内存——测试覆盖唤醒后重入。
- `_finish` 读自身载荷发现父引用:子任务输入是平台持久事实,重启后仍在;回调幂等回执保证重复 `_finish`(不应发生)无副作用。
- 等待过期(契约 expires_in_seconds)沿用既有等待过期语义,不新增。

## Open Questions

(无——自动回调 issuer、失败收敛、载荷形态均在上方定案;studio DSL 节点类型明确排除。)
