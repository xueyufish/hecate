# Proposal

## Why

演进方案 step6a(受管执行闭环)仍未接通:宿主 CLI 对含 `control_plane` 的配置明确拒绝启动;受管投递被接受(本地 QUEUED)后没有任何执行调度;`finish_run` 不发 `run_terminal`,平台投影拿不到真实终态与错误;受管任务的持久化输入缺少宿主身份打点,进程重启后会被 standalone replay 路径误判为身份失效并错误转入 `reconciliation_required`,违反"持久接受后宕机,重启只恢复同一 Task/Run"。Iteration 1 已交付平台投递/投影服务与宿主通道原语(库级),本 change 把它们装配成闭环。

## What Changes

- **CLI 受管装配**:`hecate-runner` CLI 在 `control_plane` 与 durable profile 同时配置时装配 `ManagedChannel` 与受管执行调度;`control_plane` 缺 durable 时启动失败;无 `control_plane` 的独立行为保持不变。
- **接受与执行状态分离**:接受(幂等 `submit_task`)只产生 QUEUED 接收记录与接受回执;RUNNING/终态只能由实际执行经 durable 状态迁移产生;接受回执不伪报执行。
- **串行执行调度**:新增受管调度循环,复用引擎串行槽与 durable replay/ledger 路径,逐个驱动已接受任务;受管任务经专门的 `resume_managed` 入口执行,standalone replay 跳过受管任务(不误对账)。
- **执行事件与结果上传**:任务终态迁移携带 `run_terminal` 载荷(status/error/content),上传循环把 `task_state`/`run_terminal` 事件按 Run 游标补传到平台投影。
- **身份打点**:接受时把宿主受管身份(principal=`managed:<trust_root>`,domains=`control_plane.data_domains`,默认空=默认拒绝)写入持久化任务输入;执行前校验打点与当前配置一致,不一致转入待对账。
- **关闭处理**:关闭时停止拉取新投递、等待串行槽内运行结束(诚实标记)、尽力完成一次最终上传后再退出。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `standalone-runner-host`:宿主新增受管执行装配(通道 + 接收队列 + 串行调度 + 结果上传 + 关闭处理),`control_plane` 从"显式拒绝"变为"完整受管执行闭环"。
- `managed-runner-enrollment`:受管投递闭环补齐宿主执行语义——接受只表示已接受、投影只在收到宿主事件后进入执行状态、重启按原 Task/Run 恢复、多 Run 事件补传互不跳过。

## Impact

- `packages/hecate-runner`:`profile.py`(`ControlPlaneConfig.data_domains`)、`managed.py`(引用约定常量、身份打点、`ManagedExecutionScheduler`)、`engine.py`(`resume_managed`、standalone resume 跳过受管任务)、`durable.py`(`finish_run` 发 `run_terminal`)、`__main__.py`(装配/启动/关闭)。
- `src/hecate`(平台):无生产代码改动;投递入队的生产路由(平台任务提交面 → `queue_delivery`)不在本 change 范围,`ManagedDeliveryService` 仍是唯一写方。
- 测试:平台侧新增闭环集成测试(正常路径/重试/重启/断线/重复事件/多 Run 游标);runner 包内 profile 校验与 CLI 装配测试。
