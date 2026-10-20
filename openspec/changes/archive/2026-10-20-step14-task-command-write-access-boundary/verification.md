# Verification

## 审查结论与基点

生产权限门符合契约，无需修改业务实现。审查修正测试中的角色误用、幂等自比较和副作用漏验，并补充审批锚点。

最初基点为 `origin/main` `4f97d289`。审查期间外部进程将分支 rebase 到 `origin/main` `d2e91a23`，归档 change、同步 `openspec/specs/platform-task-control/spec.md` 并创建提交 `3b0fee0e`；该提交已包含审查补强测试的主体。用户确认 GLM 停止后，继续补充一项 REQUESTED 降权回放参数和更新验证记录。本次审查没有执行 commit、push 或 archive。

生产变更仅为 `tasks.py` 的 REST 权限门和 `TaskControlService.assert_task_visible`。`issue_command` 签名和内部 workflow callback 路径保持兼容。

## 实际验证

| 检查 | 实际命令/方式 | 结果 |
|---|---|---|
| 最终权限专项 | `.venv/Scripts/python.exe -m pytest tests/test_api/test_task_command_access_boundary.py -q` | 47 passed |
| 扩展关联回归 | 下列完整 pytest 命令 | 206 passed, 3 skipped, 7 warnings；运行时专项为 46 项，最终新增的 REQUESTED 参数另经上述 47 项专项验证 |
| 共享检查 | `.venv/Scripts/python.exe scripts/dev.py check all`，外部 rebase 后再次执行 | ruff check、format 全通过；1623 files formatted；mypy 962 source files 无问题 |
| 已归档 delta 严格校验 | 将当前归档目录复制到临时隔离 OpenSpec root，在其中执行 `openspec validate step14-task-command-write-access-boundary --strict` | valid；临时目录自动清理，不改变归档状态 |
| 同步主规范 | `openspec validate platform-task-control --type spec --strict` | valid，只有长需求文本提示 |
| 归档任务校验 | `openspec validate --archived --strict --json`，单独检查当前归档结果 | 当前归档的任务完成校验通过；全量历史归档结果单独记录，不视为全仓通过 |
| 特性登记 | `.venv/Scripts/python.exe scripts/feature_inventory.py check` | 413 entries, 0 errors, 189 warnings；本 change 未修改 feature YAML |
| Diff | `git diff --check` | 通过 |

关联回归实际命令：

```text
.venv/Scripts/python.exe -m pytest tests/test_api/test_task_command_access_boundary.py tests/test_api/test_task_commands_query.py tests/test_channel/test_task_control_api.py tests/test_channel/test_workflow_callback_api.py tests/test_execution/test_task_control_service.py tests/test_execution/test_task_run_registry.py tests/test_execution/test_command_list_query.py tests/test_execution/test_approval_wait_park.py tests/test_execution/test_workflow_approval_combo.py tests/test_execution/test_workflow_subtask_contract.py tests/test_execution/test_workflow_child_adapter.py tests/test_layering_domain.py -q -ra
```

## 审查修正与场景映射

- **真实角色矩阵**：原 `test_editor_and_admin_issue_commands` 仅使用 ADMIN。现在参数化 EDITOR/ADMIN × commands/cancel，分别验证 201/200、REQUESTED 回执和服务端 issuer。
- **幂等比较**：原断言比较同一次状态自身；现在对第二次请求前后独立快照比较，并要求只有一份已结算回执。
- **同一用户降权**：原用例切换到另一个随机用户，且只测未结算命令。现在用同一 user_id 的 EDITOR → VIEWER，覆盖 REQUESTED cancel 和 REJECTED pause 两条回放路径；拒绝后既有回执逐字段相等，recorder 调用计数不变。
- **权限门先于所有命令种类**：VIEWER/role=None × cancel/pause/resume/provide_input，伪造请求体 `role=admin` 仍 403；recorder 与后端 dispatch 无调用。
- **可见性先于角色**：foreign/deleted × EDITOR/VIEWER/role=None × 两入口，均 404；只归一化调用方自带 UUID 后，完整 error envelope 与 missing 一致，recorder 无访问。
- **拒绝零副作用**：两入口 × foreign/missing/deleted/viewer/no_role/no_workspace/system。在有实际 Task、Run、durable 状态、已有回执和 outbox 事件的环境下，比较拒绝前后 Task/Run 全字段及全部 durable 表（包含 command/event log/outbox cursor/receipt）的 SQL 快照；同时检查 recorder get/record/transition 和 backend dispatch 计数不变。既有回执 ID 同时作为拒绝请求的 ID，防止借旧记录绕过权限门。
- **身份范围保持**：workspace-less JWT 与 system key 均维持 403 `NO_WORKSPACE`，不扩展跨 workspace 能力。
- **既有业务约束**：真正 EDITOR 身份仍受 revision 冲突和期限校验；持有效 wake token、自报 approved=true 但缺审批权威事实时，resume 回执 REJECTED，WAITING_APPROVAL 状态与 token 不变、无 dispatch。更完整的 pending/revoked/approval binding 与 workflow 内部调用由关联回归承载。

## 复核复跑

推送后独立复跑扩展套件:206 passed, 3 skipped;其中 `test_approval_wait_park.py::test_crash_window_waiting_approval_without_contract_fails_closed` 首次执行失败,连续两次单独复跑通过——判定为崩溃窗口计时偶发,与权限门无关(该测试走 execution 层等待/审批路径,不经过 REST `_issue`)。未修改该测试。

## 验证限制与警告调查

- 全仓 pytest 和真实 PostgreSQL 端到端未运行。平台 ORM 使用共享测试 SQLite fixture；durable 组件为独立的文件 SQLite store。
- 3 项 skip 均来自 `test_workflow_child_adapter.py`，原因是未配置 `HECATE_STEP6_POSTGRES_URL`，其双连接工具账本路径依赖 PostgreSQL 多 writer 锁语义。skip 不计作通过。
- 7 项 warnings 包括 2 项共享 session factory 收集提示、2 项既有 HTTP 422 常量弃用提示、3 项 coroutine 未 await。后三项来自 `test_task_control_service.py` 将 `_spawn_background` 替换为 `held.append` 后未关闭暂存 coroutine。已核对这三个测试及 `_spawn_background`、`submit`、`_dispatch_task` 的 AST 与 `origin/main` 完全一致；未将其归因于本次权限门，也未屏蔽警告或修改无关测试。
- 全量历史归档任务校验发现其他归档存在未完成项；当前 change 的结果单独验收，不扩大范围修改历史归档。
- 降权以请求级 AuthContext dependency override 模拟，验证相同用户、请求时角色生效；实际 membership 变更到 AuthContext 的解析由既有认证层负责。
- workflow-callback 协议信任模型、Task 提交权限、读取端点的角色门槛仍为 proposal 登记的相邻独立切片，不在本 change 内。
