# Proposal

## Why

已接受决策与共享单次工具执行均已交付，本 change 将两者连接：从持久决策选择一个动作，以稳定身份经既有治理链执行或恢复。该组件不调用模型、不调度其余动作，也不把动作成功当作 Task 成功。

## What Changes

- 新增 `AcceptedActionStep`：加载并校验已提交原记录，只使用记录中的工具与实际参数。
- 身份明确包含 issuing domain、Run、decision_step_id 和 action_key；规范 JSON + 固定 UUID namespace 派生消除分隔符歧义，使用 UUID 兼容的会话键。执行身份不可由 context 或 execution_context 覆盖。
- `ToolCallExecutor.execute_accepted_call` 复用原单工具流水线，要求已验证 ownership 和实际绑定的持久 ActionLedgerHook；既有 `execute_call` 返回形状和行为不变。
- `ActionLedgerHook.validate_accepted_dispatch` 默认拒绝；SQL 实现核验 owning Task/Run、hook 租约、精确 holder/token、数据库期限和已有动作归属。没有实现此校验的旧 hook 不能用于新派发路径。
- 新路径在恢复前、审批后领取时及业务调用前验证 ownership；领取失败不能借 readonly 降级绕过。不允许 middleware 静默改写已接受参数。
- 从控制流报告 succeeded/failed/withheld；未知结果及回执无法确认返回 stopped，保留停止类别和稳定 execution_id，不解析自由文本判断是否执行过业务。
- 新增真实 SQLite/PostgreSQL 账本与租约测试；两个独立 Python 进程从同一持久库恢复原动作，业务效果只发生一次。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `accepted-agent-decision`：单动作定位、稳定身份、强制 owner/账本绑定、治理复用、真实恢复与明确结果状态。

## Impact

完整 spec-driven。改动限定在 runtime/durable package 的单动作组件、现有 executor/hook 的新消费入口、独立测试和本 change artifacts。无新 ABC/Protocol、无平行账本、无 schema 迁移；Runtime 不反向导入 durable 或平台。

不改平台 Task 查询/API、evaluation/versioning、公共 conftest、演进计划和功能清单。未交付多轮 Controller、批次调度、入口切流、真实模型质量或完整部分成功场景认证。
