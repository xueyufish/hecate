# Review 修复验收记录

本记录对应代码 review 后的修复，保留 GLM 原有未提交实现。本轮未执行 commit、push 或 archive；完成的是本 change 的本地治理切片，step7 的其余范围仍按 proposal 排除项推进。

## 修复范围

- 所有本切片受影响生产入口使用共享治理边界。Runner 启动装配本地规则、审批与可选预算，空规则默认拒绝；聊天直接工具/模型路径与 Gateway 接入共享判定。HTTP、MCP、A2A、IM、调度和任务派发传入已解析的 workspace、主体/Agent，审批装配与执行使用同一主体及会话。
- invocation ID 表示逻辑调用，参数相同的新动作使用新 ID；主体/workspace 隔离 claim。主台账独占领取，治理不会重复 claim；无主台账路径使用独立提交的 SQL claim，唯一约束覆盖终态，防止并发重复派发及请求回滚抹除副作用意图。
- 已完成动作先重放回执，再考虑新动作授权；标量和 JSON 回执均保留。执行或输出校验后的未知副作用保留占位，禁止自动重试；输入 schema 在副作用前校验。
- 审批授权与原子消费贯通真实 ActionRequest，校验主体、参数、资源、部署、数据域与任务范围；本地要求登记签发主体。HTTP 和受管唤醒传递权威 approval_ref；期限依赖持久时间锚和进程 epoch。回调缓存及审计事件保留审批引用。
- 数据域规则检查本次请求的目标域；运行期本地授权/撤销配置重新读取，新动作不能沿用旧权限。
- 平台和 Runner 模型调用执行预留及结算；并发容量判定原子化。取消、中断和未知用量保留预留，UNKNOWN 状态持久化，后续明确用量修订才释放差额；流式终帧前完成结算。

## 验证结果

各批测试有重叠，不将下列数量相加作为唯一用例数。

| 验证 | 结果 | 范围 |
|---|---|---|
| ruff check | 通过 | src、tests、governance/runner/durable/runtime packages，以及新增迁移 |
| ruff format --check | 通过，1443 个文件 | 同上 |
| mypy | 通过，773 个源文件 | src 及 governance/runner/durable/runtime packages |
| OpenSpec strict validate | 通过 | runtime-independent-governance |
| 治理定向回归 | 170 passed | 共享 governance 包、平台 claim/装配/模型预算、SQL 审批、回调、quota、本地治理和预算、修复专项测试 |
| 入口与运行时回归 | 386 passed | Runner、Gateway、聊天入口、ToolWorker、审批回调、凭据/事实/成本和架构边界 |
| A2A / 执行控制面扩展回归 | 550 passed，30 skipped | tests/test_a2a、tests/test_execution；skip 为缺少 PostgreSQL 环境的条件跳过 |
| 最终入口回归 | 57 passed，5 skipped | A2A、MCP、IM、调度、任务 worker/续跑、HTTP G3 及入口分层；在最后一次入口装配修复后执行 |
| SC03 真实进程 | 2 passed | 独立 wheel 安装、本地审批权威及进程重启续跑，不重复业务写入 |
| SC04 / SC05 真实进程 | 18 passed | 租约续期、受管断连重连、审批唤醒链 |
| git diff --check | 通过 | tracked diff 无空白错误 |

最终入口回归命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_a2a/test_server/test_executor_entry.py tests/test_mcp/test_agent_chat_entry_parity.py tests/test_services/test_scheduling/test_executors.py tests/test_core/test_im_providers.py tests/test_gateway/test_im_routing.py tests/test_execution/test_builtin_continuation.py tests/test_execution/test_worker_integration.py tests/test_channel/test_chat_engine_g3_entry.py tests/test_layering_entry_imports.py -q --tb=short -x
```

进程矩阵命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/scenarios/test_sc03_local_approval_restart.py -q --tb=short
.venv/Scripts/python.exe -m pytest tests/scenarios/test_sc04_lease_renewal_policy.py tests/scenarios/test_sc05_managed_reconnect.py tests/scenarios/test_sc05_managed_wake_chain.py -q --tb=short -x
```

验收重点在 `tests/test_core/test_governance_repairs.py`（文件 SQLite、多独立连接的 claim 与预算竞争、回滚、回执、主体和 schema）、`tests/test_core/test_model_budget.py`（同步/流式/取消）、`packages/hecate-runner/tests/test_runner_governance_repairs.py`（本地预算竞争、审批等待、域判定、撤销）及上述真实入口/进程矩阵。

## 验证边界

- 本轮 SQL 并发/回滚证据来自独立连接 SQLite 文件库。未配置真实 PostgreSQL，相关专用用例按声明跳过；不宣称 PostgreSQL 部署认证完成。
- 未运行整个仓库的 pytest 全量套件；完成受影响范围及进程矩阵，完整套件仍由 CI 执行。
- 测试输出有第三方 protobuf datetime 弃用、既有测试 session_factory 收集及 TaskControlService coroutine 警告；上述批次无测试失败。
- 真实模型多轮、供应商账单对账、受管 policy profile 完整认证和旁路封闭攻击演练未在本切片验证，详见 support-matrix.md。
