# Tasks

## 1. 失败终态信封

- [x] 1.1 扩展 `hecate-durable` `EventKind` 增加 `TERMINAL`,在 `RunEventMapper` 增加终态信封写入(payload: state/stop_category/error_summary/artifacts,schema ref `hecate.entry.terminal/0`),复用 run 单调序列与事件存储 key;单元测试覆盖信封形状与分页可读。
- [x] 1.2 `WorkflowExecutionService` 同步排空路径异常时把已排空引擎事件以 `engine_events` 属性挂到异常;`_finalize_failure` 尽力映射该属性并恰好写一条终态信封,再写投影;测试覆盖:异常带事件、异常无事件、停止类别三分类(cancelled/iteration_limit/budget_exhausted)。
- [x] 1.3 流式路径在异常与正常收尾各恰好写一条终态信封;失败不产生 succeeded 终态或成功摘要;HTTP mapper 渲染不泄漏终态信封中的内部字段。

## 2. 登记与失败事实的事务所有权

- [x] 2.1 `EntryExecutionService` 增加 `registry_session_factory` 装配:登记(含 conversation link)与 `finalize_run` 走独立会话即时提交,`self._db` 仅作只读解析;平台 composition(HTTP/MCP/IM 入口装配)统一注入;工厂缺省时维持现行 savepoint 路径并记录为历史行为。
- [x] 2.2 测试:执行(含引擎异常)后调用方外层事务回滚,新数据库会话仍可查询 Task/Run、失败终态投影与事件信封;同时断言调用方事务中未提交的业务写入未落库(修复未提交调用方事务)。
- [x] 2.3 登记 fail-open 语义回归:注册表拒绝或存储不可用时结果携带显式 missing 标记与原因,不伪报关联。

## 3. 声明产物引用映射

- [x] 3.1 定义结果标准键 `declared_artifacts` 的读取与校验(kind=ARTIFACT、issuer_domain 一致且通过 workspace 权威归属与删除状态验证),拒绝项在信封 `extra` 留 rejected 证据且不翻转执行终态;`finalize_run` 增加可选 `artifacts` 参数写入投影。
- [x] 3.2 测试:合法声明进入 Run 投影与终态信封且按 Run 查询可读;伪造/跨域/错误 kind 引用被拒绝且公开契约不出现越权引用;请求参数注入 `declared_artifacts` 不生效。

## 4. PostgreSQL 实际入口端到端回归

- [x] 4.1 参数化入口契约端到端验收:配置 `HECATE_STEP6_POSTGRES_URL` 时平台 ORM 与事件存储均建在 PostgreSQL,经 ASGI 真实 HTTP 入口执行成功、拒绝、异常、停止四类请求;执行后以新数据库会话查询信封与投影,并用新建空内存态 `RunEventMapper` 读取存储分页,证明事实来自持久存储而非内存兜底。
- [x] 4.2 未配置 URL 时该组验收显式 skip 并保留 SQLite 回归;在验收记录中明确"PostgreSQL 入口信封端到端"为独立交付项(写入 verification/support 记录,不藏在测试说明里)。

## 5. 收尾

- [x] 5.1 全量受影响回归(entry、channel HTTP/SSE、A2A、MCP、IM、workflow 相关)+ ruff check/format + mypy;检查实际测试收集与 skip 范围。
- [x] 5.2 更新演进方案 step5d 第一条剩余项:四子项(同步失败信封、登记保留、产物映射、持久后端入口认证)验收通过后勾选该项并注明关闭范围;step5d 其余剩余项保持未完成。
- [x] 5.3 完成复核/verification 记录(含 PostgreSQL 端到端证据与诚实 skip 说明),`openspec validate --strict` 通过。

## 6. 代码复核修正

- [x] 6.1 同步公开 JSON Schema，覆盖 asyncio 取消和生成器关闭的终态，并验证终态唯一性及失败写入不制造分页序号缺口。
- [x] 6.2 产物由真实适配器传递，以现有 EvidenceModel 的 workspace/删除状态验证引用；同 issuer 跨 workspace、未知或删除引用拒绝，排除请求历史与 input_data 注入。
- [x] 6.3 补无 ASGI lifespan 的注册工厂装配，公开 Run 事件分页/订阅从实际入口存储读取并隐藏内部字段；PG 验证真实工具派发、同步/流式产物与拒绝，以及已有业务写入后的外层回滚。
- [x] 6.4 PG 入口验收接入 CI，消除夹具全局污染；完成受影响回归、静态检查和 OpenSpec 校验，更新复核报告、设计及方案声明。
