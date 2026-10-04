# Proposal

## Why

step6(durable-task-control-plane)已确认拆为两个并行 worktree 推进:`durable-execution-core`(执行可靠性 + G2 + 独立宿主)与 `platform-task-control-api`(平台任务 API + 投影 + 调度接线)。两轨都要消费的共享面——Task 生命周期状态机、控制命令回执状态、幂等提交键、治理事件 envelope、持久 Action 台账接缝——若各自演化,必然产生 schema/枚举/接口的并行编辑冲突与语义漂移。同时 G2 的四态恢复语义目前只存在于 `tool-recovery` spec(Runtime EventStore 层),平台 Action 记录与独立宿主需要同一语义的跨进程契约形态。本 change 以纯契约先行定稿共享面:只交付 schema、映射、接缝、Stub 与契约测试,不交付任何持久化实现或平台行为,使两轨从同一基线出发、只消费不修改。

## What Changes

- 新增 **Task 生命周期状态契约**:`queued / running / waiting_input / waiting_approval / succeeded / failed / cancelled / reconciliation_required` 封闭枚举;与既有 `RunState`(后端观察态)为不同维度,不得互相覆盖。`task-run` spec 预留的 step6 状态机在此落地为契约层。
- 新增**控制命令记录契约**:命令独立记录(`command_id`、命令种类、签发者、目标 task/run 引用),状态 `requested / acknowledged / applied / rejected / expired`——补齐既有 `CancelRequestState` 缺失的 `expired`,并把回执语义从"取消"泛化到任意控制命令;HTTP/协议成功响应不等于命令已应用。
- 新增**幂等提交键契约**:键绑定服务端验证的调用主体、workspace 与请求摘要;同键同体返回同一 Task/Run 关联,同键异体返回冲突错误;键作用域来自服务端身份,不接受客户端自报。
- **治理事件 envelope profile**:复用既有 `EventEnvelope` 结构,新增可选 `actor` / `source` 字段(向后兼容,未知字段容忍不变);治理事件 profile 要求二者必填;缺口以 gap 标记显式保留,读取以游标恢复,不依赖采样。
- 新增**持久 Action 台账契约**:四态 `never_started / claimed / outcome_unknown / store_unavailable`、意图(工具名 + 参数摘要 + 副作用类别)先于分发持久化、领取原子(并发至多一个领取者)、恢复返回真实结果引用或显式待对账标记(不得返回占位文本)、同动作键参数摘要变化冲突拒绝。语义与 `tool-recovery` 对齐并钉住一致。
- 新增**持久执行接缝**:`DurableTaskStore` / `ActionLedger` / `ControlCommandRecorder` 最小接口 + InMemory Stub 第二实现 + 参数化契约测试,作为两轨未来真实现(PostgreSQL 存储、平台 adapter、宿主 adapter)的共同验收集。
- **非目标**:不交付 PostgreSQL 作业记录/outbox/worker(归 `durable-execution-core`)、平台任务 API/投影/调度接线/alembic(归 `platform-task-control-api`)、宿主集成、受管投递与投影(归 `managed-runner-enrollment`)、审批与授权语义(step7)。

## Capabilities

### New Capabilities

- `durable-execution-contract`: 持久任务、控制命令、幂等键、治理事件 profile 与 Action 台账的语言中立契约及最小接缝——Task 生命周期状态机、命令回执五态、幂等键绑定规则、envelope 的 actor/source profile、Action 四态台账与游标恢复语义,以及 InMemory Stub 与参数化契约测试构成的共同验收集。

### Modified Capabilities

(无——`execution-backend-contract` 的 envelope 仅增加可选字段,既有要求不变;`tool-recovery` 语义不变,本 change 仅将其四态语义镜像为跨进程契约并以漂移测试钉住;`task-run` 是引用方。)

## Impact

- `src/hecate/contracts/schemas/`:新增 durable 相关 schema;`event-envelope.schema.json` 增量可选字段。
- `src/hecate/contracts/execution/`:新增 durable 映射模块;`events.py` 增加 actor/source。
- `src/hecate/execution/`:新增接缝接口与 InMemory Stub(与 `backend.py` / `stub.py` 同层);`backend.py` 的 `CancelRequestState` 增补 `expired`。
- `tests/test_execution/`:参数化契约测试、标准样本、纯度与漂移钉子。
- 无数据库迁移、无平台运行行为变化、无新增运行时依赖;CI 复用既有契约测试入口。
- 下游:`durable-execution-core` 与 `platform-task-control-api` 两个 worktree 以本 change 为共同基线,合并冲突面归零。
