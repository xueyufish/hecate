# Proposal

## Why

演进方案 step6c(持久等待)未接通:平台的 `PlatformTaskDispatcher` 已有持久等待进入语义(`TaskWaitingSignalError` → WAITING_INPUT/WAITING_APPROVAL + 一次性 `wait_token` + 期限),durable 契约与 SQL 存储支持命令回执(PROVIDE_INPUT/RESUME/PAUSE/RESUME,requested→applied/rejected,过期校验,`applied_command_id` 原子消费)且等待态迁移(waiting→queued/cancelled)合法;但 Runner 没有任何等待路径——固定图只会一路执行到终态,审批工具在 profile 加载期被整体拒绝,server 没有 provide-input/resume 唤醒入口,等待中的任务也无法被合法唤醒后以新 attempt Run 继续。业务 App 在独立模式下无法实现"请求补充输入/等待审批后再执行"的可靠流程。

本 change 是迭代 4 的落地切片:把等待状态机接到宿主执行与命令面,step7 提供合法审批判定,本 change 提供可靠等待。

## What Changes

- **等待进入**:durable profile 允许 manifest 声明的 `approval_required` 工具与业务 API 的 `input_required` 结果进入等待——保护动作在动作台账记录意图后不执行,任务持久进入 `waiting_approval`(审批契约)或 `waiting_input`(输入契约);等待记录绑定原 Task/Run、工具与参数摘要、契约引用、一次性 `wait_token` 与期限。
- **唤醒命令面**:宿主 HTTP 新增授权唤醒入口(提交 `provide_input`/`resume` 命令,携带 `command_id`、`wait_token`、输入载荷),经既有 server-verified 身份;命令按 `command_id` 幂等记录,独立回执(requested/applied/rejected)。
- **一次性唤醒应用**:唤醒在单事务内校验命令(期限、kind、任务绑定)与等待记录(token 匹配、未消费、未过期),消费 `wait_token`、合并输入载荷到任务输入、状态 `waiting_*→queued` 并重绑新 attempt Run、命令置 `applied` + `command_state` 事件;过期/重复/错 token 的唤醒得到显式 `rejected` 回执,不派发任何工具。
- **调度收敛**:唤醒后的任务由既有串行调度以新 attempt Run 重新驱动;重启后等待任务保持等待(不被自动执行),直到合法唤醒。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `standalone-runner-host`:宿主新增持久等待进入(审批/输入契约)与一次性唤醒命令面;等待记录绑定、原子消费、幂等回执、重启保持等待。

## Impact

- `packages/hecate-runner`:`profile.py`(durable profile 允许 `approval_required` 工具,执行机制由本 change 提供)、`engine.py`(派发边界进入等待:审批工具 raise 等待信号、`input_required` 结果转入等待;`_execute` 捕获信号并持久化等待记录;唤醒应用与重绑)、`durable.py`(等待记录与唤醒应用封装)、`server.py`(授权唤醒路由)、`__main__.py`(装配不变,调度器自动拾取唤醒后的 QUEUED 任务)。
- `packages/hecate-durable`:无契约改动(命令/等待原语已存在;如需 `WAITING_*→queued` 时重绑 run 的辅助仅复用 `apply_task_state(event_run_ref=...)`)。
- 测试:等待进入(审批/输入)、唤醒应用(输入合并、新 attempt Run、命令回执)、过期/重复/错 token 拒绝且零工具派发、强制重启后仍等待并被合法唤醒;既有回归。
- 不扩展:审批判定策略与平台审批服务(step7)、受管通道的命令下发(managed 命令投递属后续切片;受管运行的等待进入与本地唤醒机制与 standalone 共用)、checkpoint 恢复(迭代 5)、工作流回调。
