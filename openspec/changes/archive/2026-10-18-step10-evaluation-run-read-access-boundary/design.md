# Design: step10-evaluation-run-read-access-boundary

## 背景

四个评测 Run 只读端点(`list_runs`、`get_run`、`get_run_scores`、`compare_runs`,均在 `src/hecate/ops/api/evaluation.py`)当前注入了 `AuthContext` 但未使用:列表无 workspace 过滤且 `total` 跨 workspace 统计;详情只查 `id + ~deleted`;scores 按 `run_id` 直查分数表,不验证 Run 本身;compare 有 workspace 检查但不排除软删除、无角色检查,且 404 detail 回显 run UUID,与跨 workspace 的泛化 404 可区分,构成存在性探测通道。

Run 的 `workspace_id` 由评测引擎从 dataset 继承(`engine.py:195`),数据侧具备过滤条件。平台已有可复用的边界构件:`AuthContext.is_system_scope`(仅 `api_key_scope == "system"`,JWT 缺 workspace 不构成 system)、`ensure_workspace_role` / `WorkspaceRoleRequiredError`(`core/deps_workspace.py`)、以及 #283 在 `studio/agents/versioning.py` 固定的"先 ownership 后 role、跨 workspace 一律 not-found"判定次序。

## 判定模型

对每个请求按以下次序判定,与 #283 模式保持一致:

1. **Ownership**(仅针对有资源 ID 的端点):解析目标 Run(`id` 匹配 **且** `~deleted`)。非 system 身份下,Run 不存在、已软删除或 `workspace_id != ctx.workspace_id` 三种情况返回**完全相同**的 404,不泄露具体原因。
2. **Role**:ownership 通过后执行 `ensure_workspace_role(ctx, WorkspaceRole.VIEWER)`。system scope 由其内部豁免;缺 workspace/role 的非 system 身份抛 `WorkspaceRoleRequiredError`,API 层映射为 403。
3. **读取**:通过后才查询分数或计算比较。

system scope(`ctx.is_system_scope`)跳过 workspace 匹配,跨 workspace 可读。列表、详情和分数保持 system 全局可读;compare 原先直接比较 workspace_id,会误拒缺 workspace 的 system 身份,本 change 将其统一到显式 system 豁免。

## 端点改动

### 共用 helper

新增模块级 `_get_visible_run_or_404(run_id, db, ctx) -> EvaluationRunModel`:

- `select(...).where(id == run_id, ~deleted)`;无结果或(非 system 且)`workspace_id != ctx.workspace_id` → 统一 404。
- helper 只判定 ownership,不检查 role。detail/scores 在 helper 返回后检查 `ensure_workspace_role(ctx, VIEWER)`;compare 在两侧 helper 均返回后检查一次 role,异常由端点映射为 403。
- 返回 Run 供调用方继续读取。

404 响应体统一采用 detail 端点既有的 envelope:`{"error": {"code": "NOT_FOUND", "message": "Run not found", "details": None}}`,message 不含 run_id。compare 从纯字符串 detail 迁移到同一 envelope(错误体格式变化,既有测试只断言 status_code,前端按非 2xx 统一处理)。

### `GET /runs`(list_runs)

- 先 `ensure_workspace_role(ctx, VIEWER)`(缺 workspace 非 system → 403,不退化为全局查询;system scope 豁免)。
- 非 system 身份在 `base_query` 上加 `workspace_id == ctx.workspace_id` 过滤;`dataset_id`/`task_id` 筛选追加在该过滤之后,跨 workspace 筛选值自然得到空结果(`total == 0`),不扩大范围。
- system scope 无 workspace 过滤,保持全局可读。
- 分页、排序、响应结构不变。

### `GET /runs/{run_id}`(get_run)

改用 `_get_visible_run_or_404` 并随后检查 viewer role,替换现有"仅 id + ~deleted"查询;其余统计逻辑(`metric_averages`、`total_scores`)不变。

### `GET /runs/{run_id}/scores`(get_run_scores)

先 `_get_visible_run_or_404`,再检查 viewer role,最后按现有逻辑查询分数。不可见 Run 从"200 空列表"变为 404(BREAKING,见兼容性)。分数按 `run_id` 查询,Run 验证后天然隔离,无需额外过滤。

