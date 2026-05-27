## Why

P1 的 LLM 调用链路是"消息原样透传"——对话历史、工具定义、知识库检索结果直接拼接到 prompt 发给模型。这种方式在简单场景可用，但无法支撑企业级 Agent 的核心需求：

1. **Token 预算失控**：无预算管理，长对话或工具密集场景容易超出上下文窗口，导致截断或调用失败
2. **上下文质量低**：所有历史消息平等对待，无优先级区分，关键信息被淹没在噪音中
3. **工具结果不可追溯**：工具返回值作为原始文本拼接，无结构化归一化、无来源追踪
4. **模型差异被忽视**：不同 LLM Provider（GPT-4o / Claude / Qwen）对 prompt 格式、工具定义、system message 长度有不同偏好，P1 统一格式发送

Context Engineering 是 Agent 平台的核心竞争力（JD 分析覆盖率仅 40-50%），是 P2 必须补齐的能力。

## What Changes

- **新增 `services/context/` 模块**：独立于现有 services，提供上下文组装、预算治理、证据管理三个核心能力
- **Context Assembler**：在每次 LLM 调用前，根据当前任务阶段动态组装上下文（动态能力视图 + 任务工作面板），替代 P1 的消息原样透传
- **Budget Manager**：按 session 维度管理 Token 预算，支持结构化降级策略（丢弃低优先级消息 → 压缩历史 → 紧急摘要）
- **Evidence Tracker**：对工具调用结果进行归一化处理、来源标记、重要性评分，提供可追溯的证据链
- **Provider Shaping**：根据目标 LLM Provider 的特性（system message 长度限制、工具定义格式偏好、指令遵循风格）对组装后的上下文进行最终适配

## Capabilities

### New Capabilities

- `context-assembler`: 动态上下文组装——根据任务阶段（探索/收敛/执行/验证）动态选择消息子集、工具列表、知识片段，构建结构化任务工作面板
- `budget-governance`: Token 预算治理——按 session 维度分配和追踪 Token 预算，提供多级降级策略（丢弃 → 压缩 → 摘要）
- `evidence-management`: 证据管理——工具调用结果的结构化存储、归一化处理、来源追踪、重要性评分
- `provider-shaping`: Provider 适配——根据目标 LLM Provider 特性对最终上下文进行格式适配和优化

### Modified Capabilities

（无——所有变更都是新增模块，不修改现有 spec 行为）

## Impact

- **新增代码**：`src/hecate/services/context/` 目录，约 800-1200 行
- **修改代码**：`services/llm/service.py`（调用 context assembler）、`services/conversation.py`（集成 budget manager）、`engine/ports.py`（新增 context 相关 port 方法）
- **新增数据模型**：`EvidenceModel`（证据表）、`BudgetSnapshotModel`（预算快照表）、Alembic migration
- **新增 API**：无（内部服务，不暴露 API 端点）
- **新增依赖**：`tiktoken`（Token 计数，若 LiteLLM 未内置）
- **零破坏性变更**：P1 的所有 API 接口、数据模型、引擎行为保持不变
