# Codex 复核 — step5e-workflow-agent-continuation

## 复核结论

复核与授权范围内的修正已完成；真实模型成功验收未通过，整个 change **尚不能全验收通过或归档**。在原 change 内修正实现与验收缺陷。限定范围为有部署身份的 Workflow 父任务/系统 Agent 子任务、声明式持久审批等待、合法批准后的原 Run 续跑、明确拒批后的父策略收敛及已有 outbox 的回调重放；不代表整体 step5e 或任何生产 profile 已认证。

## 已修正

1. **拒批终态后的父回调可能永久丢失**：原实现提交子任务终态后只做一次内存调用并吞掉异常，worker 不会再认领终态或等待任务。现由同事务落库的 `run_terminal` 经 `PlatformOutboxProjector` 重放；平台事件已投影也仍重试回调。回调异常保持 outbox 未确认，成功应用或父已推进后才确认。重试耗尽遵循既有 relay 的显式 gap 记录，不能宣称无限可靠送达。
2. **自动回调的请求摘要冲突**：完成回调携带 `result_summary`，快速完成后的 recheck 不携带，相同 command ID 因 canonical digest 不同而冲突。现两处均从子任务提交事实取结果、不传可漂移摘要，命令 ID 绑定完整父等待 token；已推进/已消费/正在等待其他子任务的父不再接收无效回调。既有精确 ID 断言更新为完整 token，未放宽为前缀。
3. **非法审批声明被静默接受**：`approval_wait: null` 不满足布尔契约却被原判断跳过。现只要字段存在就必须为 bool，覆盖 null、字符串、整数、容器等反例。
4. **快照归属的规范化与保守边界**：系统键规范化 UUID 后派生，非法或缺失 agent ID 不产生键；人类用户路径不变。`_persist_session_state` 只在 org/user 均缺失时使用系统归属，不能把部分人类上下文重新归为 Agent。补生产保存侧、不同 Agent/缺失 Agent/人类键不匹配时恢复拒绝的反例。
5. **组合测试和真实模型证据不足**：原 SQLite 测试有恒真零执行断言，后台任务可跨夹具存活并误触真实模型；轮询吞掉任意异常。现显式驱动 claim-safe worker、隔离会话存储、断言拒批后无续跑/无副作用，不吞调度异常。原 PG 测试只有一个同时承担父/子任务的进程窗口，现父/子独立 OS 进程分别验证 child-only 与 both-separately 窗口，禁用 inline dispatch 以保持实际进程分工；Windows 使用进程树终止并记录实际 worker PID，避免虚拟环境启动器 PID 与执行 PID 混淆。
6. **真实模型可能失败却测试通过**：原变体未断言父成功/子成功/产物关系，且只在后半段写摘要、失败不留证。现共用确定性机械断言：父成功、声明 Schema/产物、唯一推进、原 Run/会话、原审批引用/稳定动作消费、唯一受保护副作用、新进程首轮模型输入包含真实工具回执。成功/失败都在 finally 记录原始模型输入/输出、执行 PID、任务事实、审批绑定和总延迟；成本未知、gate=false。
7. **文档过度承诺**：waiting 任务不会因审批绑定到期自动进入 worker 重试预算，原文的“拒绝唤醒→重试预算→自动收敛父流程”没有实现。已删除该承诺，保留自动到期收敛缺口。修正 catalog delivered 标记与 inventory 状态矛盾、未来归档链接及已归档旧 change 的链接。

## 验证记录

复核使用当前 `origin/main` 的锁定开发流程；原 worktree 基线落后于共享工具/风险分层更新，已保留原未提交内容并 rebase。CI 冲突仅涉及 pytest 入口，已统一至 `python scripts/dev.py test`。原始 GLM 记录保留在 [verification.md](verification.md)，本节只记录复核实跑。

