# Proposal

## Why

step6 的本地可靠执行半边(Worktree A `durable-execution-core`)尚不存在:phase-0(`durable-execution-contracts`,#215)只交付了契约与 InMemory Stub,没有任何持久化实现;G2 的"恢复返回真实结果引用"仍依赖进程内 channel history,跨重启即丢失;kernel ToolWorker 的 TOOL_CALL/TOOL_RESULT 与平台 Action 记录没有显式关联;hecate-runner 技术预览的全部状态(任务、事件、回执)都在进程内,重启即丢,SC03(持久化/重启半边)与 SC06(本地证据半边)均为未支持。

同时,phase-0 把契约与接缝放在 `src/hecate/contracts/` 与 `src/hecate/execution/`(主应用包内),而本 change 的交付物必须是**可独立安装的核心包**——独立宿主只安装 `hecate-runner` + 内核 wheel,不能因此拉入整个 `hecate` 应用(方案 §三:"独立发行包不能靠依赖整个 hecate 包……实现'独立'")。契约与接缝的真实代码必须迁入独立包,原路径保留纯转发 shim(与 `src/hecate/runtime/` → `hecate_runtime` 同一模式),Python API 面不变,Worktree B(`platform-task-control-api`)继续经 `hecate.contracts.execution.durable` 消费,不受影响。

## What Changes

- **新增独立核心包 `packages/hecate-durable/`**(import `hecate_durable`,仅依赖 `sqlalchemy`):
  - 契约集群迁移:`references.py`、`events.py`、`tools.py`、`durable.py` 四个纯契约模块迁至 `hecate_durable.contracts`(逐字节迁移,仅改包内互引);`src/hecate/contracts/execution/` 对应文件变为转发 shim。接缝 `DurableTaskStore`/`ControlCommandRecorder`/`ActionLedger` 与 InMemory Stub 迁至 `hecate_durable.seams`/`hecate_durable.stub`,原路径同样转发。
  - **SQL 参考存储** `hecate_durable.storage`:`SqlDurableStore` 一个类实现全部三个接缝(同步 SQLAlchemy、每次调用独立 session、自有 declarative Base,不依赖平台 ORM/alembic);PostgreSQL 为参考生产方言,SQLite(文件)为开发/CI 方言;关键状态写入与治理事件 outbox 行在同一数据库事务提交。
  - **租约与 fencing**:`LeaseManager`(资源键级租约、单调 fencing token、可注入时钟);Action 领取自带 claim token,迟到回执(旧 token 的 outcome 写入)被拒绝且只进入事件日志,不覆盖权威状态。
  - **事件游标语义**:`SqlEventLog` 按 (run, source) 的 `source_sequence` 单调分配;`event_id` 去重幂等;乱序容忍;读取端显式 gap 标记;治理事件写入前校验 actor/source profile。
  - **G2 完整收尾**:恢复查询返回真实结果内容+引用(ledger 落盘 result payload/digest/ref,不再依赖 channel history 存活);同键异参冲突拒绝;DB 层 CAS 原子领取;Action 行携带 session_id/execution_id/tool_call_id 关联列,与 Runtime TOOL_CALL/TOOL_RESULT 显式关联,不要求外部后端伪造 Pregel 事件。
- **kernel 侧 action 钩子微调**(`hecate-runtime`):新增 `ActionLedgerHook` 扩展点(plain noun + abc.ABC,runner 为具名消费者);ToolWorker 增加可选注入,领取/回执镜像写入 ledger,恢复判定合并 ledger 解析(ledger 为跨重启权威,决策表与 EventStore 路径共用同一函数,不复制语义);succeeded 恢复的 backfill 顺序为 channel history → ledger 真实内容 → 显式待对账标记。
- **hecate-runner 消费核心包**:runner.json 新增 `durable` 块(SQLite/PostgreSQL URL);durable profile 下 write 类工具经 manifest 声明准入(业务 API 是独立模式下的授权/审批权威,SC03 "不依赖平台审批服务");`Idempotency-Key` 提交幂等(同键同体返回同一 Task/Run,异体 409);cancel 落 ControlCommandRecord(requested → applied 协作生效);重启后对非终态 Task 扫描对账——已成功动作经 ledger 决策 backfill 真实结果、claimed 写入安全停止并标记 `reconciliation_required`;事件读取改走持久 event log(游标跨重启);SC06 本地半边:证据存储不可写时停止新受保护动作(readonly 继续)。
- **故障注入测试全家桶**(packages/hecate-durable/tests/):崩溃(意图/领取落盘后放弃进程状态,新 store 实例恢复)、租约过期(时钟注入,旧持有者 fencing 拒绝)、迟到回执(旧 claim token outcome 写入被拒)、存储失败(引擎注入异常 → store_unavailable 语义,写动作 fail-closed)。SQL 实现注册进 `tests/test_execution/conftest.py::DURABLE_IMPLEMENTATIONS`,继承参数化契约套件全量断言。
- **非目标**:平台任务 API/投影/调度接线/alembic 迁移(归 Worktree B `platform-task-control-api`);受管投递与状态投影(归 `managed-runner-enrollment`);审批绑定与本地授权策略(step7);中心上传缓冲(step10);SC03/SC06 的另一半(审批语义/中心上传)——两场景保持 `planned`,本 change 只交付各自的责任半边,不翻转 manifest 状态。

## Capabilities

### New Capabilities

- `durable-execution-storage`: 持久执行核心包——SQL 参考存储(PostgreSQL 参考、SQLite 开发方言)、三个接缝的 SQL 实现、租约/fencing、事件日志游标/去重/乱序/缺口、关键状态与 outbox 同事务、G2 跨重启恢复(真实结果引用、同键异参冲突、原子领取、Action↔TOOL_CALL/TOOL_RESULT 关联)与故障注入验收集。

### Modified Capabilities

- `standalone-runner-host`: 增加 durable profile——持久任务/Run/Action 记录、幂等提交、控制命令回执、重启对账与真实结果恢复、事件游标持久化、write 工具经 ledger 准入、本地证据不可写停止新受保护动作;预览(无 durable 配置)行为不变。
- `tool-recovery`: kernel 增加 ActionLedgerHook 可选钩子——领取/回执镜像持久 ledger,恢复判定合并 ledger 权威解析,succeeded backfill 增加 ledger 真实内容来源;EventStore 路径语义不变。

## Impact

- 新增 `packages/hecate-durable/`(包 + 测试);`packages/hecate-runner/`(durable profile + 测试);`packages/hecate-runtime/`(`action_hook.py` 新模块 + `tool_worker.py` 微调 + 新测试)。
- `src/hecate/contracts/execution/{references,events,tools,durable}.py`、`src/hecate/execution/{durable,stub_durable}.py` 改为转发 shim(Python API 不变);根 `pyproject.toml` 增加 workspace 依赖。
- `tests/test_execution/conftest.py` 注册 SQL 实现;`test_durable_contract.py` R6 签名白名单纳入 `hecate_durable.contracts`;`test_contract_purity.py` 扫描范围扩至新包的契约/接缝/Stub 模块;`tests/test_runtime/` 新增 kernel 钩子测试(新文件)。
- CI:`test` job 增加 `packages/hecate-durable/tests/`(SQLite);`migrations` job(postgres 服务)追加 SQL 实现 PG 模式套件;`runtime-wheel-clean-install` job 增加 hecate-durable wheel 构建与独立安装断言。
- 文档:`standalone-consumption-baseline.md` §5/§6 半边交付登记;`enterprise-agent-platform-evolution-plan.md` step6 对应项勾选。
- 无平台数据库迁移(核心包自建 schema,平台侧投影表归 Worktree B);平台入口运行行为不变(hook 默认 None)。
