# Verification Record

## 范围与结论

审查分支：`feat/step10-evaluation-report-aggregate-access-boundary`，基点 `69106c0e`。工作树：`D:/Workspace/Github/xueyufish/.worktrees/step10-evaluation-report-aggregate-access-boundary`。

三个聚合入口的实现符合约定：先显式检查 active workspace（包括 system 身份），缺失返回 403 `NO_WORKSPACE`；再检查最低 VIEWER 角色，无角色返回 403 `FORBIDDEN`。检查位于服务调用前，服务继续接收原 workspace UUID。审查未发现需要修改的生产逻辑问题，保留 GLM 的路由实现。

本轮修正新测试的断言与夹具，校正文档和验证记录。未改报告服务、distributions、sessions、其他 API、前端或 Runtime。改动保持未提交。

## 审查修正

- 原成功角色测试把状态码断言放在循环外，只检查最后的 breakdowns。改为三入口 × VIEWER/EDITOR/ADMIN 的独立参数化用例，每个请求均验证状态码和真实聚合数据。
- 固定测试窗口包含所有种子数据，避免默认时间窗口移出种子日期后，仅以空报告掩盖错误。
- 租户隔离从仅 overview 扩展到三个入口、全部允许角色。默认零 UUID workspace 与随机非零 workspace 同时存在不同数据，两者各自只返回自己的结果，防止遗漏过滤或零 UUID 回退。
- 无角色身份不再插入 `WorkspaceMemberModel(role=None)`：该列的 ORM 默认值会把 None 转为 VIEWER，与模拟的无角色 AuthContext 不一致。
- 三入口 × 无角色/普通无 workspace/system 无 workspace 独立验证错误。请求阶段拦截 `AsyncSession.execute` 及三个聚合方法，确保拒绝先于资源查询和聚合调用。
- 成功和拒绝路径均比较 EvaluationRun、EvaluationTask、EvaluationScore、EvaluationTaskScore 四表的完整快照；四表均有种子数据。合法空 workspace 仍返回正常空报告。
- 复用原先未使用的快照助手，删除重复快照实现，修正 proposal/design 中角色、显式判空与依赖注入的误述。

## 本轮实际验证

使用本 worktree 的 `.venv/Scripts/python.exe`。

| 检查 | 命令 | 结果 |
|---|---|---|
| 边界与报告回归 | `python scripts/dev.py test tests/test_api/test_evaluation_reports_aggregate_boundary.py tests/test_api/test_evaluation_reports_api.py tests/test_services/test_evaluation/test_reports_reconciliation.py tests/test_api/test_evaluation_reports_distribution_boundary.py -q -rs --tb=short` | **66 passed**，无跳过；其中新增边界文件 19 个用例 |
| 共享检查 | `python scripts/dev.py check all` | ruff check 通过；ruff format 1625 files already formatted；mypy 962 source files 无错误 |
| OpenSpec | `openspec validate step10-evaluation-report-aggregate-access-boundary --strict` | valid |
| 功能清单增量检查 | `python scripts/feature_inventory.py check --base-ref 69106c0e` | 413 entries，0 errors，189 warnings；0 new/changed entries，警告均为未变化的历史债务 |
| 差异检查 | `git diff --check` | 通过 |

既有报告 API、reconciliation 和 distributions 边界测试覆盖原响应、分桶、过滤、错误哨兵、人工覆盖与原始分来源统计，不修改其算法和用例。

## 未测范围

测试使用仓库既有 in-memory SQLite 夹具。本轮未运行 PostgreSQL 集成、完整 tests/、全量 test_api 或 CI 平台矩阵；原记录中的全量 API 数字不作为本轮验证结果。完整测试与平台矩阵仍由 CI 验证。

不新增 system 全局聚合或 workspace 选择能力；sessions 边界不在本 change 范围。
