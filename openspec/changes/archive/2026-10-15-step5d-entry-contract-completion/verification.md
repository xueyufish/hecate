# Verification

本文件原始结果为 GLM 初次实现记录。代码复核发现产物归属、公开查询、取消与测试接线缺口；最终修正范围和重跑结果以 [review.md](review.md) 为准，不能将下列原始结果直接当作最终验收结论。

## Scope

四项交付按 proposal/specs 逐一验收;基线为分支起点 `e2004611`。代码面:`execution/entry_service.py`、`execution/entry_events.py`、`execution/task_run_registry.py`、`studio/workflows/execution_service.py`、`core/composition/entry_assembly.py`、`core/composition/wiring.py`、`hecate-durable` `contracts/events.py`(`EventKind.TERMINAL` additive)。

## Results

### 1. 同步失败终态信封

- `EventKind` 增加 `TERMINAL`;`RunEventMapper.write_terminal_envelope` 以 run 单调序列写入事件存储,payload 携带 state/stop_category/error type/ artifacts,schema ref `hecate.entry.terminal/0`。
- 同步排空异常时引擎事件经异常属性 `engine_events` 带出并尽力映射;失败恰写一条终态信封(先证据后投影)。
- 单测:同步异常(带/不带部分事件)、停止三分类(cancelled/iteration_limit/budget_exhausted)、流式中断、payload 无堆栈与内部字段(`test_entry_contract_closure.py`,17 passed)。

### 2. 登记与失败事实的事务所有权

- `EntryExecutionService` 新增 `registry_session_factory`;登记(含 conversation link)与终态投影走独立会话即时提交,`self._db` 仅只读解析;lifespan `attach_state_stores` 注册应用 sessionmaker,全部入口自动生效;工厂缺省时维持 savepoint 历史路径。
- 测试:执行(含引擎异常)后调用方外层回滚,新数据库会话仍可查询 Task/Run、失败终态投影与信封;调用方未提交业务写入("ghost-agent")未落库(修复未提交调用方事务)。WAL 文件库双连接(`test_entry_registration_durability.py`,2 passed)。
- fail-open:工厂路径注册表拒绝时结果携带显式 missing 标记与原因,执行不受拖累;legacy 路径既有回归保持。

### 3. 声明产物引用映射

- 结果 dict 标准键 `declared_artifacts`(引擎/adapter 产生,读取后弹出,请求参数无法注入)经 kind=ARTIFACT + issuer 域匹配 Task/Run platform 域校验;通过项进入 Run 投影 `artifacts` 与终态信封,拒绝项在信封 `extra.rejected_artifacts` 留证据且不翻转执行终态。
- 测试:合法引用可读、伪造/跨域/错误 kind 拒绝、请求侧注入无效(`test_entry_contract_closure.py`)、helper 边界(`test_entry_events.py`)。

### 4. PostgreSQL 实际入口端到端(独立交付项,显式记录)

- `test_entry_contract_pg_e2e.py` 参数化于 `HECATE_STEP6_POSTGRES_URL`:平台 ORM 与事件存储(events 表)均建在 PostgreSQL(每测试独立 schema,测试后 DROP CASCADE);经实际 HTTP 入口(ASGI,真实执行服务 + Pregel 图,仅 provider/tool 边界 stub)执行四类请求:
  - **成功**:200,新会话查 Run 投影 succeeded,空内存态 mapper 从 PG 读出 TERMINAL succeeded 信封;
  - **拒绝**:未知 agent 404,响应无伪造 correlation;
  - **异常**:provider 中途抛错 → 500 纯文本(不含 "provider exploded"/堆栈);调用方请求事务回滚后,失败 Run 与 TERMINAL failed 信封(execution_failed/RuntimeError)从 PG 持久层恢复读取;
  - **停止**:结构化输出修复失败 → 409 invalid_output,投影与信封停止类别一致。
- 实际运行:对本地 PostgreSQL(postgres:16,独立容器,端口 5433)`1 passed in 121s`。未配置 URL 时模块显式 skip(0.18s,skip 理由注明"不以 skip 冒充通过");SQLite 层回归独立保留。
- 本项为 change 的独立验收记录,不藏在测试说明中(本节即验收记录)。

### 全量验证命令与结果

- 宽回归(channel/execution/a2a/scheduling/workflow/MCP agent_chat/IM provider/routing/分层,同 step5d 复核记录的可复现命令):**826 passed, 33 skipped, 0 failed**(503.71s)。
- `hecate-durable` 包测试(EventKind 契约变更回归):**49 passed, 1 skipped**。
- `ruff check src/hecate/ packages/hecate-durable/src/hecate_durable/ tests/`:通过;`ruff format --check src/ tests/`:通过。
- `mypy src/`:634 source files,零错误。
- `openspec validate step5d-entry-contract-completion --type change --strict`:通过。

## 收尾

- 演进方案 step5d 第一条剩余项已勾选,注明四子项由本 change 关闭;step5d 其余两条剩余项(定时 workflow 身份/A2A 映射、G3 证据)保持未完成。关闭的是第一条剩余项,不是整个 step5d。

## 边界与未验证项(如实记录)

- ASGI 满足 PG 端到端;真实 TCP 中途断连仍归 G3,未在本 change 认证。
- 独立 workflow 的 Run/部署身份、默认引擎开关(维持关闭)未变更。
- 生产 PG 组合的性能/并发(连接池、advisory lock 争用)未压测;本验收只证明行为正确性。
- PG 验收使用一次性 postgres:16 容器(端口 5433),测试后已清理;CI 中该模块按环境变量参数化,未配置时显式 skip。
