# Verification

## 基点与范围

基点 `799476f9`（#285），分支 `feat/step10-evaluation-run-read-access-boundary`。
本次审查修改仅涉及 `src/hecate/ops/api/evaluation.py`、新增的 `tests/test_api/test_evaluation_run_access_boundary.py` 和本 change artifacts；未修改其他 worktree，未提交或推送。

## 审查修正

1. 原 helper 在每次解析 Run 后立即检查 role。无角色调用方以本 workspace Run 为 baseline、以不可见 Run 为 candidate 时，会在验证 candidate ownership 前返回 403，违反两侧 ownership 均先于 role 的契约。helper 现在仅判定 ownership，detail/scores 随后检查 viewer role，compare 在两侧 ownership 通过后检查一次 role。
2. 原 task_id 隔离用例使用 Run ID，且 Run 没有 task_id，无法验证真实匹配记录的隔离。现创建 EvaluationTaskModel 并关联 Run，同时以 system 身份查询作为正对照。
3. 补齐 system 跨 workspace compare，并新增 viewer/无角色 × baseline/candidate × 不存在/已删除/跨 workspace 的比较边界矩阵，共 12 个参数化用例。

## 实际验证

所有 Python 命令使用该 worktree 的 `.venv/Scripts/python.exe`。

| 检查 | 命令 | 结果 |
|---|---|---|
| GLM 原边界测试 | `python -m pytest tests/test_api/test_evaluation_run_access_boundary.py -q`（补充用例前） | 13 passed |
| 修正后边界测试 | `python -m pytest tests/test_api/test_evaluation_run_access_boundary.py -q` | 25 passed，0 failed，0 skipped |
| 共享检查 | `python scripts/dev.py check all`（与 `bash scripts/dev.sh check all` 使用同一入口） | ruff check 通过；format check 1614 文件通过；mypy 959 源文件无错误 |
| OpenSpec | `openspec validate step10-evaluation-run-read-access-boundary --strict` | valid |
| 规划数据 | `python scripts/feature_inventory.py check` | 413 entries，0 errors，189 warnings |
| 最终差异 | `git diff --check` | 通过 |

最终关联 API 回归命令：

```text
python -m pytest tests/test_api/test_evaluation_run_access_boundary.py tests/test_api/test_evaluation_api.py tests/test_api/test_evaluation_tasks_api.py tests/test_api/test_evaluation_reports_api.py tests/test_api/test_evaluation_backflow_api.py tests/test_api/test_evaluation_annotations_api.py tests/test_api/test_publish_gate_version_api.py tests/test_api/test_agent_version_evaluation_binding.py -q
```

结果：**186 passed，0 failed，0 skipped，3 warnings**。warnings 为既有 `HTTP_422_UNPROCESSABLE_ENTITY` 弃用提示，分别由 evaluation API 和 dataset_versions 的 422 分支触发；本 change 未改动这些常量。规划检查的 189 个 warning 为显式登记的缺失治理字段、evidence/acceptance 债；feature 数据文件未修改。

## 覆盖说明

- 详情、scores、compare 的 workspace 隔离、404 body 一致性与 UUID 不回显。
- compare 在两侧 ownership 通过后才检查 role，任一侧不可见均优先 404；两侧可见但缺角色返回 403。
- 列表条目与 total 限定到当前 workspace；外 workspace 的 dataset_id/task_id 筛选不能扩围，task_id 包含真实关联记录和 system 正对照。
- viewer 四端点读取、无 workspace 身份拒绝、system 四端点跨 workspace 读取。
- 可见但无 scores 的 Run 保持 200 空列表；同 workspace compare 成功及 dataset 不匹配 422 保持。
- 拒绝请求前后 Run/Score 行数不变。
- 既有评测、发布门禁与版本评测绑定回归通过，无需改动旧测试断言。

## 验证范围与保留限制

- 本地未运行全仓 pytest、CI 或真实 PostgreSQL 端到端测试；本 change 的 HTTP 边界通过共享 SQLite/ASGI fixtures 验证。CI 仍由后续 PR 流程执行。
- 前端及 CLI 未修改、未进行手动冒烟；HTTP 消费点为 `web/src/lib/api-client.ts` 和 `src/hecate/cli/commands/workflow.py`，其正常同 workspace viewer+ 访问路径保持。
- Dataset CRUD、Run 创建和引擎内部查询的相邻访问边界缺口仍按 proposal 留待独立 change；本切片不代表整个评测域的授权已闭合。
