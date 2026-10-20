# Verification Record

基点: `b3d9ab3a`。工作树: `D:/Workspace/Github/xueyufish/.worktrees/step10-evaluation-dataset-version-write-access-boundary`。本记录为复审后的实际执行结果,替代初版验证记录。

## 修正与回归证据

- 删除/checkout 原先在版本可见性检查之前要求 editor,导致 viewer/无角色请求缺失、已删除、异 Dataset 或异 workspace 版本时返回 403。改为父 Dataset → 版本 workspace/Dataset 绑定 → editor,不可见版本统一无标识 404。
- system 原先无 workspace 过滤地预查版本,随后以 `ds.workspace_id` 执行写入;历史零 UUID/异 workspace 版本会通过预检查,在服务中抛出未处理异常。改为始终按父 Dataset 的 workspace 查版本,归属不一致返回 404,不修改历史记录。
- 删除/checkout 补齐服务再次查询的 `DatasetVersionNotFoundError` 映射,预检查后版本失效也返回常量 404。
- 修复测试种子 item 的 workspace 归属,扩展 viewer/无角色、不可见父级、归属不一致历史版本及再次查询失效场景。成功路径增加真实归属、checkout live 内容、冻结内容/hash 不变性、软删除名称保留断言。
- 修正生产代码前,新增回归实际结果为 **4 failed, 18 passed**: viewer/无角色的版本检查顺序各失败一项,system 零 UUID/异 workspace 历史版本各失败一项。

## 实际检查结果

以下命令均在该 worktree 中执行,Python 使用其 `.venv/Scripts/python.exe`。

| 检查 | 命令 | 结果 |
|---|---|---|
| 写边界回归 | `.venv/Scripts/python.exe scripts/dev.py test tests/test_api/test_evaluation_dataset_version_write_boundary.py -q --tb=short` | **25 passed** |
| API 与版本服务回归 | `.venv/Scripts/python.exe scripts/dev.py test tests/test_api tests/test_services/test_evaluation/test_publish_gate_versions.py tests/test_services/test_evaluation/test_dataset_service.py -q --tb=short` | **882 passed, 8 skipped, 18 warnings** (328.62s) |
| 共享检查 | `.venv/Scripts/python.exe scripts/dev.py check all` | ruff check 通过;format 检查 **1619 files** 通过;mypy **960 source files** 无错误 |
| OpenSpec | `openspec validate step10-evaluation-dataset-version-write-access-boundary --strict` | **valid** |
| 功能清单增量门禁 | `.venv/Scripts/python.exe scripts/feature_inventory.py check --base-ref b3d9ab3a` | **413 entries, 0 errors, 189 warnings**;新增/变更条目 **0** |
| 差异检查 | `git diff --check` | 通过 |

8 个跳过项来自已有测试声明: `test_schedule_api.py` 的 4 项要求 PostgreSQL advisory locks;`test_tool_decisions_api.py` 的 4 项声明 PostgreSQL singleton 依赖。18 个告警为 HTTP 422 常量与第三方 datetime API 的弃用提示,不影响测试结果。

功能清单曾运行不带基点的 `check --strict`,因历史治理字段/evidence/acceptance 缺项返回失败。已核对 checker、inventory、catalog、roadmap 与两份映射文件均与基点内容一致,并查明 CI 对已有基点使用增量门禁。采用同一增量模式验证通过,历史告警保留,未扩展本 change 修复范围。

## 覆盖范围与限制

- 覆盖创建、删除与 checkout 的 editor/admin 成功、viewer/无角色拒绝、跨 workspace、无 active workspace、缺失/已删除父级及版本、异 Dataset 绑定、system 跨 workspace 与真实归属、历史不一致版本拒绝、错误映射和拒绝路径数据库状态不变。
- 不修改版本服务算法、普通 Dataset 写接口、读接口、前端、Runtime 或数据库 schema。历史归属不一致版本不自动迁移。
- 测试使用仓库既有 in-memory SQLite 夹具;未运行 PostgreSQL 集成、完整 `tests/` 或 CI 平台矩阵,交由 CI 验证。尚未提交、推送或归档。
