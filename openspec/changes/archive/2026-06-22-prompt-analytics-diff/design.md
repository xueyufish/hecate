## Context — 背景

该平台拥有 Prompt CRUD 与版本管理（8.5a）：`PromptModel`（名称、current_version）、`PromptVersionModel`（prompt_id、版本、模板、变量、标签）、`PromptService`（创建、更新、回滚、按标签获取）和 9 个 API 端点。`TraceModel` 具有 `metadata_` JSON 字段但没有显式的提示引用。`LLMWorker` 执行提示但不记录使用了哪个提示版本。

研究覆盖了 10+ 个平台：LangSmith（显式 FK 从提示到追踪、变体分析仪表板）、LangFuse（元数据链接、每版本时间序列）、Vellum（发布审核工作流、审批关卡）、Dify（提交消息、基于工作空间角色的发布）、AgentArts（APM 集成、版本比较）、Humanloop（AI 生成的变更摘要）、Promptflow（变体实验）。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 版本差异，包含行级变更、变量变更和 token 数量比较
- 每版本分析，通过元数据链接的追踪衍生指标
- 版本创建时的提交消息
- 使用 LLMService 的 AI 辅助变更摘要
- 带 RBAC 执行保护的标签
- 带指标的并排版本比较

**非目标：**
- 提示版本的 A/B 测试（复用现有的 6.8a A/B 测试基础设施——未来集成）
- 提示回归关卡（新版本时自动触发评估套件——未来集成）
- 可视化提示编辑器 UI（这是后端 + API 仅）
- 提示市场/共享（独立功能）
- 多语言提示翻译（独立功能）

## Decisions — 决策

### D1: 用于分析的追踪元数据链接（不对 TraceModel 进行迁移）

**决策**：使用 `TraceModel.metadata_` JSON 字段存储提示引用。当 LLMWorker 使用提示时，它将 `metadata.prompt_id` 和 `metadata.prompt_version` 写入追踪记录。分析查询通过这些元数据字段过滤追踪。

**理由**：LangFuse 成功使用了此模式。它避免了对 TraceModel 的迁移（TraceModel 是高流量且有大量行的表）。所有数据库后端都支持 JSON 元数据过滤。添加 `prompt_version_id` FK 列的替代方案需要在最大的表上进行大量迁移并增加索引开销。

**考虑的替代方案：**
- 向 TraceModel 添加 `prompt_version_id` FK（LangSmith 模式）——精确的 JOIN 但在大表上需要大量迁移
- 时间窗口推断（Dify 模式）——当多个版本共存时不精确
- 独立的 prompt_usage 表——对 v1 过度设计

### D2: 基于 difflib 的结构化输出版本差异

**决策**：使用 Python 的 `difflib`（标准库）计算版本模板之间的行级差异。返回结构化的 JSON 差异条目数组（类型：context/added/removed、内容、行号），而非原始的统一差异文本。

**理由**：所有调查的平台都使用行级差异作为基础层。difflib 经过实战检验、零依赖且结果准确。结构化的 JSON 格式使前端能够在任何差异查看器（并排、内联或统一）中渲染。原始的统一差异也可通过查询参数作为替代格式使用。

**差异输出模式：**
```json
{
  "from_version": 2,
  "to_version": 3,
  "from_commit_message": "初始版本",
  "to_commit_message": "添加引用说明",
  "added_lines": 3,
  "removed_lines": 1,
  "token_delta": 25,
  "diff_entries": [
    {"type": "context", "from_line": 1, "to_line": 1, "content": "你是一个有用的助手。"},
    {"type": "removed", "from_line": 2, "to_line": null, "content": "请简洁。"},
    {"type": "added", "from_line": null, "to_line": 2, "content": "始终引用你的来源。"},
    {"type": "added", "from_line": null, "to_line": 3, "content": "提供详细解释。"}
  ]
}
```

### D3: 通过配置 + AuthContext 角色检查保护的标签

