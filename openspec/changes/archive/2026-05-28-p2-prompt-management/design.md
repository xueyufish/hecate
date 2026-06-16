## Context

P1 的 Prompt 硬编码在 Agent 配置中，无法版本化。架构文档 AD-9 规划了 `/api/prompts` 端点（P2 版本+标签，P3 A/B 测试）。数据库模型已在 architecture.md 中设计。

## Goals / Non-Goals

**Goals:**

1. 实现 PromptModel + PromptVersionModel ORM
2. 实现 Prompt CRUD API + 版本管理
3. 实现 Jinja2 模板引擎 + 变量插值
4. 实现标签部署（production/staging/development）

**Non-Goals:**

1. 不实现 A/B 测试（属于 P3）
2. 不实现 Prompt 自动优化（属于 P3）
3. 不修改现有 Agent 配置（保持向后兼容）

## Decisions

### D1: 复用 architecture.md 中的 Prompt 模型设计

**选择**：按照 architecture.md Lines 715-726 实现

**理由**：
- 模型设计已经过评审
- 支持版本化、标签、变量插值
- 与 ResourceVersion 设计一致

### D2: 使用 Jinja2 作为模板引擎

**选择**：Jinja2 + 变量插值

**理由**：
- Python 生态标准模板引擎
- 支持条件、循环、过滤器
- 安全沙箱模式防止代码注入

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| Jinja2 模板注入 | 使用 SandboxedEnvironment |
| 版本表增长快 | 设置版本数量限制 |
| 模板渲染性能 | 缓存渲染结果 |
