## Context — 上下文

代理模型将配置存储为 JSON 字段（model_config、tools、skills、knowledge_base_ids）。工作流存储为 Graph DSL JSON。内存块是链接到代理的独立实体。

对于导出，我们需要：
1. 序列化代理配置
2. 包含工作流 Graph DSL（如果 mode=workflow）
3. 包含内存块
4. 排除运行时数据（id、workspace_id、timestamps）

对于导入，我们需要：
1. 验证 JSON 格式
2. 使用导出的配置创建新代理
3. 创建工作流（如果包含）
4. 创建内存块
5. 按名称重新关联 KB（可选）或跳过

## Goals / Non-Goals — 目标 / 非目标

**Goals — 目标：**
- 将代理配置导出为可移植的 JSON
- 导入 JSON 以创建新代理
- 在导出中包含工作流 Graph DSL
- 在导出中包含内存块
- 前端导出/导入按钮

**Non-Goals — 非目标：**
- 导出知识库文档（太大）
- 导出对话历史
- 跨工作空间导入（P3 多租户）
- 版本迁移（推迟）

## Decisions — 决策

### D1：带元数据的 JSON 格式

**Decision — 决策**：导出的 JSON 包含 `version`、`exported_at`、`agent`（配置）、`workflow`（可选的 Graph DSL）、`memory_blocks`（列表）。

**Rationale — 理由**：自描述格式，易于扩展，包含所有必要的上下文。

### D2：排除运行时字段

**Decision — 决策**：从导出中排除 `id`、`workspace_id`、`created_at`、`updated_at`、`deleted_at`。

**Rationale — 理由**：导入会使用新 ID 创建新实体。运行时字段是环境特定的。

### D3：按名称引用 KB（可选）

**Decision — 决策**：导出包含 `knowledge_base_names` 和 `knowledge_base_ids`。导入时，如果 ID 不存在，则按名称匹配。

**Rationale — 理由**：KB ID 是跨环境不同的 UUID。名称是可移植的。

### D4：导入创建新代理（不更新）

**Decision — 决策**：导入总是创建新代理，从不更新现有代理。

**Rationale — 理由**：更安全，避免意外覆盖。用户可以在需要时删除旧代理。

## Risks / Trade-offs — 风险 / 权衡

- **[KB 不匹配]** → KB 可能在目标环境中不存在。缓解措施：导入时发出警告，跳过缺失的 KB。
- **[工作流冲突]** → 工作流名称可能冲突。缓解措施：在名称后附加时间戳。
- **[导出文件过大]** → 具有大量内存块的代理可能很大。缓解措施：内存块很小（仅文本）。
