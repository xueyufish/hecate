# Proposal

## Why

演进方案仍将车商 App 作为具名消费者，并以库存业务需求和 I-Ca 独立交付安排近期实施。用户已取消该业务前提，需要将方案调整为通用企业任务验证，同时保留已交付的独立执行能力和历史证据。

## What Changes

- 从当前方案移除车商 App 需求、库存业务约束及其对交付优先级的影响。
- 用读取材料、生成工单草稿、人工审批、调用测试业务 API 和核验结果描述通用验证任务。
- 将近期目标明确为内置 Agent 的受管执行闭环；I-Ca 与 I-Cb 保留各自验收范围，独立部署不以特定业务 App 为成立依据。
- 保留现有库存 API fixture、场景编号、链接、历史勾选和未完成门槛；说明测试样例不代表产品业务需求。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

无。仅修订演进方案，不改变运行行为或正式能力规格；本 change 使用 `skip_specs: true`。

## Impact

- 产品文档仅修改 `docs/refactor/enterprise-agent-platform-evolution-plan.md`。
- 不修改代码、测试、主规格、功能清单、历史报告或已归档 change；关联定位和路线图同步仍由 step2 明确跟踪。
- 文档与 OpenSpec 校验覆盖残留业务假设、交付顺序一致性、历史状态保留和本地链接。无需重新运行产品测试。
- 在隔离 worktree 实施，本次不提交、推送或归档。
