# Design

## Context

代码检视确认的三处缺口实现面:

- `execution/entry_service.py`:`_correlate`、`_finalize_failure`、`_finalize_success` 均以 `begin_nested()` savepoint 挂在调用方 `AsyncSession` 事务内;同步路径异常时 `_record_engine_events` 不执行(`_engine_events` 只在成功结果 dict 上出现),失败只有 Run 投影、没有事件信封;结果 dict 无产物引用出口。
- `execution/entry_events.py`:`RunEventMapper` 只映射流事件为 `EventKind.EVENT` 信封;无终态信封概念。
- `execution/task_run_registry.py`:`finalize_run` 只写 `state/summary/error_category`,投影无 artifacts 字段。
- PG 参数化基建已存在:`tests/test_execution/conftest.py` 的 `HECATE_STEP6_POSTGRES_URL` 同时切换平台 ORM 与执行存储引擎。

## Goals / Non-Goals

**Goals**

1. 同步/流式失败都留下:可得引擎事件信封 + 一条终态信封 + 一致的失败 Run 投影。
2. Task/Run 登记、终态投影、事件信封在调用方外层回滚后仍可查询;不提交调用方业务事务。
3. 结果声明的产物引用经校验进入 Run 投影与终态信封,公开路径可读、越权引用拒绝。
4. 以上在 PostgreSQL 上经实际入口端到端验证,排除内存兜底。

**Non-Goals**

- G3 真实 TCP 中途断连/恢复证据(独立后续项)。
- 独立 workflow 的 Run/部署身份设计。
- 默认引擎路由开关翻转(维持关闭)。
- 产物内容存储与 artifact registry 平台表(本 change 只做引用级映射与读取,引用表留给后续步骤)。
- 内存兜底路径的删除:未接事件存储的执行仍保留内存信封兜底。

## Decisions

### D1: 事务所有权 —— 登记与终态投影走独立注册会话

`EntryExecutionService` 新增可选 `registry_session_factory`(AsyncSession 工厂)。提供时:

- 登记(`create_task`/`create_run`/`conversation link`)在**独立会话**上执行并即时提交——发生在引擎执行之前,与调用方请求事务无关;
- 终态投影(`finalize_run`)同样走独立会话,执行结束后提交;
- `self._db`(调用方会话)只用于只读解析(默认部署、principal 查询),不产生依赖其提交的写入。

调用方事务因此可以安全回滚:引擎异常 → 入口层按既有语义返回失败 → 上层 handler 回滚业务事务 → 登记与失败事实已在独立会话提交,不受影响。修复不调用调用方会话的 `commit()`,不改变其事务边界,满足"不得提交调用方业务事务"约束。

兼容与装配:平台 composition 注册应用 sessionmaker；没有 ASGI lifespan 的入口也从相同 composition 懒解析工厂。仅显式注册 `None` 时保留历史 savepoint 行为，不认证其回滚持久性。事件存储自有连接，不提交调用方事务。

### D2: 失败终态信封 —— EventKind 扩展 TERMINAL

`hecate-durable` 的 `EventKind` 增加成员 `TERMINAL = "terminal"`，同时同步公开 JSON Schema 与标准样例。当前使用未冻结的契约草案；硬编码旧枚举的消费者需更新，不能笼统承诺其不受影响。终态信封:

- payload 携带 `state`(succeeded/failed/cancelled)、`stop_category`(cancelled/iteration_limit/budget_exhausted/execution_failed…)、`error_summary`(安全摘要,不含堆栈与内部字段)、成功时 `artifacts`(见 D4);
- `payload_schema_ref` 使用独立标识 `hecate.entry.terminal/0`,与流事件区分;
- 由 `RunEventMapper` 以 run 内单调序列追加到事件存储,与流事件同 key 同游标可分页。

写入时机:每次执行正常或异常收尾写一条，mapper 防止重复追加终态。显式停止、`asyncio.CancelledError` 和生成器关闭都进入失败/取消收尾，取消作用域内屏蔽终态落库操作的协作取消。顺序为“先证据后投影”；投影失败保留信封并告警。事件写入失败时不把未落库的事件放入内存成功视图或消耗序号，游标按实际序号筛选。

Run 注册时保存平台写入的 `entry_name` 来源标记，终态保留该标记。公开 Run 事件分页和订阅经 workspace 校验后读取相同入口事件存储，返回 client-safe payload，隐藏原始 backend detail 和拒绝候选。其他 Run 保持原 PlatformEventService 路径，不复制或迁移事件到第二套真源。

