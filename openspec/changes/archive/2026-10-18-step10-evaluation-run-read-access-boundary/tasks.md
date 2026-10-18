# Tasks: step10-evaluation-run-read-access-boundary

## 1. 边界实现(`src/hecate/ops/api/evaluation.py`)

- [x] 1.1 新增 `_get_visible_run_or_404(run_id, db, ctx)` 与 `_forbidden(exc)` 私有 helper:helper 仅检查 ownership(存在 + `~deleted` + 非 system 时同 workspace);端点在全部目标 ownership 通过后检查 role(`ensure_workspace_role(ctx, VIEWER)`);统一 404 envelope `{"error": {"code": "NOT_FOUND", "message": "Run not found", "details": None}}`,不回显 run_id。验证:`python -m pytest tests/test_api/test_evaluation_run_access_boundary.py -q`(先落最小用例)或模块可导入且回归不破坏。
- [x] 1.2 `list_runs`:`ensure_workspace_role(ctx, VIEWER)` 前置;非 system 身份在 `base_query` 加 `workspace_id == ctx.workspace_id`,dataset/task 筛选追加其后;system scope 无 workspace 过滤。响应结构与分页不变。验证:双 workspace 列表用例(total 只计本 workspace)+ system 跨 workspace 用例。
- [x] 1.3 `get_run` 与 `get_run_scores`:改用/前置 `_get_visible_run_or_404`;scores 对不可见 Run 返回 404(替代空列表);统计逻辑不变。验证:detail/scores 的 404 一致性用例(不存在/软删除/跨 workspace 三态同 body)。
- [x] 1.4 `compare_runs`:baseline/candidate 分别经 `_get_visible_run_or_404` 验证(`db.get` 改显式 select 纳入 `~deleted`),统一 404 envelope 且不回显 run_id;通过后调用一次 `ensure_workspace_role(ctx, VIEWER)`;422 分支、阈值、响应模型与比较算法不变。验证:compare 不可见/软删除 404 用例 + 403 用例 + 既有 422 回归。

## 2. 边界测试(`tests/test_api/test_evaluation_run_access_boundary.py`,新增)

- [x] 2.1 双 workspace 隔离:W2 成员对 W1 Run 的 detail/scores/compare 均 404 且与随机不存在 ID 的响应体一致;list 只返回 W2 条目且 `total` 正确。验证:`python -m pytest tests/test_api/test_evaluation_run_access_boundary.py -q` 对应用例通过。
- [x] 2.2 角色门槛:viewer 可读四端点;role=None 且所有目标均属同 workspace 的身份四端点 403;任一目标不可见时优先 404;system key 跨 workspace 可读。验证:对应用例通过。
- [x] 2.3 缺 workspace 非 system 身份:四端点被拒(不退化为全局查询);软删除 Run 三端点 404;筛选注入(W1 成员传 W2 `dataset_id`/`task_id`)返回空列表且 `total == 0`。验证:对应用例通过。
- [x] 2.4 行为细节:可见但空 scores 的 Run 返回 `{"items": [], "total": 0}`;compare 404 body 不含 run_id;被拒请求不改变 Run/score 状态(纯读断言)。验证:对应用例通过。

## 3. 回归与共享检查

- [x] 3.1 评测 API 回归:`python -m pytest tests/test_api/test_evaluation_api.py tests/test_api/test_evaluation_tasks_api.py tests/test_api/test_evaluation_reports_api.py tests/test_api/test_evaluation_backflow_api.py tests/test_api/test_evaluation_annotations_api.py -q` 全绿;如存在对 scores 空列表旧行为的既有断言,按新契约(404)核对修正并在本任务记录修改点,不允许批量放宽断言。
- [x] 3.2 关联面回归:`python -m pytest tests/test_api/test_publish_gate_version_api.py tests/test_api/test_agent_version_evaluation_binding.py -q` 与 Workflow compare 相关测试全绿;CLI/前端为同 workspace 消费者,无需代码改动(结论记录到 verification.md)。
- [x] 3.3 共享检查:`bash scripts/dev.sh check all` 通过(ruff + format + 类型检查);OpenSpec strict validation 通过(`openspec validate step10-evaluation-run-read-access-boundary --strict`);规划数据检查(feature inventory check)无漂移。

## 4. 收尾

- [x] 4.1 在 `verification.md` 记录基点(origin/main `799476f9`)、实际运行的测试命令与结果、跳过范围及原因;核对四端点行为与 delta spec 场景一一对应。
- [x] 4.2 勾选本文件完成项,自查 diff 仅触及 `src/hecate/ops/api/evaluation.py`、`tests/test_api/test_evaluation_run_access_boundary.py`、本 change artifacts 与(若回归要求修正)`tests/test_api/` 下既有评测测试断言;确认未触及 Dataset CRUD、评测引擎、前端与共享 skill 文件。

## 5. 审查修正

- [x] 5.1 将 role 检查移至端点,compare 在 baseline/candidate 两侧 ownership 均通过后才检查一次 viewer role;新增 viewer/无角色 × 两侧输入 × 不存在/已删除/跨 workspace 的回归矩阵。
- [x] 5.2 修正 task_id 隔离测试为真实 Task 关联,增加 system 查询正对照;补齐 system 跨 workspace compare 验证。
- [x] 5.3 复跑边界与关联 API 测试、共享检查、OpenSpec strict validation 和规划数据检查,将实际结果记录于 verification.md。
