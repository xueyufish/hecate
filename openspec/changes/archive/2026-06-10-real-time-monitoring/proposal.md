## Why — 原因

Hecate 已经拥有全链路追踪（8.1）将每个追踪和 span 记录到 PostgreSQL，但运维人员无法实时观察系统健康状态。没有错误率、延迟百分位数、活动会话和 token 消耗的实时可见性，生产事故会一直持续到用户投诉才被发现。行业标准平台（Grafana、Datadog、LangFuse）都提供实时监控仪表板——Hecate 也需要一个。

## What Changes — 变更内容

- 在引擎层添加 **MetricsStore ABC**，提供两个实现：`InMemoryMetricsStore`（默认，零依赖）和 `TimescaleMetricsStore`（可选，当 TimescaleDB 扩展可用时使用 PostgreSQL `time_bucket`）。遵循现有的 CheckpointStore 双实现模式。
- 增强现有的 `MetricsCollector`，增加**时间窗口聚合**（最近 1m/5m/15m/1h 的滑动窗口）、**百分位延迟**（p50/p95/p99）和**按维度细分**（按代理、模型、会话）。
- 添加 **WebSocket 端点**（`/ws/monitoring`），每 5 秒向连接的仪表板客户端推送聚合指标快照，使用 FastAPI 内置的 WebSocket 支持，配合 `ConnectionManager` 实现多客户端广播。
- 添加 **REST API**（`GET /api/monitoring/metrics`）用于按需指标查询，支持可配置的时间窗口和维度，服务于偏好轮询而非 WebSocket 的客户端。
- 将 `MetricsCollector` 接入追踪完成路径，使每次 `end_span` 调用自动更新计数器、直方图和计量器，无需额外插装。
- 添加 `METRICS_STORE_TYPE` 配置标志（`"in_memory"` | `"timescale"`）和工厂函数，遵循 `VECTOR_STORE_TYPE` 模式。

## Capabilities — 能力

### New Capabilities — 新增能力
- `real-time-monitoring`：基于 WebSocket 的实时指标流式传输、时间窗口聚合（InMemory + TimescaleDB）、REST 指标查询 API 和仪表板客户端的连接管理

### Modified Capabilities — 修改的能力
- `core-infrastructure`：向核心配置添加 `METRICS_STORE_TYPE` 和 `METRICS_PUSH_INTERVAL` 设置；在 main.py 中添加监控 WebSocket 路由和 REST 端点注册
- `full-chain-tracing`：将 TracingService.end_span 接入 MetricsStore，在每个 span 完成时更新计数器和直方图

## Impact — 影响

- **Engine layer**：`engine/metrics_store.py` 中的新 `MetricsStore` ABC + `InMemoryMetricsStore`（零外部依赖）
- **Services layer**：`services/observability/monitoring.py` 中的新 `MonitoringService`；`services/observability/timescale_metrics_store.py` 中的 `TimescaleMetricsStore`；增强的带时间窗口的 `MetricsCollector`
- **API layer**：`api/management/monitoring.py` 中的新 WebSocket 端点 + REST 端点；`main.py` 中的路由注册
- **Config**：`core/config.py` 中的两个新设置
- **Dependencies**：无新的必需依赖。可选：用于生产 TSDB 模式的 TimescaleDB PostgreSQL 扩展
- **Tests**：针对 MetricsStore ABC、InMemoryMetricsStore、MonitoringService 和 WebSocket 端点的新测试文件