### D3: 同步失败的可得事件 —— 异常携带已排空引擎事件

`WorkflowExecutionService` 的同步排空路径在异常时把**已排空的引擎事件列表**以 `engine_events` 属性挂到异常对象上(有界,与现有 `_engine_events` 同源)。`_finalize_failure` 读取该属性并尽力映射为信封,再写终态信封;属性缺失或为空时仅写终态信封(与规格"已排空可得"一致)。`platform_correlation` 注释沿用现有机制,不扩展。

### D4: 产物引用映射 —— 声明、校验、投影、读取

- **声明来源**:真实适配器保留图输出的 `declared_artifacts`，也采集本次新生成工具回执 JSON 中显式声明的同名字段，同步与流式共用抽取规则。请求提供的历史工具消息不参与采集，workflow `input_data` 禁止写该输出通道；`input_artifact_refs` 不经过本映射。
- **校验**:kind=ARTIFACT 且 issuer 匹配仅是形状检查，不能证明 workspace 归属。平台引用当前解析到现有 `EvidenceModel`：id 必须匹配当前已验证 workspace 的未删除记录。未知、跨 workspace、已删除或暂不可验证引用均拒绝，记录 backend 告警证据；不为此新增完整产物 registry 或内容存储。其他存储命名空间未经解析器验证前不授予支持。
- **投影与信封**:通过的引用进入 Run 投影 `artifacts` 字段与终态信封 payload;`finalize_run` 增加可选 `artifacts` 参数,仅接受已校验引用。
- **读取**:公开 Run 投影、事件分页和事件订阅均可读取通过校验的引用，跨 workspace 查询拒绝；后台拒绝候选不随公开事件响应返回。不新增产物内容读取 API。

### D5: PostgreSQL 端到端回归 —— 排除内存兜底

- 复用 `HECATE_STEP6_POSTGRES_URL` 参数化模式:配置时平台 ORM 与执行存储(事件存储)均建在 PostgreSQL 上;
- 经 ASGI 真实 HTTP 客户端走实际入口(成功、拒绝、异常、停止四类);ASGI 满足本项,真实 TCP 断连归 G3;
- 执行完成后:**新数据库会话/sessionmaker** 查询 Task/Run/投影/事件,断言调用方事务回滚后事实仍在;
- 内存兜底排除:断言基于新建 `RunEventMapper`(空内存态)从存储读取的信封分页,以及在无事件存储装配下不可达的路径不参与断言;若事件存储支持,辅以直接存储查询比对序列。
- 额外经公开 `/api/runs/{id}`、事件分页及订阅验证同步/流式产物和权限拒绝；策略拒绝必须证明真实业务工具零调用，成功必须证明实际派发。未知 Agent 的入口 404 仅作为额外负例，不能代替执行内策略拒绝证据。
- 请求事务先写入未提交业务记录，再执行成功或异常请求并回滚；新会话同时证明执行事实保留、业务记录未提交。仅在执行后插入再回滚不足以覆盖这个边界。
- 复用现有 PostgreSQL CI job 显式执行入口验收文件；测试夹具还原 app.state、依赖覆盖和 composition 注册，避免退出后遗留已销毁 schema 的连接工厂。

未配置 URL 时该组验收显式 skip 并保留 SQLite 结果,沿用既有诚实跳过模式。

## Risks / Trade-offs

- **独立会话登记带来两次额外提交**:登记提前于执行提交,失败执行也留下 Task/Run——这正是规格要求的行为;并发下幂等由注册表既有约束承担,不新增分布式事务。
- **EventKind 扩展是跨包契约变更**:`hecate-durable` 契约层 additive 成员,不触发版本升级;runner 侧不消费 TERMINAL(信封键为 platform run 域)。
- **产物拒绝不翻转终态**:执行成功但产物越权时,结果仍为 succeeded + 信封 rejected 证据,由验收明确断言,避免"产物失败伪装执行失败"或反之。

## Migration Plan

无 schema 迁移:投影 `artifacts` 为 JSON 字段内的 additive 键;EventKind 为枚举 additive 成员。回退即还原代码,已写入的终态信封与 artifacts 投影对旧读取方是可忽略字段,不需要清理。

## Open Questions

无——四项范围、事务约束与 PG 验收边界已在立项时与用户确认。
