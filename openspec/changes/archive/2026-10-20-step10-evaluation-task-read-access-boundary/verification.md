# 复审验证

## 复审基线与范围

- worktree: `step10-evaluation-task-read-access-boundary`,分支 `feat/step10-evaluation-task-read-access-boundary`,HEAD `d2e91a23`。
- 未执行 commit、push 或 rebase;现有 `.agents/skills/openspec-propose/SKILL.md` 无关修改保留未动,应排除本功能提交。
- AST 与 HEAD 比对:API 仅修改 `list_tasks`/`get_task`/`list_task_runs`,仅新增 `_get_task_for_read`;service 仅修改 `list_tasks`/`list_task_runs`。写接口、`get_task`/`_get_task_or_raise`、评分查询、报告 API、平台 Task 控制、Runner 与 Runtime 不变。

## 发现与修正

- Run 列表原有 `EvaluationTaskNotFoundError → HTTP 404` 映射在改造中丢失。Task 在边界解析和 service 查询之间消失时,异常未经处理并造成服务器错误。补测先在 GLM 实现上实测失败(异常外泄),再恢复恒定无 ID 的 404 映射。
- API 使用边界解析返回的 Task 的真实 workspace 调用 `list_task_runs`,system 与普通身份一致。service 保持非 Optional workspace 参数及原 `_get_task_or_raise` 路径,移除新增的重复无作用域 Task 查询。读取 helper 返回类型明确,缺 workspace 的非 system 在查询前返回恒定 404。
- 原测试未完整覆盖 status 过滤、确定排序、Run 多页及越界分页、system 下脏关联过滤、完整错误 envelope 和读取前后真实状态。补齐上述回归,使用表查询直接读取 Task/Run 全部持久列,覆盖 JSON、删除标记、时间戳,避免 ORM identity map 掩盖变化。
- 修正文档中“唯一未闭合的评测读取资源”表述;其他读取入口和写入权限仍属后续切片。

## 实际执行结果

使用本 worktree 的 `.venv/Scripts/python.exe`:

- 原实现: `-m pytest tests/test_api/test_evaluation_task_access_boundary.py tests/test_api/test_evaluation_tasks_api.py -q --tb=short` → **32 passed**。
- 新增缺陷回归、修正前: `-m pytest tests/test_api/test_evaluation_task_access_boundary.py::test_task_disappearing_before_run_query_keeps_404 -q --tb=short` → **1 failed**,确认为未处理的 `EvaluationTaskNotFoundError`。
- 修正后: `-m pytest tests/test_api/test_evaluation_task_access_boundary.py tests/test_api/test_evaluation_tasks_api.py -q --tb=short` → **39 passed**。
- `openspec validate step10-evaluation-task-read-access-boundary --strict` → 通过。
- `scripts/feature_inventory.py check` → **413 entries,0 errors,189 warnings**;warning 为现有显式治理债务/待补 evidence 与 acceptance,本 change 不修改目录数据。
- `git diff --check` → 通过。
- 共享 Ruff check、format check → 通过(1623 files)。

- `-m pytest tests/test_api tests/test_services/test_evaluation -q -n 4 --tb=short` → **1133 passed,8 skipped,20 warnings**,退出码 0。跳过用例为仓库显式标记的 Schedule API/PostgreSQL advisory locks 与 Security audit API/PostgreSQL session singleton;弃用警告来自 FastAPI 422 常量和 lark_oapi UTC 时间转换。
- `scripts/dev.py check all` → **通过**,退出码 0:共享 Ruff check、format check(1623 files)、mypy(src/packages/scripts/dev.py,962 source files)全部无错误。

仓库完整 pytest 与平台矩阵由 CI 执行,本地范围以上方实际命令和后续完成结果为准。
