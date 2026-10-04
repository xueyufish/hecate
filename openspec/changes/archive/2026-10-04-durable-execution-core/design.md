# Design

## Context

- phase-0(#215)已交付契约(`hecate.contracts.execution.durable`)、接缝(`hecate.execution.durable`)、InMemory Stub 与参数化契约套件(`DURABLE_IMPLEMENTATIONS` 注册点);本 change 是注册注释中点名的 Worktree A 生产实现。
- Worktree B(`platform-task-control-api`)并行开发平台任务 API/投影/调度接线,只消费契约不修改;文件面互不重叠(注册 dict、R6 白名单、纯度探针为双方已知的微小共享点,均为增量编辑)。
- kernel `ToolWorker` 已有 EventStore 四态恢复(G2 初修 + step1 加固),决策表在 `_recovery_outcome`;`resolve_tool_execution_state` 只覆盖进程内(重启即失)。
- hecate-runner 预览为固定线性图 + 自有 dispatch(不经 ToolWorker),全部状态进程内;SC01/SC02 已 `implemented`,SC03/SC06 `planned`(blocked_by `standalone-durable-actions`)。
- CI:`test` job 无 PostgreSQL(单测约定 SQLite);`migrations` job 有 postgres:16 服务;`runtime-wheel-clean-install` 是独立 wheel 门禁。

## Goals / Non-Goals

- Goals:可独立安装的核心包(SQL 参考存储 + 租约/fencing + 事件游标);G2 跨重启收尾(真实结果引用、冲突拒绝、原子领取、显式关联);runner durable profile(SC03 持久化/重启半边、SC06 本地证据半边);故障注入验收集;SQL 实现继承参数化契约套件。
- Non-Goals:平台 API/投影/alembic(B);受管投递(`managed-runner-enrollment`);审批/授权语义(step7);中心上传缓冲(step10);SC 状态翻转;runner 迁移到共享执行装配(step5 5c 遗留,独立 change)。

## Decisions

### D1 独立包与契约迁移(shim 保 API)

`packages/hecate-durable/`(import `hecate_durable`,依赖仅 `sqlalchemy`)承载:契约集群(`contracts/references|events|tools|durable.py`,自 `src/hecate/contracts/execution/` 逐字节迁入,仅改互引)、接缝(`seams.py`)、Stub(`stub.py`)、存储(`storage/`)。原路径文件改为纯转发 shim(逐符号 `from hecate_durable... import X as X`),与 `src/hecate/runtime/` → `hecate_runtime` 同一既有模式;根 `pyproject.toml` 依赖 `hecate-durable`(workspace)。理由:独立宿主 wheel 闭包不得包含 `hecate` 主应用(CI clean-install 断言钉住);契约定义又不允许并行词汇表(phase-0 模块 docstring)。schema JSON 文件留在 `src/hecate/contracts/schemas/`(权威产物随主仓发布,样本/校验测试按仓库路径消费,不受迁移影响)。Worktree B 消费面 `hecate.contracts.execution.durable` 经 shim 不变。

### D2 同步 SQLAlchemy,异步侧用 to_thread 卸载

接缝方法签名是同步的(phase-0 按 InMemory Stub 定稿,参数化套件直接同步调用),SQL 实现 MUST 同步可调用。存储层用同步 `create_engine`(SQLite 内建驱动;PG 经可选 extra `psycopg`)。runner 的异步路径(kernel hook 适配器、engine dispatch)统一 `asyncio.to_thread` 包裹同步调用,避免阻塞事件循环;每次调用独立 `Session`(方案"每事务独立 session"),从不跨 Run 共享。

### D3 一个 SqlDurableStore 实现三个接缝;自有 Base;create_all 起动

单一 `SqlDurableStore(DurableTaskStore, ControlCommandRecorder, ActionLedger)` 注册为 `(store, store, store)`——三接缝共享同一引擎与事务域,使"关键状态 + outbox 同事务"成为默认路径。自有 `DeclarativeBase`(独立包不 import `hecate.core.database`);宿主本地 profile 启动时 `metadata.create_all`(单宿主、新表集合,无演进负担);平台侧如需纳管,由 B 在其 alembic 树 import 本包 metadata(字段所有权仍归本包)。表:`durable_task_state`(最新状态行,含 input_payload 供重启重放)、`durable_submission`、`durable_command`、`durable_action_intent`(claim token/holder/关联列)、`durable_action_outcome`(最新结果行:result_digest/result_ref/result_payload)、`durable_event_log`、`durable_run_sequence`(per-run source_sequence 分配)、`durable_lease`。

### D4 跨方言原子性:单语句 CAS + 唯一约束,不依赖 advisory lock

领取/租约/状态推进全部用"单条 UPDATE … WHERE 守卫条件,检查 rowcount"或"INSERT 撞唯一约束后回读"实现:PG 行锁生效;SQLite 单写者串行同样正确;不使用 `pg_advisory_lock`(那是 kernel `PostgresEventStore` 的机制,存储层不需要)。并发领取:两条并发 CAS 至多一条 rowcount=1。revision 乐观校验:`UPDATE … WHERE revision=:expected`;`expected_revision` 过期拒绝。幂等键:INSERT 撞 `durable_submission` 主键 → 回读比对 digest → 同体返回原关联/异体 `IdempotencyConflictError`。

### D5 fencing:action claim token + 资源租约;迟到回执只进事件日志

- Action 领取返回单调 `claim_token`(行内自增);`record_outcome_fenced(token)` 校验当前 token,不等即拒绝(`LateOutcomeError`),并将该迟到回执作为事件日志行保留(可观测、可对账),绝不覆盖权威 outcome——这是"旧 owner 在租约过期后继续写被 fencing 拒绝"的 storage 层实现。接缝级 `record_outcome`(无 token)按 phase-0 语义为单写者假设下的直接写入。
- `LeaseManager` 按资源键发租约(holder、expires_at、单调 fencing_token,时钟可注入);`renew` 校验 holder+未过期;过期后他人夺取 token+1,旧 holder 的后续写因 token 落后被拒。runner 用它持有 run 执行权(单宿主下主要是故障注入与多进程误并发的护栏)。

### D6 事件日志:per-(run, source) 序号、event_id 去重、读端 gap 标记、状态写入同事务发射

`durable_run_sequence` 行以 `UPDATE last_seq=last_seq+1` 分配(同事务内随后 SELECT,行锁串行);`event_id` 唯一——重复 append 回读比对,同体幂等返回原序号,异体冲突;乱序 seq 接受(唯一约束保证同位不双占)。读取按 (run, source) 序升序;`[cursor+1, max]` 区间内缺失序号显式构造 gap envelope(EventKind.GAP)。所有状态写入(`apply_task_state`/命令迁移/意图/领取/outcome)在同事务追加对应 envelope(治理事件带 actor/source,写入前 `validate_governance_event`),即事务 outbox;订阅端只读事件表。

### D7 恢复语义与 Stub 逐点一致;store_unavailable 是返回态

`SqlDurableStore` 的 recovery/claimable 判定逐点复刻 `InMemoryActionLedger`(含"outcome 为 succeeded/failed 时 state=claimed + last_outcome 携带终态"的折叠),由参数化契约套件(R1–R6)钉住。读库异常时 `recovery()` 返回 `ActionRecovery(state=STORE_UNAVAILABLE)`(契约语义:失败查询绝不降级为 never_started);写路径异常原样抛出,调用方(钩子/门禁)按 G2 fail-closed。

### D8 kernel 钩子:ABC + 共用决策函数;ledger 为跨重启权威

- 新模块 `hecate_runtime/action_hook.py`:`ActionLedgerHook(abc.ABC)`(resolve / record_claim / record_outcome,出入参为 kernel 类型 + 复用 `ToolExecutionResolution`),具名消费者即 runner 适配器(满足 runtime-pluggability)。
- `_recovery_outcome` 的决策表提取为纯函数 `recovery_decision(resolution, …)`,EventStore 路径与 hook 路径共用——语义不复制,漂移不可能。
- ToolWorker 可选注入 `action_hook`:领取区内 hook.record_claim 原子领取(输者按其 resolution 走决策表);错误/成功路径镜像 record_outcome(携带原始结果,由适配器落盘内容+digest+ref);outcome 落盘失败不撤销已发生副作用——ledger 停留 claimed → 恢复 fail-closed,宿主另行标记 reconciliation(方案"执行后落盘失败保持待对账")。
- 解析合并:hook 存在时其 resolution 参与预过滤与锁区判定,取"更保守"结论(任一来源 claimed/unknown/store_unavailable 即按其决策);succeeded 以带摘要匹配者为准。succeeded backfill 顺序:channel history → hook 提供的 ledger 真实内容 → `[reconciliation required]` 标记。

### D9 runner durable profile:稳定动作键 + 重放式恢复 + 证据门禁

- 动作键稳定跨重启:Pregel session 钉为 run_id 派生(uuid5);动作键 `f"{run_id}:{tool_name}"`(固定图工具序确定),参数摘要用 kernel `tool_arguments_digest`。重启恢复 = 经 ledger 决策表重放图(非 checkpoint 续跑):已成功动作 backfill 真实结果不重执行;claimed 写入安全停止 → Task `reconciliation_required`(经 `apply_task_state`);QUEUED 任务重新派发。
- HTTP:`Idempotency-Key` 头 → `IdempotencyKey(subject=服务端身份, workspace=部署域, digest=canonical(body))`,同键同体返回原 Task/Run,异体 409;cancel 落 `ControlCommandRecord`(requested → 生效后 applied);`GET /tasks`、`GET /tasks/{id}`、`GET /tasks/{id}/actions`(四态 + 真实结果引用)= 最小事件/待对账查询 API;events 读持久日志。
- write 工具准入:仅 durable profile 且 manifest 声明 `write` 权限时允许(独立模式授权/审批权威是业务 API,SC03 断言"不依赖平台审批服务");`approval_required` 仍拒绝(step7)。
- SC06 本地半边:受保护(非 readonly)动作派发前 `EvidenceStore.probe()`(真实追加一条 gate 记录,可审计);失败 → 动作 withholds + 新受保护 run 拒绝(503);readonly 继续;ledger 写失败同样 fail-closed。

### D10 SC 场景与 manifest 纪律

SC03/SC06 保持 `planned`(各自另一半归 step7/10;`test_sc_implemented_tests_binding` 禁止 planned 场景存在 `test_sc<nn>_*` 测试函数)。半边验证测试放 `packages/hecate-durable/tests/` 与 `packages/hecate-runner/tests/`,命名避开 `test_sc\d+` 前缀;基线文档 §5 行以"step6 半边交付于 durable-execution-core,step7/10 半边待续"表述(不含"已支持/已通过")。

### D11 CI 与独立安装门禁

`test` job 追加 `pytest packages/hecate-durable/tests/`(SQLite 默认;`DURABLE_TEST_POSTGRES_URL` 未设时 PG 用例跳过);`migrations` job(postgres 服务)追加一步以该 URL 跑同一套件的 PG 模式;`runtime-wheel-clean-install` job 构建 `hecate-durable` wheel、与 runner/runtime 一起装入 clean venv,并保持"无 full-hecate"断言。类型检查经 `mypy src/ packages/` 自然覆盖。

## Risks / Trade-offs

- [shim 与 Worktree B 冲突] B 只消费不修改契约文件;shim 为纯转发,冲突面极小;注册 dict/R6 白名单为双方预告过的增量编辑。
- [SQLite 与 PG 方言差异] 只用可移植构造(单语句 CAS、唯一约束、普通 SELECT/UPDATE);PG 模式在 CI migrations job 真库回归;不使用任何 PG-only 语法。
- [同步 DB 阻塞异步循环] 适配器统一 `asyncio.to_thread`;单宿主串行负载下可控;平台侧高并发接线归 B(可用其自身 async 适配)。
- [重放式恢复 ≠ checkpoint 续跑] 图级重放经 ledger 门控不重复副作用,模型节点重复调用是 readonly 成本;与方案"等待/结果可恢复或对账"一致,checkpoint 续跑不在本 change 声明。
- [runner 自有 dispatch 不经 ToolWorker] 两路径共用 ABC + 决策函数 + digest 函数,语义单源;runner 迁移共享装配是 step5 遗留项,不在此混入。
- [契约迁移被误读为契约变更] 逐字节迁移 + shim 转发;漂移钉子、三方互检、参数化套件全部不改断言即通过,证明语义未变。

## Migration Plan

- 纯增量:新包、新模块、shim 替换(运行时行为等价)、runner 预览路径不受影响(durable 块缺省时一切如旧)。
- 合并顺序:本 change 与 B 各自 PR;契约 shim 先行合入也不影响 B(其消费面不变)。
- 回退:revert 单个 PR 即可;无数据迁移(新表集合,宿主侧 create_all);无平台行为变化。
