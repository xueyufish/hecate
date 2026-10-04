# Design

## Context

两轨已交付的底座(phase-0 #215、A 轨 #216、B 轨 #217):

- `hecate-durable`(依赖仅 SQLAlchemy):`seams.py` 三接缝 ABC、`stub.py`、`storage/`(`SqlDurableStore` 一体实现三接缝;表 `durable_task_state/durable_submission/durable_command/durable_action_intent/durable_action_outcome/durable_event_log/durable_run_sequence/durable_lease`;`LeaseManager` 租约 + fencing;事件日志 per-(run, source) 序号、event_id 去重、gap 标记、状态写入同事务追加 envelope)。**无 worker 模块**——派发循环、对账、重试、中继均不存在。
- `core/composition/durable_platform.py`:`HECATE_DURABLE_BACKEND=stub|postgres`;postgres 模式绑 B 轨 `PostgresDurableTaskStore`/`PostgresControlCommandRecorder`(自有 sync engine,独立短事务)+ **InMemoryActionLedger**(`ledger_source="stub"`);docstring 明示"core 合入后整体替换成员"。
- `execution/task_control.py`:派发为进程内(`wait` 请求内等待 / 后台 `asyncio.create_task`,重启丢失,docstring 明示);治理事件经 `PlatformEventService` 直发 `platform_events`(异步链,与 seam 状态写入不同事务——B 轨 D3 登记的原子性窗口)。
- `execution/platform_durable.py`:B 轨 PG adapter + 三张表(`task_lifecycle_states/task_submissions/control_commands`,迁移 `7d8e9a0b1c2`);`governance_events.py`:`PlatformEventService`(读写 `platform_events`)。
- 契约套件:`tests/test_execution/conftest.py` 的 `DURABLE_IMPLEMENTATIONS` 注册 dict,`SqlDurableStore` 已注册(SQLite/PG 参数化)。
- 方案 step6 清单未勾 9 项中本 change 覆盖:outbox+独立 worker、关键状态与 outbox 同事务、确定性工作流等待输入/回调、双主验证、API/命令跨重启收口。

## Goals / Non-Goals

**Goals:**

- `hecate-durable` 新增 worker 库(认领/对账/重试/drain/中继),平台经 composition 接入后跨重启执行保证成立。
- postgres 后端三接缝统一到 `SqlDurableStore`;B 轨 PG adapter 与三张表退役;治理事件收敛为"seam 同事务 outbox → 中继 → 读模型"单写者链。
- `waiting_input`/`waiting_approval` 持久等待机制(单次消费 token、期限、重启恢复、输入回放)。
- 调度器双主约束验证;确定性工作流启动 Agent 子任务并持久等待的最小场景。
- 独立 worker 进程入口;runner 侧复用同一 worker 库(SC03 持久化半边基础设施)。

**Non-Goals:**

- 受管投递/状态投影/重连去重(`managed-runner-enrollment`,SC05)。
- 审批策略判定、参数绑定校验、职责分离(step7);SC03/SC06 manifest 翻转(step7/step10)。
- 委派/验收/多级子任务编排(step13);Temporal/第二调度后端;中心上传缓冲(step10)。
- 聊天入口事件持久化改造(独立入口切片)。

## Decisions

### D1 composition:postgres 后端三接缝统一到 `SqlDurableStore`;B 轨 adapter 退役

`HECATE_DURABLE_BACKEND=postgres` 时绑定 `(SqlDurableStore, SqlDurableStore, SqlDurableStore)`——单一 sync engine、单一事务域,`ledger_source="core"`。理由:A 轨 D3 设计该存储时即以"三接缝共享事务域使 outbox 同事务成为默认路径"为目标;B 轨 composition docstring 也预告了整体替换。**备选"保留 B 轨 store/recorder、仅换 ledger"被否**:两套平行表(`durable_*` 与 `task_lifecycle_states` 等)长期并存违反"每类状态单一权威写入方",且原子性窗口只在 seam 统一后消除。`platform_durable.py` 的 `PostgresDurableTaskStore`/`PostgresControlCommandRecorder` 及 `PlatformDurableFactory` 删除;`to_sync_database_url`/`create_sync_engine` 若 worker/中继仍需则移至复用位置。stub 后端不动(开发/测试)。

### D2 表与迁移:alembic 纳管 `hecate-durable` metadata,删除三张退役表

新迁移(单支线性追加):`import hecate_durable.storage.models` 的 `Base.metadata`,以 alembic `op.create_table` 逐表生成(可由 autogen 产出后手校),创建 `durable_*` 表与 `durable_outbox_cursor`;随后 `drop_table` `task_lifecycle_states`/`task_submissions`/`control_commands`;`platform_events` 保留。实现期在 `durable_task_state`/`durable_command` 增加平台归属列 `workspace_id`(attach_workspace 单写者写入;平台作用域查询消费),等待 token 不设独立列——经 `apply_task_state(extra_update=...)` 落任务行 `extra` JSON(键 `wait_token`/`wait_expires_at`,revision CAS 保证单次消费)。理由:A 轨 design D3 预定"平台侧由 B 的 alembic 树 import 本包 metadata(字段所有权归本包)";两轨同日合入、alpha 无生产部署,退役表直接删除,迁移说明写明"表内数据不迁移"。downgrade:重建三张空表(结构自 B 轨迁移复制)、不恢复数据,并注明应用回退需回退到 #217 之前。

### D3 worker:库内实现 + 注入 dispatcher;两种运行方式同语义

`hecate_durable/worker.py`:`DurableWorker(store, dispatcher, *, poll_interval, lease_ttl, max_attempts, backoff)`——`dispatcher` 为 `Protocol`,签名"按 `durable_task_state` 行(input_payload、目标引用)发起一次执行并落地终态/等待态"。派发循环:扫描 `queued` → `LeaseManager.acquire(run 键)` 认领(拿 fencing token)→ 调 dispatcher(`asyncio.to_thread` 包同步 seam 调用)→ 按 dispatcher 返回推进状态。周期对账与启动对账共用同一函数:queued 重入认领、`running` 且租约过期者以新 token 重派、活跃租约不动。重试计数持久化在任务行(attempt 列已有承载),超限 `apply_task_state(reconciliation_required)` + 失败 envelope。drain:停止认领、等待在途(带超时)如实报告。**不新增接缝方法**——全部经既有 store/recorder/lease API;契约套件继续以现有断言覆盖,worker 行为由自有故障注入测试覆盖。

进程内 asyncio 任务由平台 lifespan 启动(默认开,`HECATE_DURABLE_WORKER=off` 可关);独立进程入口 `python -m hecate_durable.worker --dsn ... --dispatcher hecate...:工厂路径`——经可配置的工厂路径加载平台或 runner 的 dispatcher 引导(延迟 import,核心包不反向依赖),失败非零退出。两种方式走同一 `DurableWorker`,租约保证多实例/多进程并存安全。

### D4 平台派发回调化:`TaskControlService._dispatch` 语义保持,执行权移交 worker

`_dispatch` 重构为无副作用的执行回调(装载 executor、经 entry service 执行、落地 Run 投影与等待态),由 worker 在认领后调用;`submit` 只落库 + 状态 `queued` + 治理事件,不再 spawn 进程内任务。`_DispatchGuard`/`_spawn_background` 进程内仲裁机制删除(取消仲裁改为命令路径:cancel 在认领前命中 → 状态 `cancelled`;执行中 → dispatcher 内部协作中止,回执语义不变)。`wait` 同步等待视图改为订阅事件日志游标(状态行轮询),不依赖派发发生在同进程。runner 侧:其自有 dispatch 换绑同一 worker 库(durable profile 内),SC01/SC02 行为不变。

### D5 治理事件单写者:seam outbox 权威,`PlatformEventService` 收敛为读模型投影

seam 状态/命令迁移已同事务追加 envelope(A 轨 D6)——`task_control` 中与 seam 状态迁移绑定的 emit 点删除(事件由 store 落 outbox);不绑定状态迁移的事件(如有)保留直发并逐点登记。中继(`worker` 内 `OutboxRelay`):按 `(run, source)` 游标读 `durable_event_log`,经 `PlatformEventService` 幂等投影到 `platform_events`(event_id 唯一约束去重),有界重试 + 退避,游标持久化于新表 `durable_outbox_cursor`(归 worker/中继所有);连续失败不阻塞派发循环(读端 gap 显式)。`platform_events` 保留为读模型的理由:API/SSE 读路径、既有治理事件查询消费它;替换读路径属接口破坏,不在本 change。**备选"API 直读 seam 事件日志、退役 platform_events"被否**:牵动 `read_run_events`/SSE 与治理查询的契约面,且中继模式已满足"关键证据不可采样丢失"的方案要求(权威日志持久,读模型可重建)。

### D6 持久等待:状态行承载,token 单次消费,输入随任务回放

`waiting_input`/`waiting_approval` 进入路径:dispatcher 检测执行需要外部输入/审批(内核 guardrail 暂停、workflow 等待)→ `apply_task_state(waiting_*)` + 等待契约引用与单次 `input_token` 持久化于任务行(复用 A 轨 `input_payload` 列,新增 token/期限列在迁移中补齐到 `durable_task_state`)。唤醒:`provide_input`/`resolve_approval` 命令(命令端点已有 kind)校验 token(单次消费 CAS:UPDATE … WHERE token=:t AND consumed=false)、期限、任务非终态 → 输入落 `input_payload` → 状态回 `queued`(worker 重派时携带输入重放);token 复用/过期/终态 → 命令 `rejected` + 事件。重启后等待任务因状态持久天然存活,不被对账重派(非 queued)。审批策略/参数摘要绑定校验留 step7 接口位(唤醒入口不校验业务策略,只校验 token/期限/终态)。

### D7 双主验证与编排最小场景

验证面:`ops/scheduling/manager.py`(APScheduler job store 写 `scheduled_task_executions`)与 seam 表写入方互斥性——静态断言(manager 不 import seam store、task_control 不写 job 表)+ 注入测试(调度器重试与 worker 重派并发:同幂等键回填、过期 fence 拒绝)。编排最小场景:测试用确定性 workflow executor 经 `TaskControlService.submit` 发起 agent 子任务、自身经 `waiting_input`(关联键=子任务 task_ref)等待、子任务终态事件唤醒后完成——证明"平台 Task/Run 接口启动子任务 + 持久等待"链路;不做委派/验收语义。

## Risks / Trade-offs

- [B 轨表删除影响未预期的外部消费] → 迁移前 grep 确认无代码引用;两轨同日合入、无生产部署;downgrade 重建空表并注明数据不恢复。
- [中继滞后导致读模型暂时缺事件] → 权威日志持久,读端 gap 显式;SSE/API 文档标注读模型来源;对账 API 可对比权威日志与读模型水位。
- [worker 多实例并发派发] → 租约/fencing 为既有已验证机制(契约套件 + 故障注入);新增"双实例认领唯一"定向测试。
- [dispatcher 回调化改变取消时序] → 保留"认领前取消生效、执行中协作中止、终态后 rejected"三分支的定向测试,语义与 #217 验收一致。
- [等待态被旧版调用方误当失败] → API 文档与 `get_task_detail` 显式区分 waiting 与 failed;`wait` 视图超时返回等待中状态而非错误。

## Migration Plan

1. 合入代码(stub 默认不变,行为零变化);新增迁移不自动执行。
2. 部署:alembic upgrade(建 `durable_*`/`durable_outbox_cursor`,删三张退役表)→ 配置 `HECATE_DURABLE_BACKEND=postgres` → 重启(worker 随 lifespan 启动,启动对账接管存量 queued)。
3. 回退:应用回退到上一版本 + `alembic downgrade`(重建三张空表;`durable_*` 表保留,不丢执行事实);文档注明跨版本回退需 drain 后进行。

## Open Questions

(无——关键取舍 D1/D5 已在两轨 design 的预定方向内;dispatcher 工厂路径的配置格式在实现时按现有 settings 风格定,不影响规格。)
