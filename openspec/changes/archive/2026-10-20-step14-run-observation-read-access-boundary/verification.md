# Verification

## 审查结论与基点

基点：`origin/main` 与 HEAD 均为 `d061446f`。分支：`feat/step14-run-observation-read-access-boundary`。

未发现业务实现中的权限绕过或 SSE 授权时序错误。四个 REST 入口先校验 workspace、Run 与父 Task 可见性，再要求 VIEWER；SSE 在创建响应前完成授权；内部事件分页先校验可见性再 pump。审查将 `_run_read_gate` 的 service 参数从 `Any` 收紧为既有 `TaskControlService`，补齐安全回归并纠正文档事实。没有新增权限构件或修改可信内部调用的角色契约。

改动限定在两个生产文件、新增测试和本 change artifacts；未修改主规范、registry、共享权限助手、评测模块、前端或 Runtime。未执行 commit、push、archive。

## 实际验证结果

| 检查 | 命令 | 结果 |
|---|---|---|
| GLM 原测试基线 | `.venv/Scripts/python.exe -m pytest tests/test_api/test_run_observation_access_boundary.py -q --timeout=30` | 修改前 9 passed |
| 最终权限专项 | 同上 | 65 passed，无跳过 |
| 关联回归 | 下列完整命令 | 227 passed，无跳过，7 warnings |
| 共享检查 | `.venv/Scripts/python.exe scripts/dev.py check all` | ruff check/format 通过，1626 files formatted；mypy 962 source files 无问题 |
| OpenSpec | `openspec validate step14-run-observation-read-access-boundary --strict` | valid |
| 功能清单 | `.venv/Scripts/python.exe scripts/feature_inventory.py check` | 413 entries，0 errors，189 warnings；未修改 feature YAML |
| Diff | `git diff --check` | 通过 |

关联回归实际命令：

```text
.venv/Scripts/python.exe -m pytest tests/test_api/test_run_observation_access_boundary.py tests/test_api/test_run_actions_api.py tests/test_api/test_task_commands_query.py tests/test_api/test_task_command_access_boundary.py tests/test_channel/test_task_control_api.py tests/test_channel/test_workflow_callback_api.py tests/test_execution/test_task_control_service.py tests/test_execution/test_run_action_query.py tests/test_execution/test_event_relay_projection.py tests/test_execution/test_event_position_fencing.py tests/test_execution/test_entry_events.py tests/test_execution/test_command_list_query.py tests/test_layering_domain.py -q -ra --timeout=30
```

## 审查修正与覆盖映射

- 原跨 workspace 用例同时移动 Run 并切换身份，实际主要命中父 Task 不匹配。现在分别验证外部 workspace 身份读取原有 Run、Run 自身归属变化及父 Task 归属变化。
- `test_denial_precedes_every_read_and_leaves_facts_unchanged`：9 种拒绝条件 × 4 个入口，共 36 项。包含 foreign identity/foreign Run/missing Run/deleted Run/deleted Task/foreign Task/no role/no workspace/system；不可见资源用无角色身份测试，证明 404 可见性判定先于角色拒绝。完整 404 envelope 只回显请求 Run ID，伪造 query role 不能提权。
- 上述每项都在已有 Task、Run、平台事件、durable 状态、动作/结果、过期但仍 REQUESTED 的命令回执及 outbox 事件上取全字段 SQL 快照。拒绝前后数据相同；relay、pump、动作查询、事件读取、命令访问及 dispatch 观察计数均为零。SSE 拒绝为 JSON 403/404，无 event-stream 响应头和错误事件帧。
- `test_authorized_roles_preserve_real_content_and_cursor_resume`：VIEWER/EDITOR/ADMIN 共 3 项，与 ADMIN 基线逐响应比较，检查实际成功动作及白名单、敏感结果/session 不外泄、事件分页游标与 SSE 续读到终态、终态之后空流。成功读的 relay 使用计数替身；生产 relay 另由关联回归覆盖。
- `test_service_visibility_precedes_relay_and_event_reads`：6 种不可见条件 × page/stream × platform/entry 两路径，共 24 项。直接调用真实 service 并实际迭代 async generator，证明服务层的不可见资源不会先 pump 或读取事件。
- `test_entry_store_events_preserve_safe_payload_and_stream_resume`：使用现有 InMemoryEventStore 与真实 RunEventMapper，验证 entry 分支的事件/终态、游标恢复及 backend extra 清除；空 extra 的序列化按既有省略字段契约断言。
- `test_established_stream_keeps_runtime_error_frame`：通过授权后的运行时失败仍返回既有 SSE error 帧，不泄露诊断原文。它与建流前授权失败的普通 HTTP 错误分开验证。
- 新测试 fixture 使用 monkeypatch 恢复 `_worker_state` 原值，避免 teardown 固定清空全局状态。HTTPX ASGITransport 会缓冲完整响应，测试预置终态并读取实际响应内容，不再把 `client.stream` 的上下文入口误当成仅检查响应头。
- proposal/design 纠正两点：只有 `read_run_events` 直接 pump，stream 本身没有直接 pump；同 workspace 无角色由可读改成 403，缺 workspace/system 则保持 NO_WORKSPACE。

## 范围与限制

- 全仓 pytest、真实 PostgreSQL、跨进程恢复与实际网络中断故障未执行。平台 ORM 使用共享 SQLite fixture，durable 组件使用独立文件 SQLite，entry 成功路径使用既有内存事件存储。
- SSE 用例验证实际事件帧及携游标的新请求；不宣称已验证真实网络中途断连。长连接动态撤权不在本 change 范围。
- AuthContext 经 dependency override 切换请求身份；membership 到上下文的生产解析仍由既有认证层负责。
- 关联回归的 7 warnings 为：2 项共享 session factory 收集提示、2 项既有 HTTP 422 常量弃用提示、3 项 coroutine 未 await。后三项来自既有 `test_task_control_service.py` 将 `_spawn_background` 替换成 `held.append` 后未关闭所持 coroutine。已逐函数核对这三个测试及 `_spawn_background`、`submit`、`_dispatch_task` 的 AST 与 `origin/main` 一致；没有屏蔽 warning 或修改无关测试。
- 其余 Task/command/reconciliation 读取角色门槛、task 级 pump 次序保持 proposal 登记的独立后续范围。
