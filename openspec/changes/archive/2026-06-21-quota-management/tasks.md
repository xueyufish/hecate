## 1. 模型层

- [x] 1.1 创建 `src/hecate/models/quota.py`，包含 `QuotaResourceType` 枚举（requests、tokens、cost）、`QuotaScope` 枚举（workspace、api_key）、`QuotaWindowType` 枚举（rolling_minute、daily、monthly）、`EnforcementMode` 枚举（hard_reject、soft_allow）
- [x] 1.2 实现 `QuotaModel(BaseModel)`，字段：name、resource_type、scope、scope_id、limit_value、soft_limit（可空）、window_type、enforcement、enabled、workspace_id。索引在 (workspace_id, scope, resource_type) 上
- [x] 1.3 实现 `QuotaUsageModel(BaseModel)`，字段：quota_id（UUID FK）、period_start、period_end、used_value（默认 0）、last_updated、workspace_id。索引在 (quota_id, period_start) 上
- [x] 1.4 实现 Pydantic 模式：QuotaCreateSchema、QuotaUpdateSchema、QuotaReadSchema、QuotaUsageReadSchema（带 computed 的 remaining 和 utilization_pct 字段）

## 2. 迁移

- [x] 2.1 创建 Alembic 迁移，创建 2 个表（quotas、quota_usage），索引在 workspace_id、quota_id、period_start 上。从当前头（f7a8b9c0d1e2）链入
- [x] 2.2 验证迁移干净应用，表与 ORM 定义匹配

## 3. 配额服务

- [x] 3.1 创建 `src/hecate/services/quota_service.py`，包含 `QuotaService` 类（注入 AsyncSession、workspace_id）
- [x] 3.2 实现配额 CRUD：create_quota、list_quotas（按 resource_type、scope 过滤）、get_quota、update_quota、delete_quota（软删除）
- [x] 3.3 实现使用量查询：get_usage（返回配额的当前周期使用量）、list_usage（工作空间的所有配额，带 remaining/utilization）
- [x] 3.4 实现 `check_quota(resource_type, scope, scope_id, window_type)`——返回 (allowed, remaining, reset_at)。查询 QuotaUsageModel 的当前周期，如果已过期则创建新周期记录（自动重置）
- [x] 3.5 实现 `record_usage(resource_type, scope, scope_id, window_type, amount)`——原子递增当前周期的 used_value。当阈值在周期内首次被超过时通过 AlertService 触发软限制告警
- [x] 3.6 实现 `reset_quota(quota_id)`——将当前周期 used_value 设置为 0
- [x] 3.7 实现配额定义缓存：内存中缓存，TTL 60 秒，键为 workspace_id。`get_active_quotas(workspace_id)` 返回缓存或重新加载

## 4. 配额中间件

- [x] 4.1 创建 `src/hecate/core/quota_middleware.py`，包含 `QuotaMiddleware` 类（Starlette BaseHTTPMiddleware）
- [x] 4.2 实现请求计数预检：从请求解析 AuthContext，获取 workspace_id 和 API 密钥，检查工作空间级每日请求配额和 API 密钥级 RPM 配额。如果超过则返回 429 带 Retry-After 和 X-Quota-* 头
- [x] 4.3 实现响应头注入：成功响应上，从缓存的配额数据添加 X-Quota-Limit-Requests、X-Quota-Remaining-Requests、X-Quota-Reset-Requests 头
- [x] 4.4 跳过来自排除路径的中间件（/health、/docs、/openapi.json、/redoc、/metrics）
- [x] 4.5 在 main.py 中间件栈中注册 QuotaMiddleware（CORS 之后、路由之前）

## 5. LLM 后配额记录

- [x] 5.1 向 LLMWorker 添加配额记录钩子：LLM 调用完成且使用量记录在 TraceModel 后，调用 QuotaService.record_usage 记录 token（每日 + 月度）和成本（月度）
- [x] 5.2 集成软限制告警：当 record_usage 在周期内首次超过 soft_limit 阈值时，通过 AlertService 创建 AlertEventModel，alert_type 为 quota_soft_limit_reached
- [x] 5.3 向 LLM 调用端点（聊天补全、代理执行）添加配额预检：在处理前，检查工作空间是否已超过硬限制 token 或成本配额。如果超过则返回 429

## 6. 告警集成

- [x] 6.1 将 QUOTA_SOFT_LIMIT_REACHED 添加到 models/alert.py 中的 AlertType 枚举
- [x] 6.2 向 signal_provider.py 添加 QuotaSoftLimitSignalProvider（读取 QuotaUsageModel 利用率）
- [x] 6.3 更新 NotificationDispatcher 消息模板，在告警消息中包含配额名称和利用率百分比

## 7. API 层

- [x] 7.1 创建 `src/hecate/api/management/quotas.py`，包含 quotas_router
- [x] 7.2 实现 POST/GET/PUT/DELETE `/api/quotas`，用于配额定义 CRUD，带 AuthContext + AsyncSession 依赖
- [x] 7.3 实现 GET `/api/quotas/usage`，带可选的 resource_type 过滤器，返回使用摘要带 remaining 和 utilization_pct
- [x] 7.4 实现 POST `/api/quotas/{id}/reset`，带工作空间管理员角色检查
- [x] 7.5 在 main.py 中注册 quotas_router，前缀 /api，标签 ["quotas"]

## 8. 配置

- [x] 8.1 向 core/config.py 添加配额设置：QUOTA_ENABLED（bool，默认 True）、QUOTA_DEFAULT_WORKSPACE_RPM（int，默认 60）、QUOTA_CACHE_TTL（int，默认 60）

## 9. 测试

- [x] 9.1 创建 `tests/test_services/test_quota_service.py`——测试 QuotaModel/QuotaUsageModel 创建、配额 CRUD、使用量查询、check_quota 逻辑（允许/拒绝）、record_usage（递增 + 自动重置）、reset_quota
- [x] 9.2 测试配额定义缓存：缓存命中避免数据库查询，TTL 后缓存刷新
- [x] 9.3 测试周期自动重置：过期的每日周期创建新记录，过期的月度周期创建新记录
- [x] 9.4 测试软限制告警：超过 soft_limit 创建 AlertEventModel，同一周期内两次超过仅创建一个事件
- [x] 9.5 测试中间件：配额内允许请求，超过时 429，存在头信息，未配置配额时无头信息
- [x] 9.6 测试 LLM 后记录：token 使用量递增每日+月度配额，成本递增月度配额

## 10. 验证

- [x] 10.1 运行 ruff check src/hecate/ tests/——零错误
- [x] 10.2 运行 ruff format --check src/ tests/——零更改
- [x] 10.3 运行 mypy src/——零错误
- [x] 10.4 运行 pytest tests/ -q——所有测试通过
