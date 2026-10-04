# 持久执行核心包(`hecate-durable`,可独立安装):以 SQL 参考存储实现 phase-0 定义的三个接缝(`DurableTaskStore`/`ControlCommandRecorder`/`ActionLedger`),补齐租约/fencing、事件游标、关键状态与 outbox 同事务提交,并以故障注入验收集(崩溃、租约过期、迟到回执、存储失败)证明 G2 语义跨重启成立。本能力是 `durable-execution-contract` 的生产实现侧;契约语义本身不变。

## Requirements

### Requirement: 独立核心包承载契约集群并保持纯转发兼容

持久执行核心包 SHALL 作为独立 workspace 包交付(依赖仅 SQLAlchemy;PostgreSQL 驱动为可选 extra),承载持久执行所需的契约集群(`references`/`events`/`tools`/`durable` 四模块)、三个接缝、InMemory Stub 与 SQL 参考存储。契约与接缝的真实代码迁入本包后,`hecate.contracts.execution.*` 与 `hecate.execution.durable|stub_durable` 原路径 SHALL 保持纯转发 shim,Python API 面不变;第三方或平台代码经原路径消费 MUST 继续工作。核心包的契约/接缝/Stub 模块 SHALL 纳入契约层纯度探针(禁止 ORM/业务域/供应商 SDK,含懒加载);接缝实现注册进 `DURABLE_IMPLEMENTATIONS` 参数化契约套件并通过全部断言。

#### Scenario: 独立安装不含主应用

- **WHEN** 将核心包与内核/宿主 wheel 安装进干净 venv
- **THEN** 依赖闭包不包含 `hecate` 主应用包,shim 消费方在主应用内继续正常导入

#### Scenario: SQL 实现继承契约套件

- **WHEN** 参数化契约套件运行
- **THEN** SQL 实现(测试方言)与 InMemory Stub 通过同一组 R1–R6 断言

### Requirement: SQL 参考存储实现三个接缝且关键状态与 outbox 同事务

`SqlDurableStore` SHALL 以同步 SQLAlchemy 实现全部三个接缝,语义与 InMemory Stub 逐点一致(由参数化套件钉住);PostgreSQL 为参考生产方言,SQLite(文件)为开发/测试方言,二者 SHALL 使用同一套可移植构造(单语句条件 UPDATE 的 CAS、唯一约束回读),MUST NOT 依赖单方言锁原语。每次方法调用 SHALL 使用独立数据库 session,MUST NOT 跨并发 Run 共享。Task 状态迁移、命令回执迁移、Action 意图/领取/outcome 写入 SHALL 在同一数据库事务内追加对应治理事件行(事务 outbox);事件行 MUST 携带 actor/source 并在写入前通过治理事件 profile 校验。宿主本地 profile SHALL 以包内 metadata 建表,不依赖平台管理表或平台 alembic。

#### Scenario: 状态与事件同事务

- **WHEN** 一次 Task 状态迁移提交
- **THEN** 对应治理事件行在同一事务可见(状态回滚时事件一同回滚),读取端经事件日志按游标观察到该迁移

#### Scenario: 幂等提交同键异体冲突

- **WHEN** 相同幂等键携带不同请求摘要再次提交
- **THEN** 返回携带已注册摘要的冲突错误,原关联不被覆盖;同键同体返回原 Task/Run 关联

#### Scenario: 修订 fencing 拒绝过期页面

- **WHEN** Task 状态写入携带过期的 `expected_revision`
- **THEN** 写入被拒绝并返回当前修订,不静默覆盖

### Requirement: 租约与 fencing token 拒绝过期写入

存储 SHALL 提供资源键级租约:获取/续租/释放携带 holder 与有效期,时钟可注入;每次成功获取 SHALL 产生单调递增的 fencing token,过期后被他人夺取时 token 继续递增。Action 领取 SHALL 返回行内单调 claim token;outcome 写入 SHALL 校验当前 token——旧 token 的迟到回执 MUST 被拒绝且 MUST NOT 覆盖权威结果,同时 SHALL 作为事件日志行保留(可观测、可对账)。任务状态修改 MUST 校验当前 ownership 修订。

