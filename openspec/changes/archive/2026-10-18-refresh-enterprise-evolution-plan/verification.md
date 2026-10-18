# 核对与验证记录

## 基点与范围

- 已成功执行 `git fetch origin main`，核对基点与 worktree HEAD 均为 `40f7e129b7c296be2baf4a6ef3f086394eb1c7aa`（#283）。原方案最后变更为 `1017abdf`（#265）。
- 通过 `bash scripts/opsx-flow.sh start refresh-enterprise-evolution-plan --no-tools` 创建独立 worktree，分支为 `feat/refresh-enterprise-evolution-plan`；开发环境通过脚本的 `uv sync --locked` 完成。
- 仅修改演进方案及本 change 工件。沿用 ADR-037、现有主 specs 和退出条件，不修改运行行为、默认路由、产品边界或功能清单。
- 文档中的“已交付”综合核对当前实现、测试与已合并归档记录；历史验证报告中“未推送/未归档”是当次记录，交付状态以当前 origin/main 实际包含这些实现为准。

## step1—step19 逐项核对

| 步骤 | 代码/证据核对入口 | 结论与处理 |
|---|---|---|
| step1 | `docs/refactor/platform-evolution-baseline.md`、`standalone-consumption-baseline.md`、`tests/scenarios/manifest.yaml` | 基线及 SC 规格已有；真实数据流、通用任务、成本比较仍未闭合，保留原待办。 |
| step2 | ADR-037、`openspec/specs/unified-chat-execution/`、`dynamic-orchestration/`、归档 `step2-agent-native-baseline-alignment` | 原方案仍要求整项同步，实际已完成目标职责/规范/规划数据对齐；改为已交付并保留后续实现增量。 |
| step3 | `execution_driver.py`、`execution_service.py`、`execution_assembly.py`、Runner `engine.py`、`accepted_decision.py` | 类型解耦与决策接缝已交付；builtin/Runner 生产装配仍为 Pregel，非图驱动为测试验证，完整 Controller 尚未接入。 |
| step4 | `src/hecate/models/{agent_deployment,task,run}.py`、`tests/test_execution/test_task_run_registry.py` | 既有模型/登记保持；不能视为准入、完整目标/验收版本或独立 Workflow 身份已闭合。 |
| step5 | `agent_turn.py`、`port_decision_responder.py`、`execution_driver.py`、`src/hecate/studio/workflows/execution_service.py`、Runner `engine.py` | 删除服务仍直接绑定 PregelRuntime 的过期描述；单回合组件不等于完整 Controller，不自动关闭 G3 或接入 Runner。 |
| step6 | `DecisionStep`、`SqlAcceptedDecisionStore`、`AcceptedActionStep`、`AcceptedActionBatch`、`AgentTurn` 及 durable 真进程测试 | 将决策接受、冻结模型输入、单动作/串行恢复、单回合组合标为已交付；Plan Revision、通用 Child Run、任务验收、跨回合调度继续开放。 |
| step7 | `tool_call_executor.py`、`workers/tool_worker.py`、`accepted_action_step.py`、`step7-shared-tool-call-executor/verification.md` | 共享工具执行已提取且被决策动作派发实际消费；保留最终参数/权限、旁路、断连/撤权、预算及完整 profile 认证。 |
| step8 | ADR-037 与原方案撤销条款 | 继续撤销；既有协议试点不转为当前外部 Runtime 支持承诺。 |
| step9 | `packages/hecate-memory/src/hecate_memory/provider.py`、既有向量 adapter、原方案 9a—9d | 复用已有 provider/adapter，但不能据此勾选整体绑定/迁移、Knowledge 生命周期、ACL/删除与替换符合性；保留工作包。 |
| step10 | `src/hecate/models/evaluation.py`、`src/hecate/studio/agents/versioning.py`、版本评测/范围冻结测试 | 最小内置发布评测及覆盖检查已交付，外部后端替换和运行时验收仍须扩展；沿用 OTLP 等既有资产。 |
| step11 | `AgentPublicationModel`、`AgentVersionService`、`studio/api/agent_versions.py`、`publication-history-panel.tsx` | 补记版本匹配、冻结范围/证据覆盖、原子凭据、历史 UI 和 workspace/角色边界；四种放行语义分列，rollback 不等于路由回滚，完整 manifest/本地更新仍开放。 |
| step12 | `studio/api/collaboration_patterns.py`、原方案 Team/Membership/RoleDefinition 目标 | 既有协作模式和 org/workspace 不等于企业 Team/Membership 授权体系，保留原工作包。 |
| step13 | Workflow Child Task 既有交付、`accepted_action_batch.py` 与 step13 原验收 | Workflow 子任务和单决策动作恢复不等于团队 Assignment/Delegation、交接验收或通用 Batch，保留原工作包。 |
| step14 | `channel/api/tasks.py`、`execution/task_control.py`、Task 页面与 `run-actions-panel.tsx`/`run-events-panel.tsx` | 只读任务/动作/事件页面和命令分页 API 已交付；命令列表尚未接 UI，审批/取消等写交互、目标/验收视图、团队工作台仍待交付。 |
| step15 | 既有 `studio/api/self_evolution.py` 与原方案治理规则/处置回执要求 | 既有自进化/策略接口不能证明自动处置闭环；继续条件性启动，不阻断手工运营。 |
| step16 | 入口/执行栈支持矩阵、step6f、决策/动作/单回合 SQLite/PostgreSQL 归档验证 | 已有分段证据要复用，不重建矩阵；新增组件/进程通过不能继承为平台/Runner 完整生产认证。 |
| step17 | `src/hecate/channel/a2a/` 与原跨组织信任/远端对账目标 | A2A 传输不等于联邦治理，需求条件与退出门槛沿用。 |
| step18 | `src/hecate/contracts/`、`pilots/execution-backend-ts/`、Runtime/Runner 发行包及集成文档 | 基础契约与示例不重复立项；修正模板/符合性验收中与 step8 撤销矛盾的外部 Runtime 文字，未新增生态承诺。 |
| step19 | `src/hecate/runtime/` shim、聊天直接/图路径及入口支持矩阵 | 旧路径仍存在且新 Controller 未切流，不能宣称退役完成；沿用逐入口验收/使用量/回退条件。 |

