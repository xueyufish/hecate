## 1. OTel Trace Bridge — SpanProcessor — OTel 追踪桥接 — SpanProcessor

- [x] 1.1 在 `core/config.py` 中添加配置设置：`TRACE_DB_EXPORT_ENABLED: bool = True`、`TRACE_DB_QUEUE_MAX_SIZE: int = 10000`、`TRACE_DB_FLUSH_INTERVAL: int = 5`
- [x] 1.2 创建 `src/hecate/services/observability/span_processor.py`，包含实现 `SpanProcessor` 接口（`on_start`、`on_end`、`shutdown`、`force_flush`）的 `HecateTraceSpanProcessor` 类
- [x] 1.3 实现 span 类型推断：`"tool:"` → `"tool"`、`"llm:"`/`"llm_stream:"` → `"generation"`、`"session:"` → `"trace"`，否则 → `"span"`
- [x] 1.4 实现异步队列 + 后台消费者：`__init__` 中的 `asyncio.Queue`、后台 `_consumer_loop()` 任务批量插入 TraceModel，通过 `force_flush()` 优雅关闭
- [x] 1.5 实现属性映射：OTel 属性 → `TraceModel.metadata_`（包括 `otel.trace_id` 和 `otel.span_id`）、输出属性 → `TraceModel.output_data`、usage 属性 → `TraceModel.usage`
- [x] 1.6 从 OTel 父 span 上下文提取 parent_id（如果存在则使用 `span.parent.span_id`）

## 2. OTel Trace Bridge — Registration & Root Span — OTel 追踪桥接 — 注册与根 Span

- [x] 2.1 当 `TRACE_DB_EXPORT_ENABLED=True` 时，在 `main.py` 启动中注册 `HecateTraceSpanProcessor`，与现有的 `ConsoleSpanExporter` 处理器一起
- [x] 2.2 在应用启动时启动后台消费者任务，在应用关闭时停止 + 刷新
- [x] 2.3 在 `PregelRuntime.execute()` 中添加根 span 创建：用 `tracer.start_as_current_span("session:{session_id}")` 包装执行体，附带 try/except 保护
- [x] 2.4 设置根 span 属性：`session.id`、`agent.id`（如果执行上下文中可用）
- [x] 2.5 在 ToolWorker 的 `create_span()` 调用中添加 `gen_ai.tool.name` 属性，与现有的 `tool_name` 并列
- [x] 2.6 在 LLMWorker 的 `create_span()` 调用中添加 `gen_ai.request.model` 属性，与现有的 `model` 并列

## 3. OTel Trace Bridge — Tests — OTel 追踪桥接 — 测试

- [x] 3.1 测试 `HecateTraceSpanProcessor.on_start()` 根据名称前缀正确推断类型创建 TraceModel
- [x] 3.2 测试 `on_end()` 更新 status（completed/error）、end_time、output_data、usage
- [x] 3.3 测试异步队列：入队 spans、运行消费者、验证 DB 记录
- [x] 3.4 测试队列满场景：丢弃 spans 并发出警告，不抛出异常
- [x] 3.5 测试 `force_flush()` 在关闭时排空队列
- [x] 3.6 测试 OTel trace_id/span_id 存储在 metadata_ 中
- [x] 3.7 测试 PregelRuntime 根 span 创建和子 span 嵌套（工具 span 父级 = 根 span）
- [x] 3.8 测试 PregelRuntime 在追踪禁用或失败时正常执行

## 4. Tool Analytics Service — 工具分析服务

- [x] 4.1 创建 `src/hecate/services/ops_center/tool_analytics.py`，包含 `ToolAnalyticsService` 类
- [x] 4.2 实现 `get_overview(start_date, end_date, agent_id=None)` → 聚合指标（total、success_rate、avg_latency、p95_latency、unique_tools、error_count）
- [x] 4.3 实现 `get_tool_details(tool_name, start_date, end_date)` → 每个工具的指标 + 前 5 个错误
- [x] 4.4 实现 `get_trends(granularity, days, tool_name=None)` → 时间序列数据点
- [x] 4.5 实现 `get_top_errors(limit, tool_name=None, start_date, end_date)` → 排序的错误条目

## 5. Tool Analytics API — 工具分析 API

- [x] 5.1 创建 `src/hecate/api/management/tool_analytics.py` 路由器，前缀为 `/api/ops-center/tools`
- [x] 5.2 实现 `GET /overview` 端点（start_date、end_date、agent_id 查询参数）
- [x] 5.3 实现 `GET /{tool_name}` 端点（每个工具的详细信息，未找到返回 404）
- [x] 5.4 实现 `GET /trends` 端点（granularity、days、tool_name 参数）
- [x] 5.5 实现 `GET /errors` 端点（limit、tool_name、日期范围参数）
- [x] 5.6 在 `main.py` 中注册路由器

## 6. Tool Analytics Tests — 工具分析测试

- [x] 6.1 使用填充的 TraceModel 数据测试 `get_overview()`（验证 success_rate、p95、unique_tools）
- [x] 6.2 使用空数据测试 `get_overview()`（返回零值，success_rate=1.0）
- [x] 6.3 使用 agent_id 过滤器测试 `get_overview()`
- [x] 6.4 测试 `get_tool_details()` 返回每个工具的指标和顶级错误
- [x] 6.5 测试 `get_tool_details()` 对未知工具返回 404
- [x] 6.6 测试 `get_trends()` 返回每日/每小时粒度的正确数据点数
- [x] 6.7 测试 `get_top_errors()` 的排序和限制

## 7. Frontend — Tool Analytics Dashboard — 前端 — 工具分析仪表板

- [x] 7.1 创建 `web/src/app/(dashboard)/ops-center/tools/page.tsx`，包含概览卡片（总执行次数、成功率、P95 延迟、错误计数）
- [x] 7.2 添加每个工具的成功率柱状图（重用 `components/ui/bar-chart.tsx` 中的 `BarChart` 组件）
- [x] 7.3 添加工具详细表（工具名称、执行次数、成功率、平均延迟、最后使用时间 — 可排序）
- [x] 7.4 添加顶级错误列表，包含工具名称、错误消息、计数、时间戳
- [x] 7.5 添加时间范围选择器（7d / 30d / 自定义）和空状态处理
- [x] 7.6 在 `web/src/components/sidebar.tsx` 中添加 "Ops Center" 入口，链接到 `/ops-center/tools`

## 8. Verification — 验证

- [x] 8.1 运行 `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 错误
- [x] 8.2 运行 `mypy src/` — 0 错误
- [x] 8.3 运行 `python -m pytest tests/test_observability/ tests/test_ops_center/ -q` — 全部通过
- [x] 8.4 运行前端测试：`cd web && npx vitest run` — 全部通过
- [x] 8.5 端到端验证：触发一个工具执行，确认 TraceModel 记录以正确的 type/status/parent 出现
