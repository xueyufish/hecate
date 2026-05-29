# Product Step 2: Model Provider 管理后台

## Problem

当前模型管理方案存在两个问题：

1. **模型列表不可控** — `/v1/models` 直接调用 LiteLLM `get_valid_models()` 返回所有模型（200+），用户在创建 Agent 时下拉列表过长且包含不相关模型（如 image generation、realtime 等）
2. **Provider 配置不灵活** — API Key 写在 `.env` 文件里，需要重启服务才能生效，无法在 UI 上管理

## Solution

参考 Dify 和 FastGPT 的模式，实现 **Model Provider 管理后台**：

### 核心功能

1. **Provider 管理** — 后台页面增删改 Provider（OpenAI、智谱、DeepSeek、通义千问等），每个 Provider 配置 API Key + 可选 Base URL
2. **模型注册表** — 数据库存储可用模型列表，按 Provider 分组，支持启用/禁用单个模型
3. **分组选择** — 创建 Agent 时按 Provider 分组展示模型，显示友好的 Provider 名称和模型名
4. **状态指示** — 每个 Provider 显示连通状态（API Key 是否有效）

### 数据模型

```
model_providers
├── id (UUID)
├── name (string) — "openai", "zhipu", "deepseek" 等
├── display_name (string) — "OpenAI", "智谱", "DeepSeek"
├── api_key (string, encrypted)
├── base_url (string, optional) — 自定义端点
├── is_enabled (bool)
├── status (string) — "active" | "inactive" | "error"
└── timestamps

model_registry
├── id (UUID)
├── provider_id (FK → model_providers)
├── model_id (string) — "gpt-4o", "glm-4-flash"
├── display_name (string) — "GPT-4o", "GLM-4 Flash"
├── model_type (string) — "chat" | "embedding" | "image" | "audio"
├── capabilities (JSON) — {"vision": true, "tool_calling": true, "json_mode": true}
├── max_context (int) — 最大上下文长度
├── is_custom (bool)
├── is_enabled (bool)
└── timestamps
```

### API 端点

```
# Provider 管理（管理员）
POST   /api/model-providers          — 添加 Provider + API Key，自动发现模型
GET    /api/model-providers          — 列出所有 Provider 及状态
PUT    /api/model-providers/{id}     — 更新 Provider 配置
DELETE /api/model-providers/{id}     — 删除 Provider
POST   /api/model-providers/{id}/test — 测试连通性

# 模型注册表（管理员）
GET    /api/models                   — 列出所有注册模型（按 Provider 分组）
PUT    /api/models/{id}              — 更新模型（启用/禁用、显示名）
POST   /api/models                   — 手动添加自定义模型

# 用户侧
GET    /v1/models                    — 返回已启用的 chat 类型模型（按 Provider 分组）
```

### 前端页面

1. **设置 → 模型服务商** — Provider 列表页，显示状态、添加/编辑/删除/测试连通
2. **设置 → 模型管理** — 模型列表页，按 Provider 分组，启用/禁用单个模型
3. **创建 Agent 模型选择** — 改造为按 Provider 分组的下拉组件

### 集成方式

- Provider 添加后调用 LiteLLM `get_valid_models(custom_llm_provider=...)` 自动填充模型列表
- 保留 `.env` 文件方式作为 fallback（开发阶段）
- `/v1/chat/completions` 请求时通过 LiteLLM 路由，LiteLLM 根据 model prefix 自动匹配 provider

## Priority

P2 — Step 2 范围，与 Workflow Canvas、Memory System 同期实现

## References

- Dify Model Providers: `Settings → Model Providers`，分组展示，状态指示
- FastGPT: 两层配置（模型定义 + 通道/API Key）
- LiteLLM: `get_valid_models()` 自动发现 + `custom_llm_provider` 参数按 Provider 过滤
