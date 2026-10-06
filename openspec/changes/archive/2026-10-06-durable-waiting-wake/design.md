# Design

## Context

durable 契约与存储已具备等待原语:`TaskLifecycleState.WAITING_INPUT/WAITING_APPROVAL`、`ControlCommandKind.PROVIDE_INPUT/RESUME`、独立命令回执(requested/applied/rejected、过期校验、幂等 command_id)、`apply_task_state(applied_command_id=...)` 的原子命令消费、以及 waiting→queued/cancelled 的合法迁移。平台侧 `PlatformTaskDispatcher._park` 已示范等待记录形状(`extra["wait"] = {wake_kind, contract_ref, wait_token, wait_expires_at, consumed}`)。缺口全在 Runner:固定图没有等待入口,profile 加载期直接拒绝 `approval_required` 工具,server 无唤醒路由,唤醒后的重驱动与新 attempt Run 无路径。

## Goals

- 等待是宿主持久任务状态机的第一类成员:进入、保持(含跨重启)、一次性唤醒、幂等回执全部落在既有 durable 命令/迁移原语上,不新增第二套等待机制。

## Non-Goals

- 审批判定策略(谁可批、策略版本、审批服务对接)——step7;本 change 的唤醒即业务 App/本地操作员的显式决定记录(命令回执即审计事实)。
- 受管通道的命令下发(managed wake delivery)——后续切片;受管运行在本宿主的等待进入/本地唤醒与本 change 共用。
- checkpoint 恢复(迭代 5)、工作流回调(迭代 6)。
- 不引入新 Port/Protocol。

## Decisions

1. **等待进入在派发边界,不在图内。**`_dispatch_tool` 两种进入路径:(a) manifest 声明 `approval_required` 的工具——在动作台账记录意图(claim)后不执行业务调用,转为等待;(b) 业务 API 返回 `{"status": "input_required", ...}`(契约引用随结果携带)——同样转入等待。两者都以等待信号异常离开 `_execute`,由 `finally` 前的捕获持久化等待记录并释放串行槽。进入等待与任务状态迁移同事务(经 `DurableRuntime` 的新封装),`run_input` 已持久化、无需快照。
2. **profile 门更新**:durable profile 允许 `approval_required` 工具(等待机制即本 change 的绑定;判定归 step7);preview(非 durable)profile 仍拒绝——无持久化就没有可靠等待。等待记录与平台 `_park` 同形(`wake_kind`/`contract_ref`/`wait_token`/`wait_expires_at`/`consumed`),工具名与参数摘要进 `contract_ref`,满足"绑定原 Task/Run、参数摘要、审批或输入契约及期限"。
3. **唤醒 = 命令 + 原子应用。**server 新增授权路由(复用既有 bearer 身份):记录命令(幂等 command_id,重放返回原回执)→ 单事务应用:命令期限校验、等待记录校验(token 匹配且未消费、未过期)→ 消费 token、合并 `payload.input` 到任务输入(`apply_task_state(input_payload=...)`)、状态迁移 `waiting_*→queued` 并以 `event_run_ref` 重绑新 attempt Run、`applied_command_id` 原子置命令 APPLIED + `command_state` 事件。任何校验失败 → 命令置 `rejected`(留原因),零工具派发。重复唤醒(同 command_id)返回既有回执,不重复应用。
4. **唤醒后重驱动 = 新 attempt Run。**遵循"重试/唤醒是新 Run"规则:唤醒生成新 run_ref 并重绑任务,输入载荷含合并后的 provided 输入;串行调度器按既有 QUEUED 轮询拾取,动作台账按新 run 的键仲裁(旧 attempt 的已决动作经台账语义不重做)。
5. **重启语义复用既有过滤**:调度器只拾取 QUEUED/RUNNING,WAITING_* 天然不被自动执行;等待记录在 durable 行中,重启后仍可被合法唤醒。强制终止(非优雅关闭)后的已领取未决动作照旧走对账,与等待无关。

## Risks

- 等待任务占用任务槽位但不占串行槽(等待即不执行),队列吞吐不受影响;大量等待行只是持久状态,留存策略已有既有边界。
- `input_required` 是业务 API 的新约定结果:契约引用由业务方在结果中携带,宿主只透传与持久化,不解释语义——避免宿主内嵌业务输入契约。
- 唤醒路由暴露 wait_token:token 仅经授权查询(run 属主身份)可见,一次性消费后失效;泄露窗口与既有 cancel/shutdown token 同级。