### `POST /runs/compare`(compare_runs)

- baseline 与 candidate 分别经 `_get_visible_run_or_404` 验证(任一不可见 → 统一 404,不回显 run_id,不区分条件)。
- 两侧验证通过后调用一次 `ensure_workspace_role(ctx, VIEWER)`(非 system 两侧必属同一 workspace;system scope 豁免)。
- `db.get()` 改为显式 select,纳入 `~deleted`,修复软删除 Run 可参与比较的缺口。
- 422 分支(dataset 不同、evaluator 集不相交)、阈值解析、成功响应模型与比较算法全部保持不变。

## 403 映射

`evaluation.py` 内新增与 `studio/api/agent_versions.py:55` 相同模式的 `_forbidden(exc) -> HTTPException(403)` 私有 helper 及 try/except 包装;不跨模块导入 studio API 的私有函数。

## 兼容性

| 变化 | 影响 |
|---|---|
| scores 对不可见 Run 返回 404 | **BREAKING**(响应语义收紧)。同 workspace 正常流程(前端 `web/src/lib/api-client.ts`、CLI `cli/commands/workflow.py` 轮询自建 Run)均为同 workspace viewer+ 身份,不受影响;仅跨 workspace/已删除 Run 的旧行为(可探测的空列表)被关闭。 |
| compare 404 body 从纯字符串改为 envelope | 错误体格式变化;status code 不变,既有测试与前端按非 2xx 分支处理。 |
| 列表 `total` 与条目收窄到本 workspace | 对单 workspace 部署无感知;多 workspace 部署中此前能看到他 workspace Run 的调用方将只见本 workspace 数据,这正是本 change 目的。 |
| 成功响应结构、分页、比较算法、422 语义 | 不变。 |

平台内部消费(评测引擎、报告、发布门禁)走 service/engine 层直查 DB,不经这四个 HTTP 端点;已核实的 HTTP 消费者仅前端与 CLI,均为同 workspace 场景。

## 测试设计

新增 `tests/test_api/test_evaluation_run_access_boundary.py`,参照 `test_agent_version_access_boundary.py` 的双 workspace 模板(conftest 提供 `auth_context`/`system_auth_context` fixture 与 `get_auth_context` dependency override):

- 双 workspace:W2 成员对 W1 Run 的 detail/scores/compare 均 404,且与不存在 Run 的响应体一致;list 只见 W2 数据且 `total` 正确。
- 角色:viewer 可读四端点;构造 role=None 的同 workspace 身份断言 403(detail/scores/list/compare);system key 跨 workspace 可读。
- 缺 workspace 非 system 身份:四端点均被拒(detail/scores/compare 404,list 403),不退化为全局查询。
- 软删除:deleted Run 的 detail/scores/compare 均 404(与不存在一致)。
- 筛选注入:W1 成员用 W2 的 `dataset_id`/`task_id` 过滤 → 空列表、`total == 0`。
- 空 scores Run:可见但无分数的 Run 正常返回 `{"items": [], "total": 0}`(区分"可见但空"与"不可见 404")。
- compare 404 body 不含 run_id;422 分支(dataset 不同)回归保持。
- 纯读性:被拒请求后 Run/score 状态不变(以查询断言)。

回归:`tests/test_api/test_evaluation_api.py`、`test_evaluation_tasks_api.py`、`test_evaluation_reports_api.py`、`test_evaluation_backflow_api.py`、`test_evaluation_annotations_api.py`、Workflow compare 相关测试与 Agent 发布评测测试(`test_publish_gate_version_api.py`、`test_agent_version_evaluation_binding.py`)。注意:既有测试中直接以随机 UUID 请求 scores 端点并断言 200 空列表的用例(如有)需按新契约调整为 404——以实际回归结果为准,逐个核对而非批量放宽。

## 非目标

- Dataset 端点(`list_datasets`、`_get_dataset_or_404` 及 CRUD)的同类边界缺口:涉及写路径与 `EvaluationDatasetService`,留待独立切片,proposal 已登记。
- `POST /runs`(Run 创建)、评测引擎、发布门禁、前端、数据库迁移、共享 skill 文件。
