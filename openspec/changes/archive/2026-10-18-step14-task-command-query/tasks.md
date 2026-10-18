# Tasks

## 1. 存储层：ABC 扩展与双实现

- [x] 1.1 `packages/hecate-durable/src/hecate_durable/seams.py`：`ControlCommandRecorder` 新增 `list_task_commands(workspace_id: str, task_ref: BackendRef, limit: int, offset: int) -> tuple[list[ControlCommandRecord], int]` 抽象方法（含 docstring：完整 Task 引用限定、排序 `issued_at DESC, command_id DESC`、只读、空结果合法）。验证：mypy 通过，两实现未加时测试收集报 NotImplementedError/TypeError 的失败清单符合预期。
- [x] 1.2 `packages/hecate-durable/src/hecate_durable/storage/store.py`：SQL 实现——`WHERE workspace_id AND task_issuer AND task_id`，`ORDER BY issued_at DESC, command_id DESC LIMIT/OFFSET` + 独立 `count(*)`，独立 session，零写入。验证：见 1.4 参数化套件。
- [x] 1.3 `packages/hecate-durable/src/hecate_durable/stub.py`：InMemory 实现——过滤后计数再切片，锁内完成，语义与 SQL 逐点一致。验证：同上。
- [x] 1.4 新增 packages/hecate-durable/tests/test_task_command_query.py 存储参数化测试（InMemory + SQLite SQL，可选独立 schema PostgreSQL）：五状态原样返回、排序与分页拼接无重无漏、total 为过滤后总数、同 task_id 异 issuer_domain 隔离、跨 workspace 隔离、查询前后行内容与治理事件零变化（含过期 requested 不被改写）、空结果 `( [], 0)`、既有 `list_pending_commands`/`get` 回归。验证：`python -m pytest <新测试文件> -q` 全过。

## 2. 服务层

- [x] 2.1 `src/hecate/execution/task_control.py`：`TaskControlService.list_task_commands(workspace_id, task_id, limit, offset)`——先经 registry 校验 Task 可见性（不存在/删除/跨 workspace → `TaskControlNotFoundError`，消息仅含请求 task_id，零 recorder 调用），可见后 `asyncio.to_thread` 调 recorder；recorder 读取及服务层白名单投影异常转固定文案 500（`TaskControlError` 语义沿用 run-action-query 纪律，原文仅留内部 cause）。验证：服务层单测覆盖 404 矩阵与异常转译。

## 3. API 路由

- [x] 3.1 `src/hecate/channel/api/tasks.py`：`GET /tasks/{task_id}/commands` 薄路由（limit ge=1 le=100 默认 20、offset ge=0 默认 0），返回服务层已投影的 `{items, total}`；条目经共享函数逐字段白名单投影（command_id、kind、state、issued_at、issuer、task_ref、run_ref、expires_at、expected_revision；`to_dict()` 用于引用），不出现 payload/payload_schema_ref/detail_ns/extra。验证：HTTP 测试断言键集。
- [x] 3.2 更新 `tasks.py` 模块 docstring 的端点清单。验证：docstring 与路由一致。

## 4. HTTP 测试

- [x] 4.1 新增 `tests/test_api/test_task_commands_query.py`（沿用 run-action-query 套件形态）：真实 service 依赖的成功路径分页（limit=1 逐页拼接无重无漏、total 稳定）、五状态原文（requested/acknowledged 不变 applied）、过期 requested 在列表保持 requested 且单条 GET 仍懒过期、白名单键集断言、404 拒绝矩阵（missing/deleted/foreign-workspace）及无 workspace 的 403，均零 recorder 调用、存储异常固定 500 文案（不泄驱动原文、非空列表）、连续查询零状态推进。验证：`python -m pytest tests/test_api/test_task_commands_query.py -q` 全过。
- [x] 4.2 既有套件回归：`tests/test_channel/test_task_control_api.py` 与 durable 既有命令测试零失败。验证：定向 pytest 运行。

## 5. 共享检查与验证记录

- [x] 5.1 运行 SQL 观察套件（断言仅 SELECT/BEGIN，并验证 COUNT、WHERE、ORDER BY、LIMIT/OFFSET）证明过滤/分页/计数在库内完成。验证：套件通过。
- [x] 5.2 运行受影响范围测试（hecate-durable 包套件 + 服务/API 定向测试）与 `bash scripts/dev.sh check all`；按需配置 PostgreSQL 复跑 durable 全套双数据库回归。验证：0 失败；跳过项逐项记录原因。
- [x] 5.3 `openspec validate step14-task-command-query --strict` 通过；汇总实际结果到 `verification.md`（真实命令输出要点、跳过项与保留限制，不写预期结果）。

## 6. 审查修正

- [x] 6.1 从可见 Task 行派生完整引用，防止固定 issuer 返回同 id 的其他命令；白名单投影纳入服务层固定错误保护，单条查询复用同一投影函数。
- [x] 6.2 两种 recorder 统一验证 Task 引用 kind、limit/offset 边界；补充回归先复现 12 个失败，再验证修复后 40 个查询测试通过。
- [x] 6.3 将存储契约测试移至 durable 包，复用独立临时 schema helper；修正错误的 recorder 失败注入、补充真实过期时间并增强 SQL 结构断言。
