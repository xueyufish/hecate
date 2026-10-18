# 验证记录 — step14-run-action-query

本文件记录复核修正后的实际结果，替代初版超出测试证据的表述。

## 新增契约验证

命令：

    .venv/Scripts/python.exe -m pytest tests/test_execution/test_run_action_query.py tests/test_api/test_run_actions_api.py -q -W error

结果：24 passed，约 8 秒，无告警、失败或跳过。

- 服务层 13 项：真实 SQL 动作状态、多 Run 隔离、历史引用与 engine-session 诱饵隔离、5 类不可见资源、字段白名单、真实空结果/不支持区分、2 类错误脱敏、畸形读取故障、完整零副作用验证。
- HTTP 层 11 项：原服务依赖下的 SQL 200 与历史隔离、5 类 404、缺少 workspace 的 403、真实 SQL 空结果与实际 InMemory Stub 区分、2 类脱敏 500、非法 ID 422。
- SQL 台账均使用临时文件；平台记录使用项目共享 db_session，不另建 ORM engine。

## 关键验收对照

| 验收点 | 实际证据 |
|---|---|
| Run 与父 Task 共同隔离 | 查询前验证两者存在、未删除且 workspace 一致；拒绝路径断言台账读取零调用，HTTP 统一 NOT_FOUND |
| 历史 Run 不混入新尝试或 engine session | 同 Task 两个 Run 各有动作；backend_ref ID 与平台 ID 不同，并记录 engine-session 诱饵，查询只返回平台指定 Run |
| 状态来自真实台账 | 真实 intent、claim_ex 与带 token 的 outcome；成功/失败/unknown/未决领取分别断言，Task 持久生命周期为 succeeded 也不覆盖动作 |
| 白名单 | 服务和 HTTP 精确比较键集合；结果内容、session、claim token、内部字段不进入 JSON |
| 故障明确且不泄漏 | 驱动异常和应用异常均返回固定 500；响应与 caplog 均不含测试密码、SQL 参数和密钥；日志以 Run ID/异常类型关联 |
| 真实空列表与不支持 | SQL 无动作返回 supported=true；实际 Stub 缺能力返回 supported=false，来源如实标注 |
| 零副作用 | 查询成功、跨域拒绝、读取故障前后，全部 durable 表/outbox、Task/Run ORM 全字段一致；已有运行中生命周期、未决动作、已完成结果及过期 requested 命令不改变；SQL 只观察到 SELECT 和 BEGIN |

## 受影响回归

命令：

    .venv/Scripts/python.exe -m pytest tests/test_execution/test_run_action_query.py tests/test_api/test_run_actions_api.py tests/test_execution/test_task_control_service.py tests/test_channel/test_task_control_api.py tests/test_execution/test_task_run_registry.py tests/test_layering_domain.py -q

结果：89 passed，约 92 秒，无失败或跳过。

该组合报告 5 条告警：
- 既有 test_task_control_api.py 导入 test_session_factory 导致 fixture 收集提示；
- 三个既有控制测试把 _spawn_background 替换为 held.append，保留的协程未关闭，产生 coroutine never awaited 提示；已读取对应测试确认该替身写法，本 change 未修改这些控制测试或后台派发实现；
- 既有 API 分支使用 FastAPI 422 旧名称的弃用提示。

新增两个文件独立以 -W error 运行全部通过，不将组合告警隐去，也不把它们计为失败或额外通过项。

## 共享检查

    .venv/Scripts/python.exe scripts/dev.py check all

- ruff check：All checks passed。
- ruff format --check：1591 files already formatted。
- mypy：943 source files，无错误。

OpenSpec strict validation 和 git diff --check 均通过。

## 复现证据

原实现下新增回归曾得到 3 failed：
- 父 Task 已删除仍返回动作；
- 父 Task workspace 与 Run 不一致仍返回动作；
- 存储异常中的密码/SQL 参数进入错误消息。

修正后这些场景分别包含在服务与 HTTP 的正式套件，不把仅修改断言或原已通过的基础测试充作漏洞修复证据。

## 未验证与交付边界

本轮没有执行 PostgreSQL 专项或性能压测，不宣称从其他套件继承本 API 的认证。未新增 schema，不涉及并发写入语义。列表协议仍未分页。

查询仅针对当前绑定平台账本，不拉取远端宿主动作；不触发执行、重试、对账、命令惰性过期或生命周期迁移。未提交、推送、创建 PR、归档；CI 与 PR review 留待后续正常流程。
