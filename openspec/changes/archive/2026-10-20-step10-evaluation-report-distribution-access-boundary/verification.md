# Verification Record

基点: `c7944bad`。工作树: `D:/Workspace/Github/xueyufish/.worktrees/step10-evaluation-report-distribution-access-boundary`。本记录替代初版验证记录,以下为复审后的实际执行结果。

## 复审修正

- 初版 Run scope 直接沿用 `Run not found`,Task scope 使用 `Report scope not found`,违反跨 scope 同一常量 404 的约定。在 distributions 入口转换被复用 Run helper 的 404,保持原 Run API 错误体不变;服务再次查询资源失效时同样使用报告常量。
- 初版无 workspace 非 system 身份仍先 SELECT 资源再返回 404,与 delta spec 的查询前拒绝不符。参数有效后增加显式拒绝,保留双方提供/均未提供的 400 优先级与非法 UUID 的 422。
- 原测试只接受每 scope 各自常量,没有检查统一完整响应体;无 workspace 测试也未检查查询是否发生。补充后在修正生产代码前实际复现 **7 failed, 8 passed**:5 项揭示错误体不一致,2 项揭示仍执行资源查询。
- 成功路径改用非零 workspace,覆盖 viewer/editor/admin。system 测试对同一 scope 加入异 workspace 分数,验证只读取资源实际归属的分数,并比较成功前后持久状态。
- 增加 system 的缺失/软删除资源、服务再次查询失效、无角色不进入聚合等回归。不可见资源覆盖 viewer/editor/无角色,检查 Run/Task 全部使用同一无标识错误体和数据库状态不变。
- role=None 的测试身份不再插入会被 ORM 默认值转换成 viewer 的成员行。快照复用同一 helper,直接读取四张表的全部持久列。
- 修正文档中的事实错误:原服务在 `workspace_id=None` 下对正常指定资源返回 404,并非空直方图 200;viewer 是合法读角色,新增门槛拒绝的是无角色身份。其余报告入口仍在本切片范围之外。

## 实际验证结果

命令在该 worktree 中运行,使用本地 `.venv/Scripts/python.exe`。

| 检查 | 命令 | 结果 |
|---|---|---|
| 分布边界、既有报告 API、reconciliation、Run 读边界回归 | `.venv/Scripts/python.exe scripts/dev.py test tests/test_api/test_evaluation_reports_distribution_boundary.py tests/test_api/test_evaluation_reports_api.py tests/test_services/test_evaluation/test_reports_reconciliation.py tests/test_api/test_evaluation_run_access_boundary.py -q -rs --tb=short` | **72 passed, 1 warning**, 38.88s,无跳过 |
| 共享检查 | `.venv/Scripts/python.exe scripts/dev.py check all` | ruff check 通过;**1622 files** format 检查通过;mypy **962 source files** 无错误 |
| OpenSpec | `openspec validate step10-evaluation-report-distribution-access-boundary --strict` | **valid** |
| 功能清单增量门禁 | `.venv/Scripts/python.exe scripts/feature_inventory.py check --base-ref c7944bad` | **413 entries, 0 errors, 189 warnings**;新增/变更条目 **0** |
| 差异检查 | `git diff --check` | 通过 |

测试告警为 Run comparison 既有 HTTP 422 常量的弃用提示。功能清单告警为未变更条目的历史治理/evidence/acceptance 欠项,增量准入通过;未修改清单或治理脚本。

## 范围与限制

- 代码修改限于 `src/hecate/ops/api/evaluation_reports.py`;测试新增于独立文件。本次不修改 `evaluation.py`、报告服务算法/签名、Dataset/item、Task 控制命令、前端、Runtime、共享 skill 或其他报告入口函数。
- 已回归分箱、错误分数排除与人工覆盖 reconciliation,未改变这些算法。
- 使用仓库 in-memory SQLite 夹具;未重跑整个 `tests/test_api`、完整 `tests/`、PostgreSQL 集成或 CI 平台矩阵,完整测试交由 CI 验证。
- 改动未提交、未推送、未归档。
