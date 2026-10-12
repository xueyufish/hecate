# Proposal

## Why

演进方案将 step5e 列为独立产品闭环(I-Ca/M-S)的生产前置,但四条执行机制(聊天直接工具循环、Pregel Agent 图、确定性 Workflow、独立 Runner)至今没有同一套对外行为:代码核查发现**直接循环把未知写结果的 `OutcomeUnknownError` 当作普通错误文本回灌给模型继续推理**(违反 G2"未知结果停止"语义)、`BudgetRefusedError` 未被捕获导致预算耗尽后仍发起下一轮模型调用、重启重放(`completed_result`)在 claim 账本未绑定时静默短路;Workflow 父任务的子步骤只有 `goal` 字符串、无产物/输出 schema 校验、子回调无幂等去重(重复/迟到回调可能重复推进父流程)。同时,runner 预览的固定工具计划不能证明模型自主选工具(SC03 gap 一直登记到本步),也没有任何真实模型多轮执行的证据基线与四机制差异矩阵可供 ADR 决策。

## What Changes

按演进方案"本地与受管分别验收、无证据不预定结论"的约定,本 change 交付 step5e:

- **直接循环对齐引擎语义**:未知结果(`OutcomeUnknownError` 及 duck-typed `outcome_unknown`)终止对话并以 needs-review 收尾(绝不回灌模型);预算拒绝(`BudgetRefusedError`)以显式 budget-exhausted 理由终止(迭代上限单独计数、单独理由);会话重入时已完成动作先经回放(`completed_result`)再决定新派发,claim 账本绑定时不得静默短路;客户端断连在迭代边界协作取消。
- **结构化最终输出**:聊天执行可声明输出 schema,最终回答在返回前校验;校验失败做一次有界重试(注入校验错误反馈),仍失败则以显式 invalid-output 理由失败。
- **Workflow 调用自主 Agent 子任务**:子步骤 schema 扩展(mode、输出 schema、期望产物,向后兼容 `goal`);子任务终态回调先做产物/输出校验,**父流程不依据 Agent 自称完成直接推进**;校验失败路由到 `on_failure`;父任务按 (task, step) 幂等去重子回调,重复/迟到回调显式拒绝且不产生第二次推进。
- **四机制差异矩阵**:新建矩阵文档,按方案要求的八轴(模型调用、工具选择/结果回灌、结构化输出、预算/停止、审批等待、持久化、取消、错误事件)比较直接循环、Pregel 图、确定性 Workflow、Runner;语义变化时同步维护。
- **真实模型多轮验证**:新增反馈驱动的多轮脚本 responder("读材料→按结果选择后续工具→生成结构化结果")作为确定性 CI 场景;同一任务定义经基线 runner 跑真实模型,产出固定模型/工具版本、质量 rubric、延迟与成本阈值的记录式报告(tier 2,CI 不收集不执行);确定性 CI 与真实模型报告分别保存。
- **ADR**:基于矩阵与真实模型证据比较"复用现有图机制 / 收敛直接循环 / 抽取轻量 harness"的改动、依赖、恢复复杂度与维护成本;Runner 是否需要模型驱动选择由 ADR 结论决定,无证据不预定拆包、不移除 Pregel、不预设 step5f。

**本切片不含**(后续 change 承接):Runner 模型驱动选择或改用共享装配的实施(等 ADR 结论另行立项);step5d 剩余项(聊天事件持久化入平台契约、定时 WorkflowExecutor 收口、A2A per-agent 身份、G3 默认启用翻转);受管断连/重连认证(I-Cb);真实业务收益与行业合规验收。

## Capabilities

### New Capabilities

- `autonomous-execution-parity`: 聊天直接循环与引擎路径共享的对外停止/恢复/输出语义——未知结果终止、预算终止、重放先于派发、协作取消、结构化最终输出,以及两条聊天路径上的一致可观察行为。
- `workflow-agent-subtask`: 确定性 Workflow 调用自主 Agent 子任务的步骤契约——扩展步骤 schema、产物校验先于父流程推进、回调幂等与失败路由。

### Modified Capabilities

(无——路径选择与收敛仍由 `unified-chat-execution` 管辖;step6 持久等待/唤醒语义不重复定义,本 change 只引用。)

## Impact

- 代码:`src/hecate/channel/api/v1/chat.py`(循环停止语义、取消、结构化输出)、`src/hecate/core/composition/governance.py`(回放绑定与停止异常传播)、`src/hecate/execution/workflow_child.py` + `task_dispatcher.py`(步骤 schema、产物校验、回调幂等)、`src/hecate/channel/api/v1/schemas.py` 或请求模型(output_schema 字段);`packages/hecate-runtime` 仅在引擎路径行为缺口证实后最小修正(现有 `RECEIPT_UNKNOWN` 恢复表预期已满足多数场景)。
- 测试:`tests/scenarios/`(多轮反馈驱动 responder、新 S 场景、manifest 登记)、`tests/scenarios/baselines/`(真实模型记录式报告 schema 与样例结构)、`tests/test_channel/`/`tests/test_execution/`(停止语义、回调幂等)。
- 文档:`docs/design/` 四机制差异矩阵、新 ADR(比较三方案)。
- 兼容:G1/G2 既有回归与 step6 不变量必须保持;`goal`-only 旧步骤负载继续可用;直接循环仍是 G3 回滚路径,本 change 不改变默认路由。
- 验收基线:对应演进方案 step5e 清单与 SC03 gap("固定工具计划不能证明模型自主选工具")、验收矩阵"自主 Agent 多轮执行"行。
