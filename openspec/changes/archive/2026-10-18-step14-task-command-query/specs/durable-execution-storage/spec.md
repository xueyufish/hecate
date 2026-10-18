# Spec Delta

## ADDED Requirements

### Requirement: 命令回执按 Task 的存储层列表查询

`ControlCommandRecorder` 接缝 SHALL 新增按 Task 的列表查询方法（进入 ABC 声明），并由 SQL 参考存储与 InMemory Stub 同时实现，语义逐点一致（由参数化套件钉住）。查询 SHALL 同时以 workspace 与完整 Task 引用（issuer_domain 与 id 两列）限定，SHALL NOT 仅按 task ID 匹配——同 ID 不同 issuer 的 Task 命令 SHALL NOT 出现。SQL 实现 SHALL 在数据库内完成过滤、分页与计数（WHERE + ORDER BY `issued_at DESC, command_id DESC` + LIMIT/OFFSET + COUNT），SHALL NOT 读取全量后内存切片；InMemory 实现的 total SHALL 为过滤后总数、切片在计数之后，与 SQL 语义一致。查询 SHALL 拒绝非 Task kind 引用、limit 不在 1–100 或 offset 为负的参数，两种实现错误语义一致。该查询 SHALL 只读：SHALL NOT 迁移任何命令状态、SHALL NOT 写入治理事件或任何行。查询 SHALL 返回持久化回执原文（五种状态原样），空结果为合法返回而非错误。既有 `list_pending_commands` 与单条 `get` 的行为 SHALL 不变。

#### Scenario: SQL 在库内完成过滤分页计数

- **WHEN** 以 SQL 存储查询某 Task 的命令列表并观察该过程执行的语句
- **THEN** 仅出现对命令表的 SELECT（及合法事务 BEGIN），过滤、排序、LIMIT/OFFSET 与计数均由数据库语句承担，无全量读取后切片

#### Scenario: 完整 Task 引用限定

- **WHEN** 存储中存在 task_id 相同但 issuer_domain 不同的两条命令，查询其中一条所属的完整 Task 引用
- **THEN** 仅返回该 issuer 下的命令，另一条不出现，total 亦不含它

#### Scenario: 双实现语义一致

- **WHEN** 同一组命令数据分别经 SQL 存储与 InMemory Stub 以相同参数查询
- **THEN** items 的 command_id 顺序、state 值与 total 完全一致

#### Scenario: 列表只读无副作用

- **WHEN** 列表查询执行前后比对命令行与治理事件
- **THEN** 无任何状态迁移、无新增事件行，已过期 requested 不被改写

#### Scenario: 空结果与既有方法不变

- **WHEN** 查询一个没有命令的 Task 引用，或继续调用既有 `list_pending_commands`/`get`
- **THEN** 列表返回空 items 与 total=0（非错误）；既有方法行为与变更前一致

#### Scenario: 存储查询拒绝非法输入

- **WHEN** 调用方传入非 Task 引用，或无效 limit/offset
- **THEN** SQL 与 InMemory 均抛出 ValueError，不读取命令行
