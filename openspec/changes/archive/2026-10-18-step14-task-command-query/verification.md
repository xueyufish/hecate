# 验证记录 — step14-task-command-query

## 本次审查修正

- 原服务校验 Task 可见后仍用固定 hecate issuer 查询，可能返回同 id 的另一 issuer 的记录。现从可见 Task 行的 issuer_domain 与 id 构造完整引用；回归测试实际复现错误记录被选中。
- 原字段投影在路由 try/except 之外，投影失败会绕过约定的固定错误响应。现由服务在同一异常保护内完成读取及九字段投影，路由直接返回结果；既有单条读取复用同一个 command_receipt_summary，响应字段及懒过期行为不变。
- 原 SQL/InMemory recorder 没有查询参数边界；负 limit/offset 等输入在两种实现中可能产生不同结果。现两者均拒绝非 Task kind、limit 不在 1–100 或 offset 为负。
- 原平台单元测试会根据环境变量访问 PostgreSQL，并 drop/create 整个 durable 表集。存储契约套件现移至 packages/hecate-durable/tests/test_task_command_query.py，复用现有 decision_database：SQLite 使用临时文件，PostgreSQL 每个测试使用独立临时 schema，仅清理该 schema。
- 修正服务可见性测试注入错对象的问题：现在注入实际 recorder，拒绝路径断言零查询。
- 为过期 requested 测试补上真实 expires_at；SQL 观察同时验证 COUNT、WHERE 范围及 ORDER BY / LIMIT / OFFSET，不能只凭语句都是 SELECT 就声称已验证 SQL 下推。

## 本次审查实际验证

| 检查 | 结果 |
|---|---|
| 修复前回归 | 12 failed、28 passed，复现 issuer、投影错误及双实现输入边界问题 |
| 查询专项（两个平台文件 + durable 查询文件，InMemory/SQLite） | 40 passed，0 warnings |
| 受影响完整回归 | 265 passed、6 skipped、6 warnings |
| 查询存储三实现复核（InMemory / SQLite / 真实 PostgreSQL） | 36 passed，0 warnings |
| scripts/dev.py check all | exit 0；ruff check 通过，1608 文件格式通过，mypy 检查 955 个源文件无错误 |
| openspec validate step14-task-command-query --strict | valid |
| git diff --check | 通过 |

受影响回归命令：

```text
.venv/Scripts/python.exe scripts/dev.py test
  tests/test_execution/test_command_list_query.py
  tests/test_api/test_task_commands_query.py
  tests/test_channel/test_task_control_api.py
  tests/test_execution/test_durable_contract.py
  tests/test_execution/test_run_action_query.py
  tests/test_api/test_run_actions_api.py
  tests/test_execution/test_task_control_service.py
  tests/test_execution/test_managed_host_channel.py
  packages/hecate-durable/tests
  -q --tb=short
```

该轮显式不配置 DURABLE_TEST_POSTGRES_URL，6 个跳过来自已有 accepted-decision PostgreSQL 专用测试（含参数化用例）及重排提交测试的 SQLite 分支；不把跳过计为通过。

另单独配置现有 hecate-step6-pg 测试容器（127.0.0.1:55432）的连接，执行：

```text
.venv/Scripts/python.exe -m pytest
  packages/hecate-durable/tests/test_task_command_query.py -q --tb=short
```

此轮包含 12 个 PostgreSQL 查询用例，连同 24 个 InMemory/SQLite 用例共 36 passed。该轮只运行隔离后的查询文件，没有运行旧的会重置共享表的 package store fixture。

## Warning 调查

本 change 的 40 个专项测试和 36 个三实现测试均无 warning。广义回归的 6 个 warning 来源已逐项查看：

- 2 个 PytestCollectionWarning：旧 test_task_control_api.py / test_managed_host_channel.py 将 test_session_factory 作为模块顶层名字导入，pytest 尝试按测试收集。
- 1 个 DeprecationWarning：既有 payload-schema 拒绝路径使用 HTTP_422_UNPROCESSABLE_ENTITY。
- 3 个 RuntimeWarning：旧 test_task_control_service.py 的场景将 _spawn_background 替换为 held.append，保留协程但未关闭；原 _dispatch_task 调用路径未在本 change 修改。

已确认上述测试文件与 HEAD 一致；本 change 未抑制 warning 或修改相关执行路径。

## 范围与保留限制

- 修改现有 recorder ABC、SQL/InMemory 实现、TaskControlService、tasks 路由，以及三个独立测试文件和本 change 工件。
- 没有模型、迁移、前端、Runtime 动作派发或批处理改动。
- 列表返回已记录状态：过期 requested 不因列表读取变为 expired；既有单条查询仍执行懒过期。
- 排序沿用持久化 issued_at 字符串和 command_id 的倒序，不改变历史数据或迁移时间字段类型。
- 此次 PostgreSQL 复核覆盖新查询契约，没有重新执行旧 durable 包的全部 PG 故障测试；完整 CI/PR 检查尚未执行。
- 所有修改留在用户指定 worktree，未提交、未推送。
