# Step5 执行链复核与修正

## 结论与范围

本轮以 `main` 的 `5619cce` 为基线，复核演进方案 Step5、已合并的 #206—#212、独立包、入口迁移与上一轮报告。**Step5 不能整体声明完成**：独立包和只读预览有实现，但宿主没有复用方案要求的完整共享执行应用服务，HTTP 表面也未符合完整执行后端绑定；入口迁移仍有已登记尾项。本轮修复已实现路径中的错误，不提前引入 Step6 的持久任务生命周期。

变更由 [step5-execution-review](../../openspec/changes/step5-execution-review/proposal.md) 承接。既有报告的测试记录属于各自基线；本报告的验证记录以本轮修改为准。

> 2026-10-05 更新：change `runner-contract-shared-service` 已补齐当时指出的两项 Step5c 缺口——平台 builtin 与 Runner 共用 `hecate_runtime.execution_service`，Runner HTTP 面正式映射 Step3 请求/回执/事件/能力声明，并在 durable profile 中提供重启可查询的执行事件游标。本报告的历史结论保留，不再代表当前 Step5c 状态。


## 发现、修复与回归

| 问题 | 原行为与影响 | 本轮修改 | 验证位置 |
|---|---|---|---|
| runner 查询和取消缺少身份隔离 | 匿名可读 Run 状态、事件、产物；其他 principal 可取消；证据查询可读所有 principal | 除健康/能力探针外校验身份；Run 按原 principal 和数据域授权，证据查询固定当前 principal。匿名拒绝记录由部署操作员读取本地文件 | `packages/hecate-runner/tests/test_server.py` |
| 参数与启动能力校验不足 | 只验证参数是否 dict，字段错误仍派发；不支持的 backend、版本、工具映射等启动成功 | 分发前校验内置参数和 manifest 中经摘要验证的 JSON Schema；不支持的后端/版本/必需能力/工具映射启动拒绝；空 secret、重复工具、非 loopback 绑定拒绝 | `test_server.py`、`test_profile.py` |
| endpoint 配置未实际调用 | 所有运行仍走 Stub，却在证据中标为 endpoint | 按配置 URL 调用 JSON 模型端点；记录来源，失败不回退 Stub。固定工具计划保持技术预览边界 | `packages/hecate-runner/tests/test_engine.py` |
| 本地审计未真正实现准入失败关闭 | 执行结束才写证据，存储不可写时仍先调用业务 API；查询取同日较早记录 | 接受 Run、派发工具前持久写入；工具回执与执行结束分别记录；线程内串行写入并 flush/fsync；查询按时间文件和同日记录逆序 | `test_engine.py` |
| 串行准入存在竞争 | submit 只查锁，执行任务启动后才加锁，同一时刻多个请求会排队 | submit 内保留执行槽位，后续提交明确拒绝；写入失败释放槽位 | `test_engine.py` |
| 取消和关闭是假回执 | 取消只响应 requested，未记录/执行；关闭只停止事件循环；CLI 默认等待关闭超时后退出 | 记录取消请求并在模型/工具边界阻止后续派发；不能撤回已发生请求；关闭停止准入、清理任务并记录 unknown；CLI 等待显式关闭 | `test_engine.py`、`test_server.py` |
| 入口错误地把 Agent ID 当 Principal ID | 正常注册时两个 ID 不同，Run 登记失败；旧测试强制相同掩盖问题 | 按 agent/workspace 查询真实 Principal；回归用独立生成的 Principal ID | `tests/test_execution/test_entry_service.py` |
| 关联失败会污染事务或留下部分记录 | flush 失败使后续执行/提交不可用，客户端 reason 暴露数据库错误；session 来源可不一致 | savepoint 隔离 Task/Run 和会话关联；返回固定安全原因；统一实际 session，冲突在执行前拒绝 | `test_entry_service.py` |
| 评估 workflow 无法实际执行或不能追溯 | 调用不存在的 `make_runtime_port`；未传 workspace 和固定版本，未使用共享存储 | 使用真实 factory、工具/guardrail 装配、共享事件和会话存储；传 workspace/固定版本；经 registry 校验内部 Task 同域。Agent 评估路径的同名工厂错误也修复 | `test_entry_service.py`、评估 engine 回归 |
| 共享工具查询跨 workspace | 按全局名称查定义和执行，同名工具混淆、可命中其他 workspace 的工具 | HTTP/MCP/IM/A2A/定时 agent 的共享装配显式传 workspace，定义和执行查询均限定该 workspace | `test_entry_assembly.py`、入口回归 |
| 共享装配遗漏引擎 event_store | 存储传给 Worker 和 materializer，却未传给 Pregel；工具事件可见，但 TURN/CHANNEL_WRITE/STEP_END 缺失，不能证明 commit point 与恢复日志完整 | `assemble_execution` 同时将事件存储注入引擎；新增真实执行回归检查 TURN 与 commit 事件 | `test_builtin_backend.py`、工作流与入口回归 |
| builtin 请求语义不一致 | 幂等比较忽略 graph/model 等后端配置；调用方可在调度后修改输入；interrupt 后报告 succeeded；错误 reference kind 可命中 Run | 比较包含执行定义；复制 JSON 输入和图/工具配置；interrupt 用 unknown 和显式原因表达，直到有继续执行契约；查询验证 Run kind/issuer | `test_builtin_backend.py`、`test_backend_contract.py` |
| 独立包验证覆盖有缺口 | CI 只运行 `tests/`，runner 包级测试未执行；冷启动子进程继承 PYTHONPATH 可使源码泄漏 | CI 纳入 runner 包测试；冷启动移除 PYTHONPATH/PYTHONHOME，使用 Python `-I` 消费安装的 wheel | CI、SC01/SC02 |