## 本次实际验证

### 组件、发布、查询与规划工具

执行 worktree 的 Python，显式收集包内测试（默认 testpaths 不包含 packages）：

```text
.venv/Scripts/python.exe scripts/dev.py test
  packages/hecate-runtime/tests/test_execution_service.py
  packages/hecate-runtime/tests/test_decision_step.py
  packages/hecate-runtime/tests/test_port_decision_responder.py
  packages/hecate-runtime/tests/test_accepted_action_step.py
  packages/hecate-runtime/tests/test_accepted_action_batch_runtime.py
  packages/hecate-runtime/tests/test_accepted_agent_turn_runtime.py
  packages/hecate-runtime/tests/test_tool_call_executor.py
  tests/test_api/test_agent_version_evaluation_binding.py
  tests/test_api/test_agent_version_access_boundary.py
  tests/test_api/test_agent_publication_receipt.py
  tests/test_execution/test_run_action_query.py
  tests/test_api/test_run_actions_api.py
  tests/test_execution/test_command_list_query.py
  tests/test_api/test_task_commands_query.py
  tests/test_scripts/test_feature_inventory.py
  -q --tb=short
```

结果：**418 passed，无失败/跳过，78.26s**。这些是既有测试，没有新增镜像实现的文档测试。

### 持久化与进程恢复

```text
.venv/Scripts/python.exe scripts/dev.py test
  packages/hecate-durable/tests/test_accepted_decision_store.py
  packages/hecate-durable/tests/test_accepted_action_bridge.py
  packages/hecate-durable/tests/test_accepted_action_batch.py
  packages/hecate-durable/tests/test_accepted_agent_turn.py
  packages/hecate-durable/tests/test_task_command_query.py
  -q --tb=short -rs
```

结果：**99 passed，无失败/跳过，41.62s**。本次未配置 `DURABLE_TEST_POSTGRES_URL`，这些文件按环境动态添加 PostgreSQL 参数，因此 PG 分支本次未收集，不能把“无跳过”解释成双库通过。本次覆盖 InMemory/SQLite 与相应真实子进程；PG 交付事实引用各归档报告，本次未重跑其认证。

### 文档、OpenSpec 与规划数据

- 临时 Python 检查：64 个本地证据链接全部存在；step1—step19 标题和摘要行无遗漏/重复、顺序一致；代码围栏配对、表格边界与行尾空白通过。脚本不加入仓库。
- `openspec validate refresh-enterprise-evolution-plan --strict`：valid。lightweight 设置 `skip_specs: true`，没有产品行为 delta。
- `.venv/Scripts/python.exe scripts/feature_inventory.py check --base-ref origin/main`：exit 0，413 entries，0 errors；新增/变更条目为零。189 warnings 均为 unchanged historical debt 的治理字段欠账，本次未修改规划 YAML 或生成文档，未清除或隐藏欠账。
- `git diff --check`：通过；最终再次 fetch 后 HEAD 与 origin/main 仍一致，均为 `40f7e129b7c296be2baf4a6ef3f086394eb1c7aa`。
- 初次临时校验发现一处新引用不存在，已改指事件时间线 change 中含实际验证结果的 `tasks.md`；统一新工件的 LF 换行后重新校验通过。
- 规划源 YAML 和生成文档与 origin/main 按换行归一后逐字节一致；没有变更实现代码、测试、主 specs、ADR 或 CI。
- `.venv/Scripts/python.exe scripts/dev.py check all`：ruff check 通过、1613 文件 format 通过、mypy 检查 959 个源文件无错误，exit 0。

## 保留范围

未重跑真实模型、前端浏览器/构建、干净安装、完整 PG 矩阵、生产负载或全部场景。本次不变更原真实模型 gate=false、成本未知和生产准入结论。前端/PG 的既有交付引用原工件，新增本地回归不冒充其认证。CI 与 PR review 留待后续推送流程；本次未提交、推送、归档。
