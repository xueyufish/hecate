# Verification

## 基点与范围

复核前重新 fetch `origin/main`,仍为 `a8b1aeed2f95abbf18f74c8b9b31287bdee24e43`(#302)。分支为 `feat/step14-task-observation-read-access-boundary`,实际 worktree 为 `D:/Workspace/Github/xueyufish/.worktrees/step14-task-observation-read-access-boundary`(路径中没有额外的 `feat/` 目录)。

生产 diff 仅新增 `src/hecate/channel/api/tasks.py` 的 `_task_read_gate`,并接入 `list_tasks`、`get_task`、`list_task_runs`。AST 比较确认其他生产定义不变。service/registry、共享认证、Runtime、前端、评测模块、共享 skill 和既有测试均未修改。

## 复核发现及修正

1. 原 authorized 测试在设置 viewer/editor 后调用 `_baseline`,而该函数内部强制设置 admin,使三个角色用例实际都验证 admin。修正为只读取当前身份的 `_responses`,使用同一 user 的 `dataclasses.replace` 切换角色,并确认读取完成后选中身份仍未改变。
2. 原夹具只有无 durable 状态的 Task 与空 Run 列表,无法证明等待信息、有效状态过滤或 Run 排序。补充真实 Agent/version/principal/deployment、waiting_input/queued/unrecorded Task、等待信息、多次 Run、软删除和跨 workspace 数据。Run 创建时间与 attempt 顺序相反,直接断言 live attempts 1/3 顺序及投影/游标;另保留合法空 Run 列表。
3. 原零副作用测试比较部分 Task 字段和均为 None 的 durable 状态,并非有数据的完整快照。现在种入持久状态、输入、submission/outbox、命令回执、action intent/outcome 和平台事件,直接 SQL 比较 Task/Run/平台事件与所有 durable 表的全列快照;拒绝路径同时观察 service 业务查询、durable 访问、relay 与派发,要求调用列表为空。monkeypatch 自动恢复全局 worker state 与原方法。
4. 补全 detail/runs 的 foreign/missing/deleted × role=None/VIEWER 矩阵,断言完整 404 envelope;三入口覆盖 roleless、缺 workspace、system key 及伪造 query role。缺 workspace 另钉死资源查找前拒绝。
5. 修正文档中 async 伪代码、混写的兼容性表和无代码依据的“task 级 pump 次序”相邻缺口。三个 Task 观察方法原本没有 pump;本 change 不增加 pump。

复核未发现生产权限门绕过:workspace 必需 → 资源可见性(detail/runs) → VIEWER → 既有业务读取。生产实现保留 GLM 原样。

## 实际验证

全部 Python 命令使用本 worktree 的 `.venv/Scripts/python.exe`。

| 检查 | 命令 | 复核实际结果 |
|---|---|---|
| GLM 原始专项 | `python -m pytest tests/test_api/test_task_observation_access_boundary.py -q --timeout=30` | 原文件 9 passed,8.72s;不能据此证明 viewer/editor 被执行 |
| 强化专项 | 同上 | 28 passed,36.63s |
| 关联回归 | 下方完整命令 | 177 passed,0 skipped,5 warnings,109.66s(含专项 28 项) |
| 共享检查 | `python scripts/dev.py check all` | ruff check、format 全部通过(1629 files);mypy 962 source files,0 issues |
| OpenSpec | `openspec validate step14-task-observation-read-access-boundary --strict` | valid |
| 功能清单 | `python scripts/feature_inventory.py check` | 413 entries,0 errors,189 governance warnings;清单及配置与基点一致 |
| Diff | `git diff --check` | 通过 |

```text
python -m pytest tests/test_api/test_task_observation_access_boundary.py tests/test_api/test_task_commands_query.py tests/test_api/test_task_command_access_boundary.py tests/test_api/test_run_observation_access_boundary.py tests/test_channel/test_task_control_api.py tests/test_execution/test_task_control_service.py -q -ra --timeout=30
```

### 回归警告核对

5 warnings 分别为:一条导入 `test_session_factory` 的 collection warning、一条原 `_issue` 路径使用 `HTTP_422_UNPROCESSABLE_ENTITY` 的弃用警告、三条 `test_task_control_service.py` 中持有协程但未关闭的 RuntimeWarning。后者来自 `test_background_dispatch_via_worker_reconciles`、`test_cancel_queued_applies_without_execution`、`test_expired_command_converges_on_read`:以 `held.append` 替换 `_spawn_background` 后保留了 `_dispatch_task` 协程。AST 比较确认三个测试与 `origin/main` 相同;生产 AST 比较确认 `_issue`、`_dispatch_task` 等既有逻辑未被本 change 改动。未屏蔽警告或修改无关代码。

## Delta 场景映射

- 跨 workspace、软删除/不存在、可见性优先: `test_invisible_tasks_reject_before_role_and_business_reads`,16 cases(detail/runs × foreign_identity/foreign_task/missing/deleted × None/VIEWER),含完整 404、query role 伪造、零调用及全列快照。
- 无角色/缺 workspace/system、拒绝不读: `test_forbidden_requests_never_reach_business_or_durable_reads`,9 cases(三入口 × 三身份),含 NO_WORKSPACE/FORBIDDEN 区分、资源查询前拒绝和零副作用。
- viewer 可读及既有成功语义: `test_authorized_roles_preserve_populated_responses_and_facts`,3 roles,每个角色逐响应比较 admin 基线,实际请求列表/分页/queued 与 waiting_input 过滤/unrecorded/等待详情/Run 列表/空 Run 列表及未记录详情;成功读取也不改变已填充事实。

## 范围与限制

- 未跑全仓 pytest、真实 PostgreSQL 或网络/跨进程故障场景。平台 ORM 使用共享 db_session 的内存 SQLite,durable 组件使用独立临时 SQLite(沿用同族测试模式)。
- AuthContext 通过依赖替换切换,不代表认证 provider 或真实 membership 撤销测试。
- `lifecycle_state=unrecorded` 现有实现未筛除有记录 Task,返回与未过滤列表相同。本次按 `origin/main` 既有查询语义比较,没有把它改为“仅无记录 Task”;该历史过滤行为不由本次授权切片修正。
- command receipt/reconciliation 读角色门、Task 提交、workflow callback、SSE 动态撤权均不在本切片;不据此宣称所有 Task 控制入口授权完成。
- 未提交、推送、归档或同步主规范。
