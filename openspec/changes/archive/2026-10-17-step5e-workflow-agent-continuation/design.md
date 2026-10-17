# Design — step5e-workflow-agent-continuation

## Context

#261 交付了单 Agent 的持久审批续跑:类型化等待信号停车、pending 绑定、决定门唤醒、原生续跑与派发时一次性消费。组合链缺三段接线(均已核实):①`stamp_step_input` 的白名单输入无 `approval_wait`,子任务审批链不启用;②系统发起执行 `user_id=None`,`execution_assembly` 的 tenant provider 返回 None,`SessionStateMaterializer.save` 直接跳过持久化(`execution_assembly.py:319-329`),恢复侧 `_resume_engine_session` 亦以 None 键加载必失败;③子任务拒批终态写在 `task_control._apply_approval_rejected`,不经 `dispatcher._finish` 的 `_issue_child_callback`,父流程等不到收敛。见 proposal.md - Why。

## Goals / Non-Goals

**Goals**:声明驱动的子任务审批、可信系统快照归属、拒批收敛、组合进程重启证据;既有产物校验/回调幂等/失败策略语义零改动。

**Non-Goals**:独立 workflow 的 Run 身份;Runner 自主选择;审批 UI/HTTP 面;父流程等待期限策略(维持现状无期限);多级嵌套 workflow 审批矩阵。

## Decisions

### D1: 审批声明走既有步骤声明通道

`WorkflowPlan` 步骤字段新增 `approval_wait: bool`(默认 False);`_validate_step` 校验布尔类型(非布尔显式拒绝);`stamp_step_input` 将其写入子任务输入(`approval_wait=step.get("approval_wait") is True`)。提交白名单(`task_control.submit` 的 `persisted_input`)已有该键通道(#261 引入),子派发装配 `_wire_approval_backend` 据 `context.approval_wait` 启用——无装配改动,只有声明与盖章两处。与 `mode`/`output_schema`/`artifact` 同通道:显式声明、向后兼容、可重放(同计划重新解析产生同形状)。

### D2: 系统执行的合成租户键从 agent principal 派生

保存侧与恢复侧共用一个纯函数 `system_execution_tenant(agent_id) -> UUID`(如 `uuid5(NAMESPACE_URL, f"hecate:agent-principal:{agent_id}")`,落在 execution_assembly 或共享模块):

- **保存侧**:`execution_assembly` 组装时,`user_id is None` 且执行上下文携带服务端验证的 agent 归属 → tenant provider 返回 `(derived, derived)`;两者皆无 → 维持 None(跳过持久化,现状)。agent 归属来源:任务派发路径的 `context.agent_id`(从持久化任务载荷解析,提交时经 `submit` 的 agent/workspace 校验);不从请求消息或工具输入取值。
- **恢复侧**:`_resume_engine_session` 的加载键同样分支——`context.user_id` 优先,否则用同一派生函数与 `context.agent_id`。两侧同源(同一持久化载荷的同一字段),键推导按定义一致。
- 不写入人类用户表、不与真实用户 UUID 空间碰撞(uuid5 于独立命名空间);快照归属仅是存储键,授权仍由审批绑定/唤醒门独立把持——租户键不构成任何授权事实。
- 备选:为系统执行引入显式 `org/user` 字段贯穿载荷——改动面大且与既有 `user_id=None` 语义冲突;派生键是最小接线。

### D3: 拒批终态进入既有核实回调链

`_apply_approval_rejected` 完成终态写入后,检测子任务持久化输入的 `workflow_parent` 盖章,经 `dispatcher._issue_child_callback` 同一核实路径发出回调(声明状态=failed、来源=approval_rejected)。回调幂等/核实语义(同 workspace、状态一致、一次性 token)全部复用。终态已提交而内存回调失败的窗口，由同事务落库的 `run_terminal` 经已有 `PlatformOutboxProjector` 重放；平台事件已投影也不能跳过尚未成功的回调。回调异常继续交给 relay 的持久重试/显式 gap 机制，不新建回调通道。自动回调统一使用提交事实，命令 ID 绑定完整父等待 token，避免两个入口的摘要差异冲突。失败来源与子任务执行失败可区分(回调载荷携带 `error_category: approval_rejected`,父 `_execute_workflow` 的既有来源分支照常路由 on_failure)。

### D4: 组合重启 E2E 复用 worker 子进程框架

复用 #261 的子进程模式(`worker_process_e2e` 形态):父任务以两步 workflow 提交(步骤 0 声明 `approval_wait` + 产物),父/子 worker 使用独立 OS 进程，禁用测试装配的 inline spawn，分别认领对应任务。窗口 (a) 只重启子 worker(停车后 kill,审批续跑);窗口 (b) 父等待中先重启父 worker，再重启子 worker(关联与流程位置恢复)。Windows 使用完整进程树终止，PID 文件记录实际执行进程，不能用虚拟环境启动器 PID 代替。断言:副作用计数恰一次、审批绑定消费一次、父推进恰一步、事件/回调可查。真实模型变体沿用 env 门控与记录落盘。

### D5: 确定性层不动既有断言

SQLite 确定性套件:声明传递(盖章进输入)、父等待保持(子 waiting_approval 期间父状态与等待契约不变)、拒批收敛(on_failure=fail/await 两分支)、伪造回调拒绝(既有核实语义反例)。既有 workflow 子任务契约套件、持久审批套件不改断言全绿。

## Risks / Trade-offs

- [合成租户键与真实用户键碰撞] → uuid5 独立命名空间 + 前缀字符串域;键不参与授权。
- [拒批回调与既有回调语义漂移] → 不新建通道,复用 `_issue_child_callback` 的核实与幂等;反例覆盖"waiting 中伪造成功回调被拒"。
- [父无期限等待在子绑定过期后悬置] → 当前 worker 不扫描 waiting 任务的绑定到期；过期唤醒被拒绝且 token 不消费，不能声称会自动进入重试预算或收敛父流程。等待期限与自动到期收敛维持开放，不在本 change 的拒批决定/合法批准续跑验收内。
- [E2E 双进程窗口在 CI 的时长] → 沿用 #261 的 420s 预算与文件轮询模式;Linux CI 无 Docker 端口代理问题。

## Migration Plan

纯增量:新声明字段可选、合成键仅作用于 user_id=None 分支、拒批回调是新增收敛路径。无 schema 迁移;回滚 = 不声明 `approval_wait`(子任务行为回到现状)。旧持久化数据不受影响(无 `approval_wait` 输入即未启用;无系统快照即维持不可续跑的现状语义)。

## Open Questions

(无——步骤声明键名 `approval_wait` 与既有载荷通道一致;派生键的具体哈希形式实现时定,不影响规格与任务拆分。)