- 环境：`bash scripts/dev.sh sync --profile postgres` 成功，使用 uv.lock；首次尝试在同步尚未结束时启动 pytest 缺少 pluggy，属于测试启动顺序错误，同步完成后重新执行。
- `bash scripts/dev.sh check all`：ruff 全通过、1578 文件格式检查通过、mypy `src/ packages/ scripts/dev.py` 共 933 源文件零错误；最终检查见下方收口记录。
- `bash scripts/dev.sh test tests/test_execution tests/test_services/test_workflow tests/test_services/test_session_state tests/test_runtime packages/hecate-runtime/tests packages/hecate-durable/tests -q --tb=short --timeout=180`：**2255 passed / 54 skipped / 8 warnings**，475.93s。skip 包含缺少 PG URL 的并发专项及可选后端；warning 为 SQLAlchemy session-factory 收集、既有 task-control 三个测试未等待的 coroutine、FastAPI 弃用提示，未出现新增回调/归属测试的 warning。
- `HECATE_STEP6_POSTGRES_URL=... bash scripts/dev.sh test tests/scenarios/test_workflow_approval_restart.py tests/scenarios/test_durable_approval_restart.py tests/test_execution/test_approval_wait_park.py -q --tb=short --timeout=600`：**31 passed / 2 skipped**，294.92s；两项 skip 是未启用真实模型的变体。覆盖父/子独立进程窗口及原有单 Agent 续跑、先行写入、消费后领取前崩溃、同参数新动作再次审批。
- 真实模型单独显式启用：**1 failed / 2 deselected**，126.47s；失败含义及原始证据如下，不能并入通过计数。自动审批最初因外部模型目的地与凭据使用未明确授权而拒绝；用户明确授权后仅执行本次补采。
- 最终 focused：`bash scripts/dev.sh test tests/test_execution/test_workflow_approval_combo.py tests/test_execution/test_workflow_subtask_contract.py tests/test_execution/test_event_relay_projection.py -q --tb=short --timeout=180`：**45 passed**，35.23s，包含真实模型失败形态的 fail/await 确定性反例。
- 最终 `bash scripts/dev.sh check all`：exit 0，ruff/format/mypy 均通过；933 源文件零类型错误。
- `feature_inventory.py extract`、`sync --check`、`check --base-ref origin/main`：exit 0，412 条、0 errors；191 warnings 为未变更历史治理债务，新/变更条目增量准入通过。`openspec validate step5e-workflow-agent-continuation --strict` 与 `git diff --check`：exit 0。
- 临时容器 `hecate-workflow-review-pg`（PostgreSQL 16，127.0.0.1:55449）在核实专用任务标签后已删除并移除匿名卷；测试 worker 已退出，现有业务 PostgreSQL 未改动。
- 基线已 rebase 到 `f118462b`；原 GLM 未提交内容的安全 stash `f570e040ce0b5b682e2e1a7a63dcbc36e64dad95` 保留供核对。本次所有修正仍为未提交内容。

## 真实模型实际结果

补采 `workflow_approval_restart_20261017T063930205Z.json`：`zai/glm-4.5-flash`，evidence_version=2，**failed**，gate=false，总延迟 117.98s，成本未知。父/子 worker 实际分别重启，子在原会话收到真实工具回执，受保护工具仅执行一次；最终子输出为自然语言，违反步骤的 JSON Schema，父正确记录 `source=validation` 并失败。原 GLM 记录只有摘要且测试缺少父成功断言，不能替代修正后验收。原始失败保留，不进行挑选式重复采样；补确定性 fail/await 反例，任务 6.1 恢复未完成。

Workflow 子任务当前仅向模型提示 Schema，最终由父任务校验；它没有自动复用 chat 入口的受限格式修复。此处没有放宽父契约，也没有临时新增跨入口输出归一化/修复语义。单独明确该输出策略或模型质量基线后再验收本项。

## 保留边界

- 审批决定由可信服务边界注入；新增审批 HTTP/UI、SoD、控制面进程重启认证不在本轮。
- 父等待与子审批到期的自动收敛仍开放；过期唤醒拒绝且不消费 token。父 on_failure=await 的 reconciliation 是待对账状态，不伪报成功。
- 独立 workflow 的 Deployment/Run 身份、Runner 自主模型循环、多级嵌套故障矩阵另行设计。
- 实例默认开关不翻转；真实模型 gate=false、成本未知、延迟不作生产通过结论。
- 合成工具仅为测试 fixture；生产实现不加入业务数据模型或工单/库存逻辑。
- 本次未提交、未推送、未归档；远程 CI/PR review 留待后续提交。
