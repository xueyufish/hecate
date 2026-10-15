# step5d 入口契约实现复核

## 基线与结论

基线 `e20046116cee711d93b6d9c449338dd4d2c339fc`，用户指定 worktree `D:/Workspace/Github/xueyufish/.worktrees/step5d-entry-contract-completion`，复核时 GLM 实现未提交。按 proposal、design、delta spec、tasks 和演进方案追踪入口、持久事务、真实适配器、公开查询及 PostgreSQL 验收。原实现有明确契约缺口，已在同一 change 修正，不创建另一套执行生命周期。

## 发现、修正与证据

| 优先级 | 原实现问题 | 修正与验证 |
|---|---|---|
| P1 | 产物只比较 kind 和 issuer；平台不同 workspace 共用 hecate 域，同 issuer 的外域记录或伪造 id 可进入投影 | 以权威 EvidenceModel 的 id、workspace 和删除状态验证；未知归属拒绝。真实 PG 同步/流式工具声明覆盖合法、跨 workspace、未知与已删除记录 |
| P1 | 同步真实 WorkflowExecutionService 丢弃最终产物声明；原正例直接替换整套执行服务，未验证生产适配器 | 同步/流式保留图声明，并采集新工具回执中的显式声明。排除输入历史、拒绝 workflow input_data 注入输出通道。PG 用真实 HTTP、Pregel、工具网关和适配器执行，仅替换模型/工具提供方 |
| P1 | RunEventMapper 内部可以读到事件，但公开 /api/runs/{id}/events 查另一套平台事件表，返回空页 | 新 Run 登记平台来源标记，终态保留；workspace 校验后，公开分页与订阅读取同一入口事件源。PG 验证分页、终态后订阅结束、公开投影及跨 workspace 拒绝；隐藏 backend detail 和原始拒绝候选 |
| P1 | 仅捕获 Exception，asyncio.CancelledError 和 GeneratorExit 直接跳过流式终态与投影 | 取消/生成器关闭进入 cancelled 收尾，取消作用域内保护证据提交，并关闭底层生成器。新增实际异常类型反例；不以此声称完成真实 TCP 断连认证 |
| P1 | Python 增加 terminal 事件类型，公开 event-envelope JSON Schema 仍只接受 event/gap | 同步 Schema 及终态标准样例，新增实际生成信封对公开 Schema 的回归 |
| P2 | 注册工厂只在 ASGI lifespan 注入，stdio MCP 等入口继续退回调用方事务 | composition 在无 lifespan 时懒解析应用工厂；显式 None 注册才选择历史路径，保留明确的兼容与测试边界 |
| P1 | PG 成功用例的桩工具缺可信元数据，真实调用失败但模型仍给出答案；拒绝只测未知 Agent 404，未测执行内策略拒绝；未测产物、流式或公开事件查询 | 修正工具元数据，并断言真实派发次数。补同步/流式产物及策略拒绝、公开查询、回滚隔离；未知 Agent 404 保留为附加负例。现有 PG CI job 显式运行该模块 |
| P2 | PG 夹具退出后未恢复全局存储和 app 配置，可能留下指向已删除 schema 的实例 | 通过测试级 monkeypatch 恢复 composition、app.state、依赖覆盖与 API key 配置 |
| P2 | 事件写入失败仍先递增序号并放入内存成功集合；分页按数组下标解释序号游标 | 持久追加成功后再推进内存视图；分页按 source_sequence 筛选。写入失败反例证明无虚假事件和漏页，终态追加在单次执行 mapper 内去重 |

登记的独立事务设计保留，未调用调用方业务会话的 commit。新增 PG HTTP 反例先 flush 调用方业务写入，再运行成功/异常请求并回滚；新会话验证 Task/Run 与信封存在，同时业务行不存在。原 SQLite 用例在执行之后才写入业务行，保留为补充证据，不代替该 PG 反例。

## 验证结果

