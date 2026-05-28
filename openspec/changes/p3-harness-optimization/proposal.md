## Why

P1-P2 没有 Harness 优化闭环——失败案例不会自动归因、不会生成约束规则、不会注入下次会话。P3 需要让系统具备自我改进能力。

## What Changes

- **新增 `FailureAnalyzer`**：LLM 驱动的失败归因分析（参考 AgentRx 10 种失败分类）
- **新增 `ConstraintGenerator`**：从失败案例自动生成约束规则
- **新增 `ConstraintInjector`**：将约束规则注入下次会话的 system prompt
- **扩展 `EvidenceTracker`**：与失败分析关联

## Capabilities

### New Capabilities

- `failure-analysis`: LLM 驱动的失败归因分析（10 种分类）
- `constraint-generation`: 从失败自动生成约束规则
- `constraint-injection`: 约束规则注入下次会话

### Modified Capabilities

（无）

## Impact

- **新增代码**：`services/harness/` 目录，约 500-700 行
- **新增依赖**：无（复用 LLM 调用）
- **修改代码**：`services/conversation.py`（集成约束注入）
- **架构影响**：Harness 闭环成为系统自我改进的核心机制
