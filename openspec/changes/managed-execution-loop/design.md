# Design

## Context

Iteration 1 与 step6 复核已交付:平台 `ManagedDeliveryService`(持久投递意图、幂等 accept、pending 才重投)与 `ManagedProjectionService`(event_id 去重、内容冲突拒绝、来源/Run 投影、终态不回滚);宿主 `ManagedChannel`(注册→凭据→pull/accept→按 Run 游标上传)、`LeaseGate`、durable store(`submit_task` 幂等接收队列、状态迁移发 `task_state`/`run_terminal` 事件)、引擎串行槽与 durable replay。缺口是宿主装配:CLI 拒绝 `control_plane`,接受后的任务无人执行,重启会被 standalone replay 误对账,`finish_run` 不发 `run_terminal`。

## Goals

- 把已有原语装配成"投递→接受→串行执行→事件/结果上传→投影"的闭环;接受/执行状态分离、幂等重投、重启恢复、断线补传全部落在既有幂等机制上,不新增第二套状态机。

## Non-Goals

- 平台任务提交面 → `queue_delivery` 的生产路由(平台侧派发策略属后续平台集成;`ManagedDeliveryService` 保持唯一写方)。
- 动作时受管 LeaseGate 强制(step6b)、持久等待唤醒(step6c)、checkpoint 恢复(step6d)、工作流回调(step6e)、安装制品进程级故障矩阵(step6f)。
- 不引入新的 Port/Protocol/ABC;全部是既有具体类型的组装与普通方法。

## Decisions

1. **受管调度复用引擎串行槽与 durable replay 机制,不走新执行器。**新增 `ExecutionEngine.resume_managed(task_ref, run_ref)`:校验任务 issuer 为受管约定(`managed-host`)、状态为 QUEUED/RUNNING,然后与 `resume()` 共享"串行槽 + 动作台账 + `finish_run`"的驱动路径。standalone `resume()` 对受管 issuer 直接返回 None——两个恢复入口互斥,避免同一任务被两条路径同时认领或被误置待对账。
2. **受管身份在接受时打点,执行时校验。**`ManagedChannel._accept_delivery` 持久化的输入载荷带 `_host_identity = {principal: "managed:<trust_root>", domains: data_domains}`;`resume_managed` 校验打点与引擎当前受管身份配置一致,不一致转 `reconciliation_required`(与 standalone replay 的身份失效语义一致)。`control_plane.data_domains` 默认空——工具派发沿用现有域检查,默认拒绝;动作时租约强制留给 step6b。
3. **幂等键只绑投递内容,身份打点不进摘要。**`submit_task` 的 request digest 继续只覆盖 `delivery_row_id` + 投递 `input_payload`,同投递跨重启/跨配置重投仍幂等返回原 Task/Run;打点是宿主本地溯源字段,不参与冲突判定。
4. **终态经 `run_terminal` 事件上传。**`DurableRuntime.finish_run` 增加 result 载荷并传 `terminal_payload` 给 `apply_task_state`,durable 事件日志获得与状态迁移同事务的 `run_terminal`;平台 `_update_run_projection` 已支持折叠该载荷,平台侧零改动。
5. **CLI 装配在 `__main__` 组装,循环各自独立。**通道循环(upload→pull→sleep)与受管调度循环(drain→sleep)是两个 asyncio 任务,共享引擎串行槽天然互斥;启动 drain 复用现有 `_schedule_pending_resumes` 并按 issuer 分流:standalone 任务走原 replay,受管任务交给调度循环。关闭顺序:停止拉取/调度 → 引擎诚实收敛 → 尽力一次最终上传 → 释放资源。
6. **注册失败不阻断启动。**平台不可达/未准入时通道循环自行降级重试(既有语义),宿主继续服务本地入口;接受与执行只在投递到达后发生。

## Risks

- 受管任务与 standalone 任务共享同一 durable 库与串行槽:以 issuer 约定分流,误配(如删除 `control_plane` 后遗留受管任务)时调度缺席、任务保持原状并可重新配置恢复——standalone replay 明确跳过它们,不产生错误对账。
- `run_terminal` 上传依赖通道可用性:上传失败本地事实不受影响,游标推进前平台投影滞后,属于既定"上传中断不阻塞宿主本地执行"语义。
