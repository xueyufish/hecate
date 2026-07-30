## 1. 配置和数据模型

- [x] 1.1 向 `src/hecate/core/config.py` 添加 `METRICS_STORE_TYPE`（默认 `"in_memory"`，值 `"in_memory"` | `"timescale"`）、`METRICS_PUSH_INTERVAL`（默认 `5`）和 `MAX_METRICS_BUFFER_SIZE`（默认 `100000`）设置
- [x] 1.2 在 `src/hecate/models/metric.py` 中创建 `MetricsModel` ORM 模型，字段包括：`id`、`timestamp`、`name`、`kind`（counter/gauge/histogram）、`value`（float）、`tags`（JSONB）；在 `(name, timestamp)` 上创建复合索引，在文档字符串中标注 TimescaleDB 超表提示
- [x] 1.3 将 `MetricsModel` 添加到 `src/hecate/models/__init__.py` 导出，并在 `tests/conftest.py` 中为表注册导入

## 2. 引擎层——MetricsStore ABC

- [x] 2.1 在 `src/hecate/engine/metrics_store.py` 中创建 `MetricsStore` ABC，带抽象方法：`record_counter(name, value, tags)`、`record_gauge(name, value, tags)`、`record_histogram(name, value, tags)`、`query_metrics(name, window, aggregation, tags)`、`get_snapshot(windows) -> MetricsSnapshot`
- [x] 2.2 定义 `MetricsSnapshot` Pydantic 模型，字段包括：`timestamp`（datetime）、`windows`（窗口名到指标字典的映射）、`counters`、`gauges`、`histograms`
- [x] 2.3 在同一文件中实现 `InMemoryMetricsStore`，使用按时间窗口（1m、5m、15m、1h）组织的环形缓冲区，在快照查询时进行垃圾回收，并设置 `MAX_METRICS_BUFFER_SIZE` 上限

## 3. 服务层——MonitoringService

- [x] 3.1 在 `src/hecate/services/observability/monitoring.py` 中创建 `ConnectionManager` 类，带方法：`connect(websocket)`、`disconnect(websocket)`、`broadcast(message)`、`shutdown()`；在广播时迭代冻结副本，按客户端捕获断开连接
- [x] 3.2 创建 `MonitoringService` 类，带方法：`start()`、`stop()`、`get_metrics_store()`、`_push_loop()`（异步后台任务，每 METRICS_PUSH_INTERVAL 秒查询 MetricsStore.get_snapshot 并通过 ConnectionManager 广播）
- [x] 3.3 在 `src/hecate/services/observability/timescale_metrics_store.py` 中实现 `TimescaleMetricsStore`，使用 `async_sessionmaker` 进行自管理事务，使用 `time_bucket()` 进行聚合，当 TimescaleDB 未安装时回退到 `date_trunc()`
- [x] 3.4 在 `src/hecate/services/observability/monitoring.py` 中创建 `get_metrics_store()` 工厂函数，使用 `METRICS_STORE_TYPE` 配置配合 match/case，在 case 块内部导入具体实现

## 4. API 层

- [x] 4.1 在 `src/hecate/api/management/monitoring.py` 中创建 WebSocket 端点 `ws_monitoring()`，路径为 `/ws/monitoring`，接受连接，处理 subscribe/ping 动作，并从 MonitoringService 发送周期性快照
- [x] 4.2 在同一文件中创建 REST 端点 `get_metrics()`，路径为 `GET /api/monitoring/metrics`，接受查询参数：`name`、`names`、`window`、`aggregation`、`agent_id`、`session_id`；委托给 MetricsStore.query_metrics
- [x] 4.3 在 `src/hecate/main.py` 中注册监控路由和 WebSocket 路由；将 MonitoringService 生命周期（start/stop）接入应用生命周期上下文管理器

## 5. 集成——将 TracingService 接入 MetricsStore

- [x] 5.1 更新 `src/hecate/services/observability/tracing.py` 中的 `TracingService.end_span()`，接受可选的 `metrics_store: MetricsStore | None = None` 参数；当提供时，调用 `record_counter("requests_total")`、`record_histogram("request_latency_ms")`、错误时调用 `record_counter("errors_total")`，存在使用数据时调用 `record_counter("input_tokens"/"output_tokens")`
- [x] 5.2 在应用生命周期初始化期间将 MonitoringService 中的 MetricsStore 实例接入 TracingService

## 6. 测试

- [x] 6.1 测试 `MetricsStore` ABC 不可实例化；`InMemoryMetricsStore` 实现所有抽象方法
- [x] 6.2 测试 `InMemoryMetricsStore` 跨时间窗口累积计数器、计量器和直方图
- [x] 6.3 测试 `InMemoryMetricsStore` 在快照时垃圾回收过期条目并强制 `MAX_METRICS_BUFFER_SIZE` 上限
- [x] 6.4 测试 `InMemoryMetricsStore.get_snapshot()` 为每个窗口返回正确的聚合（sum、count、p50/p95/p99）
- [x] 6.5 测试 `ConnectionManager` 处理连接、断开、广播和过期连接清理
- [x] 6.6 测试 `TimescaleMetricsStore` 使用测试 session_factory 持久化和查询指标（带 date_trunc 的 SQLite 回退）
- [x] 6.7 测试 WebSocket 端点接受连接、响应 ping、并至少接收一个指标快照
- [x] 6.8 测试 REST 端点 `GET /api/monitoring/metrics` 返回正确的指标值及查询过滤器
- [x] 6.9 测试 `TracingService.end_span()` 在配置时更新 MetricsStore 计数器和直方图；当 MetricsStore 为 None 时正常完成
- [x] 6.10 测试 `get_metrics_store()` 工厂默认返回 InMemoryMetricsStore，对不支持的类型抛出 ValueError

## 7. 验证

- [x] 7.1 运行 `ruff check src/hecate/ tests/`——0 错误
- [x] 7.2 运行 `ruff format --check src/ tests/`——0 错误
- [x] 7.3 运行 `mypy src/`——0 错误
- [x] 7.4 运行 `python -m pytest tests/ -q`——所有测试通过（1564 通过）
