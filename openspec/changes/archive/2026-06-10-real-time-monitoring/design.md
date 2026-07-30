## Context — 上下文

Hecate 已完成 8.1 全链路追踪，通过 `TracingService` 将追踪/span 记录写入 PostgreSQL 的 `traces` 表。存在一个内存 `MetricsCollector`（`services/observability/metrics.py`），但没有时间窗口聚合、没有百分位计算，且未通过任何 API 暴露。除了 `api/v1/chat.py` 中的 OpenAI 兼容聊天流式传输外，没有 WebSocket 或 SSE 端点。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 通过 WebSocket 每 5 秒推送实时指标快照（错误率、延迟百分位数、活动会话、token 使用量）
- 通过 REST API 提供按需指标查询，带可配置的时间窗口
- 支持双模式存储：内存（默认，零依赖）和 TimescaleDB（可选，用于生产持久化）
- 从追踪完成自动更新指标——除了 8.1 已提供的之外，无需额外插装
- 遵循现有的 Hecate 模式（引擎层中的 ABC，服务层中的实现，基于配置的工厂选择）

**非目标：**
- 构建前端仪表板 UI（这只是后端）
- 支持告警/阈值（属于特性 8.6，依赖于此）
- 支持 ClickHouse 或其他 TSDB 后端（目前仅 TimescaleDB 和内存）
- 跨服务器重启持久化内存指标
- 在此迭代中支持按用户/按工作区的指标隔离

## Decisions — 决策

### D1：用于仪表板流式传输的 WebSocket 优于 SSE

**决策**：使用 FastAPI 原生 WebSocket（`/ws/monitoring`）进行实时指标推送

**理由**：Grafana（Centrifuge）和 Datadog 都使用 WebSocket 进行仪表板流式传输。WebSocket 支持双向通信（客户端可以订阅/取消订阅特定指标频道）和复用订阅。FastAPI >= 0.115 具有原生 WebSocket 支持——零额外依赖

**考虑的替代方案**：
- SSE：更简单但单向；无复用订阅；自动重连是唯一优势，但 WebSocket 客户端同样能很好地处理
- 轮询：可行但 30-60s 的延迟不足以用于实时调试

### D2：带双实现的 MetricsStore ABC（InMemory + TimescaleDB）

**决策**：在 `engine/metrics_store.py` 中定义 `MetricsStore` ABC，附带 `InMemoryMetricsStore`（同一文件，零依赖）和 `services/observability/timescale_metrics_store.py` 中的 `TimescaleMetricsStore`。配置标志 `METRICS_STORE_TYPE` 通过工厂函数选择实现

**理由**：遵循 `CheckpointStore`（引擎层 ABC + InMemory）和 `PostgresCheckpointStore`（服务层实现）以及 `VectorStore`（基于配置的工厂）使用的完全相同的模式。InMemory 用于开发/测试，TimescaleDB 用于生产

**考虑的替代方案**：
- 仅内存：丢失历史数据，无法查询趋势
- 仅 TimescaleDB：对扩展增加硬依赖，破坏开发/测试
- ClickHouse：对于此阶段过于庞大，会增加新的服务依赖

### D3：带可配置时间桶的滑动窗口聚合

**决策**：InMemoryMetricsStore 为固定时间窗口（1m、5m、15m、1h）维护环形缓冲区。每个指标事件追加到所有适用的窗口。窗口过期并被垃圾回收

**理由**：环形缓冲区提供 O(1) 追加和 O(n) 扫描，其中 n 是窗口中的事件数。对于 1000 req/s 下的 5 秒快照，1m 窗口最多持有 60K 条记录——完全在内存预算内。TimescaleMetricsStore 使用 SQL `time_bucket()` 进行服务器端聚合

### D4：通过后台任务实现 5 秒推送间隔

**决策**：`MonitoringService` 运行一个 `asyncio.Task`，每 5 秒查询 MetricsStore 并将快照广播到所有连接的 WebSocket 客户端

**理由**：Datadog 实时模式使用 2s 间隔但限制 50 台主机。Grafana 在数据变更时使用实时推送。5s 是新鲜度和数据库/网络负载之间的平衡。对于内存存储，查询是 O(1)——无需担心。对于 TimescaleDB，查询命中预聚合的物化视图

### D5：仅在追踪/span 完成时更新指标

**决策**：接入 `TracingService.end_span()` 以调用 `MetricsStore.record_*()`。不对热路径（LLM 调用、工具执行）做任何更改——仅在完成路径上

**理由**：最小化性能影响。追踪完成已经是异步数据库写入；添加内存计数器增量可以忽略不计。这避免触及 `PregelRuntime`、`LLMWorker` 或 `ToolWorker`——所有指标都来自现有的追踪数据

### D6：带优雅关闭的 WebSocket 连接管理器

**决策**：`ConnectionManager` 类维护活动连接的 `set[WebSocket]`。广播遍历冻结副本，按客户端捕获 `WebSocketDisconnect`，并移除过期连接。在应用关闭时，发送最终的 `{\"type\": \"shutdown\"}` 消息并关闭所有连接

**理由**：FastAPI 文档中使用的标准模式。冻结副本防止迭代期间的突变。按客户端错误处理防止一个断开的连接阻塞整个广播

## Risks / Trade-offs — 风险 / 权衡

| 风险 | 影响 | 缓解措施 |
|------|--------|------------|
| InMemoryMetricsStore 重启丢失数据 | 中 | 记录限制；生产持久化使用 TimescaleDB 模式 |
| TimescaleDB 扩展未安装 | 低 | 优雅降级到内存；`METRICS_STORE_TYPE` 默认为 `"in_memory"` |
| WebSocket 连接扩展（1000 以上并发仪表板） | 当前低 | 内存广播是 O(n) 连接数；为了扩展，稍后添加 Redis pub/sub 后端 |
| 5 秒推送间隔可能感觉缓慢 | 低 | 可通过 `METRICS_PUSH_INTERVAL` 配置；如果需要可以降低到 2s |
| 高负载下环形缓冲区内存使用 | 低 | 10K req/s 下 1m 窗口 = 600K 条目 ≈ 50MB；添加 `MAX_METRICS_BUFFER_SIZE` 上限 |
