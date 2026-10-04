# Tasks

## 1. 数据模型与迁移

- [x] 1.1 新增 `models/task_lifecycle.py`:`TaskLifecycleStateModel`(issuer_domain+task_id 唯一,lifecycle_state/revision/recorded_at/writer_source/workspace_id)与 `TaskSubmissionModel`(idempotency_key 唯一,subject/workspace/request_digest/task/run 引用);Pydantic 读写 schema 就近定义。验证:模型单测(唯一索引、字段校验)随 3.x 服务测试覆盖。
- [x] 1.2 新增 `models/control_command.py`:`ControlCommandModel`(command_id 唯一,契约 ControlCommandRecord 全字段 + workspace_id);命令读取 schema。验证:随 3.x 服务测试覆盖。
- [x] 1.3 新增 `models/platform_event.py`:`PlatformEventModel`(event_id 唯一,envelope 全字段 + envelope JSON,(workspace_id, run_id, source_sequence) 索引)。验证:随 4.x 事件测试覆盖。
- [x] 1.4 alembic 迁移(head `e9a4b72c6d10` 之后)建四表;downgrade 删表。验证:`alembic upgrade head` 于测试库跑通(或迁移结构校验测试),SQLite/PG 双方言索引可用。

## 2. Seam adapter 与 composition 绑定

- [x] 2.1 `execution/platform_durable.py`:`PostgresDurableTaskStore`(record_submission 幂等仲裁/同键异体冲突;apply_task_state 迁移校验 + revision 乐观检查;get_task_state)与 `PostgresControlCommandRecorder`(record 幂等/transition 终态吸收/get),每调用独立短事务 sync session;sync engine 工厂(aiosqlite→sqlite 映射、惰性构建)。验证:注册进 `DURABLE_IMPLEMENTATIONS` 后参数化契约套件全量通过。
- [x] 2.2 `core/composition/durable_platform.py`:`get_durable_suite()` 进程单例,`HECATE_DURABLE_BACKEND`(stub|postgres,默认 stub);postgres 模式 ledger 保持 InMemory 并在返回值元数据标注来源。验证:composition 单测(两种绑定、惰性、标注)。

## 3. 平台任务控制服务

- [x] 3.1 `execution/task_control.py`:提交路径(幂等键 + create_task/create_run + apply_task_state(queued) + task.submitted 治理事件;同键同体重放返回原关联,异体 409 冲突,作用域不匹配拒绝)。验证:服务级测试覆盖三情形与治理事件断言(actor/source 非空)。
- [x] 3.2 生命周期推进与投影:terminal 收敛(succeeded/failed/cancelled)、reconciliation 标记与显式对账收敛(reconciled=True 路径)、Run projection 更新经 TaskRunRegistry.update_projection。验证:迁移校验拒绝矩阵(终态回退/静默收敛/stale revision)测试。
- [x] 3.3 inline 派发:wait=true 同步等待至终态;wait=false asyncio 后台执行;执行复用共享装配 + EntryExecutionService(existing_task_id 关联),生命周期 running→terminal 随执行推进,run 事件经 RunEventMapper 逐条持久追加(platform_events),非流式执行补 terminal 事件。验证:stub port 端到端服务测试(两模式、事件落库、断开请求后台续跑)。
- [x] 3.4 命令路径:记录(requested)→ cancel 转发 backend.request_cancel → 回执/拒绝如实落账;pause/resume/provide_input 未声明能力记录 rejected;过期命令读取惰性收敛 expired;命令治理事件。验证:命令状态机测试(成功≠applied、拒绝、过期)。

## 4. 治理事件与读取

- [x] 4.1 `execution/governance_events.py`(或就近):envelope 构造(contract_version/event_id/task+run ref/source_sequence/actor/source/correlation)与持久追加 + 按 run 游标分页读取(EventPage 语义)。验证:游标连续性/断线恢复/workspace 隔离测试。
- [x] 4.2 待对账查询服务:reconciliation_required Task + 过期未决命令 + 台账 pending_reconciliation(Stub 时空列表 + ledger_source 标注)。验证:查询只读、惰性 expired 收敛、来源标注测试。

## 5. API 层

- [x] 5.1 `channel/api/tasks.py` 路由 + main.py 挂载:`POST /api/tasks`、`GET /api/tasks`(分页)、`GET /api/tasks/{id}`(生命周期+runs)、`GET /api/tasks/{id}/runs`、`GET /api/runs/{run_id}`(投影)。验证:HTTP 测试(201/幂等重放/409/404 跨 workspace 不可区分)。
- [x] 5.2 事件端点:`GET /api/runs/{run_id}/events`(游标分页)与 `/events/stream`(SSE,游标断线恢复)。验证:分页/SSE 冒烟 + 游标恢复测试。
- [x] 5.3 命令端点:`POST /api/tasks/{id}/commands`、`POST /api/tasks/{id}/cancel`、`GET /api/commands/{command_id}`。验证:回执状态如实(requested≠applied)、过期收敛、404 语义。
- [x] 5.4 待对账端点:`GET /api/reconciliation/pending`。验证:workspace 作用域、Stub 台账来源标注。

## 6. 调度器接线(step5 尾巴)

- [x] 6.1 `ops/scheduling/manager.py` `_execute_task` 接 ExecutorRegistry:按 agent_id/workflow_id 解析 executor、传递行上下文、结果映射 success/failed、error_message/result_summary 落库;空转路径删除;registry 进程级缓存。验证:stub executor 测试(分派调用、失败如实记录、无 executor 时 failed 明确原因)。

## 7. 收口验证

- [x] 7.1 分层与回归:`ruff check`、`ruff format --check`、`mypy src/`、相关 pytest 套件(test_execution/test_channel/test_ops 范围)全绿;分层测试(test_layering_domain/test_layering_entry_imports)通过。
- [x] 7.2 文档同步:计划文档 step6 平台轨条目、tasks 勾选与报告;AGENTS.md 无需新规则确认。