- 原 GLM 定向基线：32 passed，保留空字符串 raises 匹配警告；复核将断言改为实际异常类型。
- 新增反例复现 Schema 拒绝 terminal、同 issuer 跨 workspace 引用放行，以及取消/生成器关闭后没有终态。真实适配器产物丢失同时由代码核对和最终集成回归覆盖。
- 首轮宽回归：828 passed、39 skipped、7 failed。失败全部是旧测试精确比较投影时未包含新入口来源字段；已保持原状态/摘要断言并加入来源字段验证。
- 最终宽回归：838 passed、39 skipped、0 failed。范围为 channel、execution、A2A、scheduling、workflow、MCP agent_chat、IM provider/routing 与入口/领域分层。
- 宽回归条件跳过：JS 试点依赖未安装、PG 专项环境变量未设置及由专用 binding 测试覆盖的多 schema 样例；PG 入口和原生续跑已分别显式执行。其余可选 PG/试点能力不因此获认证。保留 12 条收集、依赖弃用与测试桩 coroutine 清理警告。
- PostgreSQL 入口专项：7 passed。包含同步/流式产物、真正的策略拒绝、公开分页/订阅/权限、已有业务写入后的成功/异常回滚，以及原成功/异常/停止用例。全部使用本轮独立 postgres:16 测试容器，平台 ORM 与事件表均在 PG，查询使用新会话及空 mapper。
- 原生 PostgreSQL 续跑兼容性：5 passed。首次因 worktree 未装已声明的 psycopg 驱动出现 fixture setup errors；使用 uv 安装现有可选依赖后全量通过，未修改依赖声明。
- S12 双执行路径多轮/停止/结构化输出：28 passed。
- hecate-durable 整包回归：49 passed、1 条可选配置跳过。各次运行分开记录，不累计为去重用例总数。
- Ruff check、全量源文件/测试格式检查通过；mypy 覆盖 src 与 hecate-durable，652 个源文件零错误。OpenSpec change strict validate 和 git diff --check 通过。

## 复现

```powershell
.venv/Scripts/python.exe -m pytest tests/test_channel/ tests/test_execution/ tests/test_a2a/ tests/test_services/test_scheduling/ tests/test_services/test_workflow/ tests/test_mcp/test_agent_chat_entry_parity.py tests/test_core/test_im_providers.py tests/test_gateway/test_im_routing.py tests/test_layering_domain.py tests/test_layering_entry_imports.py -q -rs --timeout=90 -p no:cacheprovider
.venv/Scripts/python.exe -m pytest tests/test_execution/test_entry_contract_pg_e2e.py -q --timeout=120 -p no:cacheprovider
.venv/Scripts/python.exe -m pytest tests/test_execution/test_builtin_continuation.py -q --timeout=120 -p no:cacheprovider
.venv/Scripts/python.exe -m pytest tests/scenarios/test_s12_autonomous_multiturn.py -q --timeout=120 -p no:cacheprovider
```

PG 两个命令须显式配置指向一次性测试数据库的 HECATE_STEP6_POSTGRES_URL；不能用未配置时的 skip 代替执行证据。测试会创建并删除自己的临时 schema。本轮测试容器为 `hecate-entry-contract-review-pg`，端口 `127.0.0.1:55433`，不使用机器上既有业务数据库；验收结束后已移除该临时容器及其测试数据卷。

## 支持边界与交付状态

- 产物映射支持可从当前 workspace 的存量 EvidenceModel 验证的引用；未接入的文件/远程产物命名空间保持未验证并拒绝发布，不增加完整产物 registry 或内容存储。仅同域、模型自报或输入字段不构成资源授权。
- 回滚持久性以实际 PG 独立连接验收；显式选择历史 savepoint 的装配不具备该保证。SQLite WAL 不消除并发写锁，不据此宣称支持调用方持有写锁时的独立写事务。
- 新来源标记只用于本次路径新建的 Run，不回填旧 Run、不补造历史终态或产物。
- 终态事件使用未冻结的契约草案；旧消费者若硬编码旧 kind 枚举需更新。真实 TCP 断连、独立 workflow 身份、A2A per-agent 映射、安装/升级 profile、默认引擎翻转和完整生产认证均不在本轮。
- 仅关闭方案 step5d 第一条剩余项在上述范围内的契约交付；其余剩余项保持未完成。
- 所有修正留在用户指定工作树，本轮未提交、推送或归档；主 checkout 不改动。
