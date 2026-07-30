## 1. 依赖和数据模型

- [x] 1.1 在 `pyproject.toml` 的 `[observability]` 可选依赖组下添加 `opentelemetry-api`、`opentelemetry-sdk`、`opentelemetry-instrumentation-fastapi`；同时添加到 `[dev]`
- [x] 1.2 使用 `uv pip install -e ".[dev]"` 安装新依赖
- [x] 1.3 在 `src/hecate/models/trace.py` 中创建 `TraceModel` ORM 模型，字段包括：`id`、`trace_id`、`parent_id`、`type`、`name`、`session_id`、`agent_id`、`user_id`、`input_data`、`output_data`、`metadata`、`usage`、`level`、`status`、`start_time`、`end_time`、`created_at`；在 `trace_id`、`session_id`、`agent_id`、`parent_id` 上创建索引
- [x] 1.4 在 `src/hecate/models/trace.py` 中创建 Pydantic 模式 `TraceCreateSchema`、`TraceReadSchema`、`TraceListSchema`
- [x] 1.5 将 `TraceModel` 添加到 `src/hecate/models/__init__.py` 导出
- [ ] 1.6 为 `traces` 表创建 Alembic 迁移

## 2. 引擎层变更

- [x] 2.1 在 `src/hecate/engine/ports.py` 中添加 `SpanContext` 数据类，字段包括：`span_id`（str）、`trace_id`（str）、`parent_id`（str | None）
- [x] 2.2 向 `EnginePort` ABC 添加 `create_span(name, parent_id=None, attributes=None) -> SpanContext | None` 抽象方法
- [x] 2.3 向 `EnginePort` ABC 添加 `end_span(span_id, output_data=None, usage=None) -> None` 抽象方法
- [x] 2.4 在 `src/hecate/engine/eventstore.py` 的 `Event` 数据类中添加 `trace_id: str | None = None` 字段
- [x] 2.5 更新 `InMemoryEventStore.append()` 以在版本化的 Event 副本中保留 `trace_id` 字段

## 3. 服务层——TracingService 重写

- [x] 3.1 重写 `src/hecate/services/observability/tracing.py`：将内存骨架替换为基于异步 SQLAlchemy 会话的生产 `TracingService`；实现 `start_trace`、`start_span`、`end_span`、`get_trace`、`list_traces` 方法，写入 `TraceModel`
- [x] 3.2 创建 `src/hecate/services/observability/trace_manager.py`，包含 `OpsTraceManager` 单例：异步队列、用于分发的 `_worker_task` 协程、`on_trace_start`、`on_span_start`、`on_span_end`、`flush` 方法
- [x] 3.3 创建 `src/hecate/services/observability/trace_providers.py`，包含 `TraceProvider` ABC 和 `NoOpTraceProvider` 默认实现

## 4. EnginePort 适配器实现

- [x] 4.1 更新 `src/hecate/services/orchestration/engine_port_adapter.py` 中的 `_ProductionEnginePort`，使用 OTel tracer 和 `TracingService` 实现 `create_span` 和 `end_span`
- [x] 4.2 更新 `src/hecate/services/orchestration/agent_execution_port.py` 中的 `AgentExecutionPort`，实现 `create_span` 和 `end_span`
- [x] 4.3 在两个适配器中从 OTel 上下文提取 `trace_id` 并传递给 `TracingService` 调用

## 5. PregelRuntime 和 Worker 集成

- [x] 5.1 更新 `src/hecate/engine/pregel.py` 中的 `PregelRuntime._emit()` 以接受 `trace_id: str | None = None` 参数并传递给 `Event` 构造
- [x] 5.2 更新 `PregelRuntime.execute()` 中所有 8 处 `_emit()` 调用点，从 `EnginePort.create_span()` 返回值传递 `trace_id`
- [x] 5.3 更新 `src/hecate/engine/workers/llm_worker.py` 中的 `LLMWorker`，在 LLM 调用前通过 `engine_port.create_span` 创建 generation span，之后调用 `end_span`，将 `trace_id` 传递给 `_emit`
- [x] 5.4 更新 `src/hecate/engine/workers/tool_worker.py` 中的 `ToolWorker`，在工具调用前通过 `engine_port.create_span` 创建 tool span，之后调用 `end_span`，将 `trace_id` 传递给 `_emit`

## 6. FastAPI 中间件

- [x] 6.1 在 `src/hecate/main.py` 生命周期中添加 OTel `FastAPIInstrumentor` 设置：配置 `TracerProvider`、`BatchSpanProcessor` 并插装应用
- [x] 6.2 在 `src/hecate/core/config.py` 中添加 `TRACING_ENABLED` 配置标志（默认 `true`）；当为 `false` 时跳过 OTel 设置
- [x] 6.3 添加中间件或依赖项，从请求状态提取 `agent_id`、`session_id`、`user_id` 并设置为 OTel span 属性

## 7. REST API

- [x] 7.1 创建 `src/hecate/api/management/traces.py` 路由，包含 `GET /api/traces`（带查询参数的列表：`session_id`、`agent_id`、`limit`、`offset`、`start_time`、`end_time`）
- [x] 7.2 添加 `GET /api/traces/{trace_id}` 端点，返回包含分层 span 树的追踪详情（使用递归 CTE 或内存中树构建）
- [x] 7.3 在 `src/hecate/main.py` 中注册 traces 路由

## 8. 测试

- [x] 8.1 测试 `SpanContext` 数据类创建和字段访问
- [x] 8.2 测试 `EnginePort.create_span` 和 `end_span` 是抽象的（不实现它们就无法实例化 EnginePort）
- [x] 8.3 测试 `Event` 数据类接受 `trace_id` 参数并默认为 `None`
- [x] 8.4 测试 `InMemoryEventStore.append()` 在存储的事件中保留 `trace_id`
- [x] 8.5 测试 `TracingService.start_trace` 创建 `status="started"` 的 TraceModel
- [x] 8.6 测试 `TracingService.start_span` 创建具有正确 `parent_id` 和 `trace_id` 的子记录
- [x] 8.7 测试 `TracingService.end_span` 更新 status、output_data、usage 和 end_time
- [x] 8.8 测试 `TracingService.list_traces` 带过滤器（session_id、agent_id、时间范围、分页）
- [x] 8.9 测试 `TracingService.get_trace` 返回追踪的所有记录
- [ ] 8.10 测试 `OpsTraceManager` 分发到本地数据库并调用提供者插件
- [ ] 8.11 测试 `TraceProvider` ABC 不可实例化；`NoOpTraceProvider` 将所有方法实现为无操作
- [ ] 8.12 测试 `_ProductionEnginePort.create_span` 返回带有有效 ID 的 `SpanContext`
- [ ] 8.13 测试 `_ProductionEnginePort.create_span` 在没有追踪上下文时返回 `None`
- [x] 8.14 通过 httpx AsyncClient 测试 traces API 端点：列表返回 200 及追踪记录，详情返回 200 及 span 树，未找到返回 404
- [ ] 8.15 测试 `main.py` OTel 设置在 `TRACING_ENABLED=false` 时被跳过
- [ ] 8.16 测试 Alembic 迁移创建具有正确模式和索引的 `traces` 表

## 9. 验证

- [x] 9.1 运行 `ruff check src/hecate/ tests/`——0 错误
- [x] 9.2 运行 `ruff format --check src/ tests/`——0 错误
- [x] 9.3 运行 `mypy src/`——0 错误
- [x] 9.4 运行 `python -m pytest tests/ -q`——所有测试通过