#### Scenario: 租约过期后旧持有者被拒

- **WHEN** 持有者 A 的租约过期且持有者 B 已夺取(token 递增),A 随后提交写入
- **THEN** A 的写入被 fencing 拒绝,B 的权威状态不受影响

#### Scenario: 迟到回执不覆盖权威结果

- **WHEN** 一个旧 claim token 的 outcome 写入在新领取已经发生后到达
- **THEN** 写入被拒绝,权威 outcome 不变,迟到事实以事件行留痕

### Requirement: 事件日志提供去重、乱序容忍与显式缺口

事件日志 SHALL 按 (run, source) 维度以 `source_sequence` 单调分配序号(同事务分配);`event_id` 全局唯一——重复写入同 event_id 同体 SHALL 幂等返回原序号,异体 SHALL 冲突;乱序到达的序号 SHALL 被接受(同位序号被不同事件占用时冲突)。读取 SHALL 以游标恢复:返回 cursor 之后按序号升序的事件,区间内缺失的序号 SHALL 以显式 gap envelope 标记;MUST NOT 以客户端时钟重建顺序,尾部/空页 SHALL 继续返回游标。

#### Scenario: 断线后游标续读与缺口标记

- **WHEN** 事件 1、2、4 已落盘,读端以 cursor=0 请求
- **THEN** 返回事件 1、2、4 与 [3,3] 的 gap 标记,及指向 4 的 next_cursor

#### Scenario: 重复投递幂等

- **WHEN** 同一 `event_id` 的同一事件被重复写入
- **THEN** 第二次写入幂等返回,序号不重复分配,不产生重复事件行

### Requirement: G2 跨重启恢复返回真实结果且动作与 Runtime 事件显式关联

Action 台账 SHALL 在意图行持久化 session/execution/tool_call 关联列,使平台 Action 与 Runtime `TOOL_CALL`/`TOOL_RESULT` 事件经 `execution_id`/`tool_call_id` 显式关联,MUST NOT 要求外部后端伪造 Pregel 事件。outcome 行 SHALL 落盘真实结果内容、摘要与引用;恢复查询对已成功动作 SHALL 返回该真实结果(内容+引用+摘要),对未知结果 SHALL 返回显式待对账标记,MUST NOT 以占位文本冒充。同动作键的参数摘要变化 SHALL 冲突拒绝;领取 SHALL 以数据库层 CAS 原子完成,并发至多一个领取者。读库失败时恢复查询 SHALL 返回 `store_unavailable` 判定,MUST NOT 降级为"从未执行"。

#### Scenario: 崩溃重启后已成功动作返回真实结果

- **WHEN** 写动作 outcome 已落盘后进程崩溃,新 store 实例按同动作键恢复
- **THEN** 恢复返回落盘的真实结果内容/引用/摘要,该动作不被重新执行

#### Scenario: 崩溃重启后 claimed 写动作安全停止

- **WHEN** 写动作意图与领取已落盘、outcome 未落盘时进程崩溃,重启后恢复
- **THEN** 该动作判定为待对账/安全停止,不自动重跑,业务目标不被第二次调用

### Requirement: 故障注入验收集覆盖四类故障

核心包 SHALL 附带故障注入测试集,以同一 SQL 实现运行:进程崩溃(状态落盘后放弃进程内状态,以新实例恢复)、租约过期(注入时钟推进,过期持有者被 fencing 拒绝)、迟到回执(旧 token 写入被拒且留痕)、存储失败(引擎注入异常 → 恢复查询返回 `store_unavailable`、写路径 fail-closed)。SQLite 与 PostgreSQL SHALL 经同一测试参数化;PostgreSQL 模式在具备真实实例的环境运行,不可用时显式跳过而非静默通过。

#### Scenario: 存储失败不降级

- **WHEN** 存储引擎在恢复查询时抛出异常
- **THEN** 恢复判定为 `store_unavailable`,写类动作安全停止,不被解释为从未执行