## 尚未完成的 Step5 交付

以下差距不能以本轮 bug 修复或 SC01/SC02 通过替代验收：

1. **5c 共享执行应用服务**：runner 仍自行构造固定 `model → read-tool` 图并直接消费 Pregel；平台 adapter 和 builtin backend 使用 `assemble_execution`。需要把宿主切到同一执行应用服务，让模型、工具、上下文、安全、事件装配契约一致；固定库存工具仅为演示 adapter。迁移应另设聚焦 change，验证两条路径行为，不在安全修补中重写执行框架。
2. **正式 HTTP 后端绑定**：预览 `/runs` 仍用宿主自己的请求、字符串引用、状态和事件格式，缺完整 `ExecutionRequest`、幂等准入和标准 receipt/envelope；`/capabilities` 也不是完整 `BackendCapabilities`。保留预览客户端兼容，新增契约 adapter 并通过 Step3 公共绑定测试后才可宣称正式 backend。Step6 负责持久化，不负责补回本步的契约一致性。
3. **5d 入口迁移尾项**：定时 workflow 仍经 `WorkflowTestRunner`；调度 manager 尚未接 executor registry；A2A 尚无 caller→agent 的身份映射。前两者分别明确迁移 change 与 Step6 接线责任，完整 A2A 鉴权在 Step7/Step16 认证前完成。
4. **事件/错误/产物及等待/订阅视图**：现有映射与进程内事件不等价于持久 Run 订阅，仍按原方案保持未完成；Step5 关闭契约映射，Step6 关闭持久性与控制命令语义。

## 技术预览边界

- SC01/SC02 的 `implemented` 表示只读预览场景已实现，不代表 5c 所有架构要求完成，也不构成生产认证。
- 模型 endpoint 测试验证真实 HTTP 调用路径和来源记录，使用测试 HTTP transport，不宣称任何真实模型供应商已认证。完整模型协议/选择工具逻辑另行接 adapter。
- 预览 JSON Schema 仅支持内部 `$ref`；远程 schema 引用启动拒绝，避免校验请求时访问未配置网络目标。
- 取消在调用边界协作执行；正在执行的外部调用可能完成，返回 requested 不等价于 applied，更不等价于撤回业务动作。
- 本地证据写入失败不允许新动作；动作已发生后证据写入失败需人工核查，不能承诺恢复。完整 SC06 及跨进程存储仍未认证。
- builtin 尚未实现 budget/deadline 准入，携带这些约束的请求明确返回 unsupported，不静默忽略。旧会话缺失的 TURN/提交日志不能由此次修补补写，也不能作为历史恢复认证证据。
- 共享入口现在按 workspace 解析工具；其他未采用该共享装配的旧调用方仍需在治理收敛中核对，不扩大为全平台已完成租户认证的结论。
- OpenSpec change 保留待合并/归档；未更改 main，未提交或推送。

## 验证记录

- 广泛回归：`tests/test_execution`、分层/内核独立性、真实聊天入口、工作流、A2A、MCP 与 runner 包测试，**514 passed / 12 skipped**。使用已构建的 TypeScript pilot，设置 `HECATE_REQUIRE_LIVE_PILOT=1`；跳过项是“不受单一 schema 管理”的样例，改由专属 binding 测试覆盖，不是异构后端缺席。
- 共享装配引擎事件修复后，重跑 builtin、入口服务、G3、MCP、工作流各 wiring 与 runner：**133 passed**。
- 最后预算、立即关停与审计落盘边界的 builtin/runner 回归：**82 passed**。
- 平台关联、工具 registry、评估 engine/离线 runner 回归：**48 passed**；独立打包与定时任务尾链一组：**25 passed**。各组有重叠，不相加宣称独立用例总数。
- 最终 wheel 的 SC01/SC02 冷启动、授权读取、越权拒绝、参数拒绝与 manifest 写权限拒绝：**10 passed**。从仓库之外的临时目录以 `-I` 启动，移除源码路径环境变量，仅消费非 editable wheel。
- 全仓库 `ruff check` 与 `ruff format --check` 通过；平台、独立内核、宿主 `mypy` **708 source files / no issues**；OpenSpec 修复 change 严格验证与执行栈版本矩阵检查通过。
- 本地 Python 为 `3.14.6`（项目要求 `3.12+`）。类型检查使用已安装 mypy 的纯 Python 源码隔离运行，因本机策略阻止其二进制模块。未运行全量平台测试；合并前仍需项目 CI，包括 Python 3.12 和 PostgreSQL 迁移门禁。
