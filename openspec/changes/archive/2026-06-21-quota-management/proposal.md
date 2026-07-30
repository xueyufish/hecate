## Why

Hecate 拥有多租户 RBAC（10.1、10.2）、租户隔离（10.5）、成本仪表板（8.3）和告警系统（8.6），但无法强制每个租户的硬性资源限制。工作空间当前可以无限制地消耗 API 调用、token 和费用——仅内存中的速率限制器（每个 API 密钥 60 RPM）提供突发保护，且它是临时的，没有持久性或成本/token 感知。配额管理关闭了成本治理循环：成本仪表板显示支出，告警系统警告阈值，配额管理执行防止超额的硬性限制。

## What Changes

- **添加 QuotaModel**——每工作空间或每 API 密钥的配额定义，包含资源类型（请求、token、成本）、限制值、窗口类型（滚动 60 秒、每日、月度）、软限制阈值和执行模式（硬拒绝或软允许）。
- **添加 QuotaUsageModel**——持久化的使用追踪，包含周期开始/结束、当前已用值、最后更新时间戳。支持窗口边界的周期性重置。
- **添加 QuotaService**——配额定义的 CRUD、使用量查询、配额检查逻辑（is_exceeded）、使用量记录和周期重置管理。
- **添加 QuotaMiddleware**——FastAPI 中间件，在认证上下文解析后、路由处理前检查请求计数配额（RPM、每日请求）。返回 429 带 `Retry-After` 和 `X-Quota-*` 响应头。
- **添加 LLM 后配额记录**——每次 LLM 调用后，根据适用的配额记录实际的 token 使用量和成本。通过现有的告警系统（8.6）在阈值被超过时触发软限制告警。
- **添加 QuotaEnforcement 依赖**——FastAPI 依赖项，对资源密集型端点（聊天补全、知识上传）检查工作空间级配额。
- **增强现有的 RateLimiter**——升级 `core/rate_limit.py` 以支持基于配额系统的每工作空间 RPM 限制，替换全局 60 RPM 默认值为可配置的每工作空间限制。
- **添加 API 路由器**——`/api/quotas`（定义的 CRUD）、`/api/quotas/usage`（查询当前使用量）、`/api/quotas/reset`（手动周期重置，仅管理员）。
- **添加配额配置设置**——默认工作空间 RPM、默认每日 token 限制、默认月度成本限制、执行开关。

## Capabilities

### New Capabilities

- `quota-management`：配额定义 CRUD、使用量追踪、执行中间件、LLM 后记录以及与告警系统的软限制通知集成。

### Modified Capabilities

- `alerting`：添加 `quota_exceeded` 和 `quota_soft_limit_reached` 告警类型，当使用量超过配置的阈值时触发，复用现有的 AlertEvaluator 和 NotificationDispatcher 基础设施。

## Impact

- **新文件**：`models/quota.py`（2 个模型 + 3 个枚举 + 模式）、`services/quota_service.py`、`api/management/quotas.py`、`alembic/versions/xxxx_add_quota_tables.py`。
- **修改的文件**：`api/middleware.py`（添加 QuotaMiddleware）、`core/rate_limit.py`（与配额系统集成）、`core/config.py`（配额设置）、`engine/workers/llm_worker.py`（LLM 后配额记录）、`main.py`（路由器 + 中间件注册）、`tests/conftest.py`（模块导入）。
- **数据库**：2 个新表（quotas、quota_usage），复合索引在 (workspace_id, resource_type, window_type) 上。
- **API**：`/api/quotas` 下的 1 个新路由器组，包含 CRUD + 使用量查询 + 重置。
- **中间件**：请求管道中认证和路由处理器之间的新配额检查层。
