# Design

## Context

存储现状（origin/main `f6a696b4` 已核实）：`ControlCommandRecorder` ABC（`packages/hecate-durable/src/hecate_durable/seams.py`）只有 `record`/`transition`/`get` 三个方法；`list_pending_commands(workspace_id)` 在 `SqlDurableStore`（storage/store.py）与 `InMemoryControlCommandRecorder`（stub.py）上都有但**不在 ABC 上**——`TaskControlService.pending_reconciliation` 是鸭子类型消费。`CommandRow`（durable_command 表）已有平铺列：`command_id`(PK)、`kind`、`issuer`、`task_issuer`、`task_id`、`issued_at`(String ISO)、`state`(String)、`workspace_id`、`run_issuer`/`run_id`、`expires_at`、`expected_revision`、`payload`、`payload_schema_ref`、`detail_ns`、`extra`、`updated_at`。

服务与路由现状：`TaskControlService.get_command`（task_control.py）带懒过期（requested + expires_at 已过 → transition 到 expired）；`GET /api/commands/{command_id}` 路由（channel/api/tasks.py）的白名单九字段即本变更沿用目标。Task 可见性校验与统一 404、存储失败固定文案 500 的纪律在 `list_run_actions`（step14-run-action-query）已建立。测试先例：`tests/test_api/test_run_actions_api.py`（真实依赖成功路径 + 404 拒绝矩阵 + 存储 500），`tests/test_channel/test_task_control_api.py`（命令下发与单条读取）。

## Goals / Non-Goals

**Goals:**

- 一个只读列表方法贯穿三层：recorder ABC → SQL/InMemory 双实现 → `TaskControlService` → 薄路由。
- 复用既有列与排序纪律，零迁移、零新表。
- 双实现行为由参数化套件钉住（`durable-execution-storage` 的既有 requirement 要求"语义逐点一致"）。

**Non-Goals:**

- 不把既有 `list_pending_commands` 移上 ABC（本变更只管新方法；鸭子类型遗留单独登记，不搭车）。
- 不做状态筛选、时间范围过滤、跨 Task 聚合；不做懒过期清扫；不改单条查询、Runtime 派发、批处理、前端。

## Decisions

1. **新方法 `list_task_commands(workspace_id, task_ref, limit, offset) -> tuple[list[ControlCommandRecord], int]` 加入 `ControlCommandRecorder` ABC**。返回元组（items, total）而非新 dataclass——消费方只有 service 一处，契约类型零新增。备选：鸭子类型（沿用 `list_pending_commands` 现状）——被否：`TaskControlService._recorder` 的静态类型是 `ControlCommandRecorder`，mypy 干净消费要求方法在 ABC 上；两实现都在仓库内，扩展既有 ABC 不违反 runtime-pluggability（无新接缝）。
2. **SQL 实现单条 SELECT + 单条 COUNT，全部下推数据库**：`WHERE workspace_id = :ws AND task_issuer = :ti AND task_id = :tid ORDER BY issued_at DESC, command_id DESC LIMIT :limit OFFSET :offset`，`SELECT count(*)` 同 WHERE。每次调用独立 session（既有纪律）。备选：`count(*) OVER ()` 窗口一语句取回——被否：两语句更可移植（SQLite/PG 一致），且与 `list_publications` 的 count+select 先例一致。
3. **`issued_at` 是 String(64) 列，字符串排序的前提要显式化**：本查询按持久化字符串排序，不重新解释历史时间；平台当前写入使用 UTC ISO 格式，调用者应维持该格式约定；`command_id DESC` 兜底同刻排序稳定性。该前提写入 design 与测试注释；不为此变更改列类型（改列属 schema 变更，超出边界）。
4. **Task 可见性先行，复用 `list_run_actions` 的校验形状**：service 先经 registry 解析 Task（workspace/删除/存在性），从该可见行的 issuer_domain 与 id 构造完整引用（不硬编码 hecate），不可见统一抛 `TaskControlNotFoundError`（消息只含请求的 task_id）且**零 recorder 调用**；HTTP 层经 `_map_service_error` → 404。存储失败转固定文案 500（消息只含 task_id 与固定文本，异常原文仅入内部 cause），复用 run-action-query 建立的异常边界——注意此处异常来自 `asyncio.to_thread` 内的 recorder，包装点在 service 层，覆盖读取与白名单投影。
5. **列表绝不调 `get`/`transition`，投影逐字段构造**：服务层在同一异常保护内完成单条九字段投影，路由只返回服务结果；共享 command_receipt_summary 同时供既有单条查询复用，逐字段赋值（禁止 `**record` 或 dict 展开——step14 API 切片的既定纪律），`task_ref`/`run_ref` 经 `to_dict()`；`payload_schema_ref`/`payload`/`detail_ns`/`extra` 不出服务边界。懒过期清扫只属于单条读取与 reconciliation，列表返回原文——spec 已把这条边界写成 requirement。
6. **测试三层分工**：
   - 存储层参数化套件位于 packages/hecate-durable/tests/test_task_command_query.py（InMemory + SQLite SQL，可选 PostgreSQL）：五状态、排序/分页/total、完整 Task 引用隔离（同 id 异 issuer）、只读零副作用（前后行/事件比对）、空结果、既有方法回归。
   - SQL 观察套件同时断言仅 SELECT/BEGIN、COUNT 与分页 SELECT 数量，以及 WHERE 的 workspace/task_issuer/task_id、ORDER BY、LIMIT/OFFSET 子句，避免仅凭 SELECT 就声称已验证 SQL 下推。
   - HTTP 套件（真实 service 依赖走成功路径，不替换 `_get_task_control_service`——run-action-query 教训）：分页拼接、五状态原文、白名单键集断言、404 拒绝矩阵（missing/deleted/foreign workspace/零 workspace）、过期 requested 保持原状、单条 GET 懒过期不回归、存储 500 固定文案。
