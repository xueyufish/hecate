## MODIFIED Requirements — 修改的需求

### 需求：FastAPI 应用入口点
`main.py` 模块应使用 CORS 中间件、统一错误处理、生命周期事件、健康检查端点、路由注册和用于自动请求追踪的 OpenTelemetry 插装来初始化 FastAPI 应用

#### 场景：启动时启用 OTel 插装
- **当** FastAPI 应用启动时
- **则** 应配置 `FastAPIInstrumentor` 为每个 HTTP 请求自动创建 OTel span，使用 `opentelemetry-api` 和 `opentelemetry-sdk` 作为追踪后端

#### 场景：OTel span 包含业务属性
- **当** 处理请求且请求状态中有 `agent_id` 和 `session_id`
- **则** 根 OTel span 应包含 `agent_id` 和 `session_id` 作为属性

#### 场景：通过配置禁用追踪
- **当** 设置了环境变量 `TRACING_ENABLED=false`
- **则** 不应配置 OTel 插装且不应创建 span

#### 场景：健康检查端点
- **当** 调用 `GET /health`
- **则** 应返回 `{"status": "ok"}`
