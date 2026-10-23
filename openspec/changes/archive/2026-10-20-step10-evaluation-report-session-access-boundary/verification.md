# Verification Record

## 审查范围与结论

基点：`a8b1aeed`。分支：`feat/step10-evaluation-report-session-access-boundary`。工作树：`D:/Workspace/Github/xueyufish/.worktrees/step10-evaluation-report-session-access-boundary`。

GLM 的生产授权逻辑符合约定，审查保留其实现：不带 `task_id` 复用 aggregate gate；带 `task_id` 先拒绝普通身份的缺 workspace 请求，再解析 Task 可见性，最后检查 VIEWER。system 的 Task 模式使用目标 Task 实际 workspace，workspace 模式缺 workspace 时仍拒绝。本轮生产文件只额外更新共享 404 助手的说明，使其涵盖 distributions 与 sessions。

本轮修正测试夹具、扩大必要边界覆盖并校正文档。未修改报告服务算法、其他报告入口行为、评测 Task CRUD、平台 Task、前端或 Runtime。所有改动保持未提交。

## 本轮修正

- 无角色夹具不再插入 `WorkspaceMemberModel(role=None)`：ORM 默认值会将其变成 VIEWER，与无角色 AuthContext 不一致。
- 原成功用例仅覆盖 VIEWER；现在两种模式均参数化验证 VIEWER/EDITOR/ADMIN，使用固定窗口和真实评分，避免空报告掩盖过滤错误。
- 两个实际 workspace（含合法零 UUID 与随机非零 UUID）同时存在不同数据，分别验证双向隔离；同一 workspace 中放置两个 Task，确认带 `task_id` 只返回目标 Task 的 sessions。
- 原“归属先于角色”用例使用 VIEWER，不能证明角色拒绝顺序；现在缺失/已删除/跨 workspace × VIEWER/EDITOR/ADMIN/无角色均检查恒定、无标识的 404，并阻止聚合服务调用。
- workspace 模式的无角色、普通缺 workspace、system 缺 workspace，以及 Task 模式普通缺 workspace，在请求期间同时拦截 `AsyncSession.execute` 与 `session_rollup`，证明查询和聚合前拒绝。Task 模式可见无角色身份返回 403，且不调用聚合服务。
- system 改用非零目标 workspace 的真实数据，加入同 workspace 其他 Task 和同 Task ID 的其他 workspace 评分作为干扰，排除全局、零 UUID 或 null 过滤回退。
- 普通身份和 system 均验证已删除 Task 即使残留评分也返回 404；system 缺失 Task 同样拒绝。
- 成功、拒绝、合法空结果及参数校验路径均比较 Task/TaskScore 全表快照，覆盖只读保证；保留 422 参数错误、400 时间窗口错误与正常空报告。
- proposal/design/tasks/specs 与实际验收对齐。删除原记录中无证据的“零 UUID workspace 一律不可见”描述，零 UUID workspace 按既有契约合法处理。

## 本轮实际结果

以下命令使用该 worktree 的 `.venv/Scripts/python.exe`。

| 检查 | 命令 | 结果 |
|---|---|---|
| 新边界与报告回归 | `python scripts/dev.py test tests/test_api/test_evaluation_reports_session_boundary.py tests/test_api/test_evaluation_reports_api.py tests/test_api/test_evaluation_reports_aggregate_boundary.py tests/test_api/test_evaluation_reports_distribution_boundary.py tests/test_services/test_evaluation/test_reports_reconciliation.py -q -rs --tb=short` | **96 passed**，无跳过；新边界文件 30 个用例，既有四文件合计 66 个用例 |
| 共享检查 | `python scripts/dev.py check all` | ruff check 通过；ruff format 1629 files already formatted；mypy 962 source files 无错误 |
| OpenSpec | `openspec validate step10-evaluation-report-session-access-boundary --strict` | valid |
| 功能清单增量准入 | `python scripts/feature_inventory.py check --base-ref a8b1aeed` | 413 entries，0 errors，189 warnings；0 new/changed entries，警告均为未变化的历史债务 |
| 差异检查 | `git diff --check` | 通过 |

既有报告 API（含 session 聚合、排序、分页）、reconciliation、aggregate 与 distributions 边界测试未修改且通过。本轮未改变既有聚合计算。

## 未测范围

测试使用仓库既有 in-memory SQLite 夹具。本轮未运行 PostgreSQL 集成、完整 tests/、全量 test_api 或 CI 平台矩阵。原记录中的全量 test_api 数字不作为本轮验证结果；完整套件和平台矩阵仍待 CI 验证。

不新增 system 全局聚合、workspace 选择能力或数据迁移；两种模式的授权只在 sessions API 入口收敛。