7. **存储查询输入边界与测试隔离**：SQL 和 InMemory 均拒绝非 TASK 引用，以及 limit 不在 1–100 或 offset 为负的参数。存储契约测试复用现有 decision_database 隔离 helper：SQLite 使用临时文件，PostgreSQL 每例使用独立临时 schema；不 drop/create 共享表。平台服务/API 测试使用既有 db_session 与隔离 SQLite durable 存储。
8. **`asyncio.to_thread` 包装**：recorder 方法是同步的，service 层沿用既有 `asyncio.to_thread(self._recorder.list_task_commands, ...)` 形态。

## Risks / Trade-offs

- [`issued_at` 字符串排序对未来写入方格式漂移脆弱] → design 决策 3 显式记录前提；双实现一致套件钉住当前行为；若未来出现混合格式写入方，属新变更（列类型或排序键迁移）。
- [LIMIT/OFFSET 深翻页在大历史上成本线性增长] → 命令量级为人工控制操作（每 Task 个位数到十位数），offset 分页足够；keyset 分页不在本切片。
- [workspace 归属依赖记录时写入的 `extra.workspace_id`/`CommandRow.workspace_id` 列] → 与 `list_pending_commands`、单条 `get_command` 同一归因来源；本变更不改变归因语义，仅复用该列过滤。
- [ABC 扩展对包外实现者是破坏性变更] → `hecate-durable` 为 monorepo 内包，ABC 仅两实现且均在仓库内；接口为纯新增（默认实现不提供，强制两实现显式给出，避免隐式错误共享）。
- [查询引用与记录引用的 issuer 来源耦合] → 列表按可见 Task 行的 `issuer_domain` 构造引用，而命令记录侧（`issue_command`/`_reject_callback`）仍以 `task_ref_of`（PLATFORM_ISSUER）锚定——该锚定同时被 durable Task 状态与托管投递查找消费，不能在本切片单独改。当前唯一 `create_task` 调用点使用默认 issuer，两侧引用对全部可达 Task 等价；若未来引入非默认 issuer 的 Task，记录侧必须与查询侧在同一变更内迁移，否则已下发命令不会出现在列表中。`test_service_uses_the_visible_task_issuer` 钉住查询侧语义。

## Migration Plan

纯增量：ABC 新方法 + 两实现 + service 方法 + 一条路由。无迁移、无配置变化。回滚 = revert 提交。

## Open Questions

（无。）