**决策**：在 `core/config.py` 中定义保护的标签为 `PROTECTED_PROMPT_LABELS`（默认：`["production"]`）。当 `PromptService.update_prompt` 被调用且标签变更添加或移除受保护标签时，服务检查 `AuthContext.role`。只有 `admin` 角色的用户可以修改受保护标签。非管理员用户收到 403 Forbidden。

**理由**：Dify 使用基于工作空间角色的发布（编辑者→预览，管理员→发布）。Vellum 有完整的发布审核工作流。对于 v1，更简单的基于配置的方法加角色检查就足够了。配置列表使其可扩展——组织可以添加自定义受保护标签（例如 "staging-eu"、"compliance-approved"）而无需更改代码。

**考虑的替代方案：**
- 完整的审批工作流（Vellum 模式）——对 v1 来说太重，需要审核人模型、通知分发、审批状态机
- 数据库中的每标签 RBAC 规则——灵活但增加了复杂性，配置对于常见情况就足够了
- 环境隔离（Dify 模式）——需要每个环境单独的提示存储，过度设计

### D4: PromptVersionModel 上的提交消息

**决策**：向 `PromptVersionModel` 添加 `commit_message: str | None` 字段。`PromptUpdateSchema` 接受可选的 `commit_message`。`PromptService.update_prompt` 将其持久化到新版本上。`PromptVersionReadSchema` 在响应中包含它。

**理由**：Dify、LangSmith、LangFuse 都支持手动提交消息。Vellum 要求它们进行审核。对于 v1，手动输入就足够了。AI 辅助摘要功能（D5）通过在需要时生成摘要来补充这一点。

### D5: 通过 LLMService 的 AI 辅助变更摘要

**决策**：添加 `POST /api/prompts/{id}/versions/{version}/summary` 端点，通过将版本差异发送到 LLMService 生成人类可读的变更描述。LLM 提示指示它总结改变了什么以及为什么这可能重要（例如"将语气从正式改为随意，添加了 2 条关于引用的新说明"）。

**理由**：Humanloop 自动生成变更摘要。Vellum 在审核期间提供 AI 影响分析。这是一个差异化因素——开发者无需手动阅读差异即可即时了解变更。摘要是按需生成的（非创建时），以避免每次版本更新时产生 LLM 成本开销。

### D6: 通过元数据 JSON 上的 SQL 查询进行分析聚合

**决策**：`PromptAnalyticsService` 查询 TraceModel，通过 `metadata_->>'prompt_id' = X AND metadata_->>'prompt_version' = N` 过滤，然后聚合：COUNT(*) 用于调用次数、AVG(end_time - start_time) 用于延迟、SUM(usage->>'total_tokens') 用于 token、COUNT(status='error')/COUNT(*) 用于错误率。成本通过 CostService 对过滤后的追踪集计算。

**理由**：LangFuse 使用相同的基于元数据的聚合模式。PostgreSQL 支持带有 GIN 索引的 `->>` JSON 路径运算符以实现高效过滤。SQLite（测试）支持 `json_extract()`。SQLAlchemy 中的抽象层处理两者。

## Risks / Trade-offs — 风险 / 权衡

- **[风险] 大型追踪表上的元数据 JSON 查询可能很慢** → 通过如果出现性能问题，在未来迁移中向 `traces.metadata` 添加 GIN 索引来缓解。对于 v1，查询首先按 workspace_id + 时间范围扫描，然后按元数据过滤。

- **[风险] LLMWorker 并非始终知道它使用的是哪个提示版本** → 通过使元数据写入条件化来缓解——仅当 `agent_config.prompt_id` 已设置时。如果未配置提示，则不写入元数据，该追踪不会出现在分析中。

- **[权衡] v1 中无审批工作流** → 带角色检查的受保护标签对大多数团队来说就足够了。完整的审核工作流可以在以后根据需要添加。

- **[权衡] AI 摘要是按需的，非自动的** → 避免了每次版本更新时的 LLM 成本。用户在需要摘要时触发它。这与 Humanloop 的模式匹配。

- **[权衡] v1 中无 A/B 测试集成** → 现有的 6.8a A/B 测试基础设施可以在以后利用。目前，版本比较分析提供了做出手动部署决策的数据。
