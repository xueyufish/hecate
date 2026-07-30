## Why — 为什么

该平台具有 Prompt CRUD 和版本管理（8.5a），包含不可变的版本快照、标签和回滚。但是，无法并排比较版本、衡量每个版本的性能、保护生产标签免受未授权更改或附加变更摘要。这使得提示管理是盲目的——团队无法回答"v2 和 v3 之间发生了什么变化？"或"v3 的性能比 v2 好吗？"，而且生产标签可以被任何用户修改。

## What Changes — 变更内容

- **版本差异 API**：`POST /api/prompts/{id}/diff` 返回行级差异（添加/删除/上下文行、行号、token 计数增量、提交消息），使用 Python difflib，输出结构化的 JSON 或原始的统一差异格式
- **版本分析 API**：`GET /api/prompts/{id}/versions/{version}/analytics` 返回由 TraceModel 元数据链接衍生的指标：调用次数、平均延迟 p50/p95/p99、总 token 使用量、错误率和估计成本
- **版本比较 API**：`GET /api/prompts/{id}/compare?version=X&version=Y` 返回并排指标比较，包含调用次数、平均分数（如果评估数据可用）、延迟和成本方面的差异
- **提交消息**：PromptUpdateSchema 上的可选 `commit_message` 字段，持久化到 PromptVersionModel
- **AI 变更摘要**：`POST /api/prompts/{id}/versions/{version}/summary` 使用 LLMService 生成人类可读的变更摘要
- **受保护标签**：config.py 中的 PROTECTED_PROMPT_LABELS 列表（默认：["production"]）；通过 PromptService 中的 AuthContext 角色检查执行；仅管理员可以修改受保护标签
- **追踪元数据链接**：LLMWorker 在 TraceModel metadata_ JSON 字段中写入 `prompt_id` 和 `prompt_version`，基于 agent_config.prompt_id
- **提示分析服务**：新的 `services/prompt_analytics_service.py` 包含 `PromptAnalyticsService`，从 TraceModel + CostService 聚合指标

## Capabilities — 能力

### New Capabilities — 新增能力
- `prompt-analytics`：每提示版本的分析仪表板，包含调用次数、延迟、token 使用量、错误率和成本指标，全部通过追踪元数据链接实现
- `prompt-version-management`：版本差异（行级 + 输出格式）、提交消息、AI 辅助摘要、受保护标签 RBAC、版本比较

### Modified Capabilities — 修改的能力
- `engine/workers/llm_worker.py`：当配置了 prompt_id 时将 prompt_id 和 prompt_version 写入 TraceModel metadata_
- `api/management/prompts.py`：差异、摘要、分析和比较的新端点
- `core/config.py`：新的 PROTECTED_PROMPT_LABELS 配置设置
- `models/prompt.py`：新的 commit_message 列
- `services/prompt_service.py`：标签更新中的受保护标签检查

## Impact — 影响

- **新文件**：`services/prompt_analytics_service.py`（100 行）、`api/management/prompt_analytics.py`（150 行）
- **修改的文件**：`models/prompt.py`（+1 列）、`services/prompt_service.py`（+ 受保护标签检查）、`api/management/prompts.py`（+ 差异端点）、`engine/workers/llm_worker.py`（+ 元数据写入）、`core/config.py`（+ 设置）
- **迁移**：models/prompt.py 中的 +1 列（commit_message on PromptVersionModel）
- **依赖**：无——difflib 是标准库，分析使用现有的 TraceModel/CostService 查询
