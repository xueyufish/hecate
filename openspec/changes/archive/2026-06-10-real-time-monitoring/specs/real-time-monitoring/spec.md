## Purpose — 目的
定义 Hecate 的实时监控能力——基于 WebSocket 的指标流式传输、带双模式存储（InMemory + TimescaleDB）的时间窗口聚合，以及 REST 指标查询 API。

## Requirements — 需求

### 需求：MetricsStore ABC 定义指标持久化接口
引擎应定义一个 `MetricsStore` ABC，带有用于记录和查询时间窗口指标的抽象方法。应提供两个实现：`InMemoryMetricsStore`（默认，零外部依赖）和 `TimescaleMetricsStore`（可选，需要 TimescaleDB 扩展）

#### 场景：记录计数器指标
- **当** 调用 `record_counter(name="requests_total", value=1, tags={"agent_id": "abc"})`
- **则** 计数器应在指定的时间桶中按给定值递增

#### 场景：记录延迟的直方图指标
- **当** 调用 `record_histogram(name="request_latency_ms", value=150.3, tags={"endpoint": "/v1/chat"})`
- **则** 该值应追加到当前时间桶的直方图中

#### 场景：记录计量器指标
- **当** 调用 `record_gauge(name="active_sessions", value=42, tags={})`
- **则** 计量器应设为给定值，替换任何先前的值

#### 场景：查询时间窗口的指标
- **当** 调用 `query_metrics(name="requests_total", window="5m", aggregation="sum")`
- **则** 应返回最近 5 分钟的聚合指标值

#### 场景：查询百分位延迟
- **当** 调用 `query_metrics(name="request_latency_ms", window="5m", aggregation="p95")`
- **则** 应返回最近 5 分钟的 95 百分位延迟值

#### 场景：获取所有指标的完整快照
- **当** 调用 `get_snapshot(windows=["1m", "5m", "15m"])`
- **则** 应返回包含每个请求窗口的所有计数器、计量器和直方图聚合的 `MetricsSnapshot`

### 需求：InMemoryMetricsStore 提供零依赖默认实现
`InMemoryMetricsStore` 应使用按时间窗口（1m、5m、15m、1h）组织的环形缓冲区。每个指标事件追加到所有适用的窗口。过期数据应在每次快照查询时进行垃圾回收

#### 场景：指标在时间窗口中累积
- **当** 2 分钟内记录了 100 次计数器递增
- **则** 1m 窗口仅包含过去 60 秒内的事件，而 5m 窗口包含所有 100 个事件

#### 场景：过期数据被垃圾回收
- **当** 请求快照时，1m 环形缓冲区有超过 60 秒的条目
- **则** 这些条目应在快照计算期间被移除

#### 场景：内存上限强制
- **当** 任何单个环形缓冲区中的条目数超过 `MAX_METRICS_BUFFER_SIZE`（默认 100,000）
- **则** 最旧的条目应被逐出以保持在上限内

### 需求：TimescaleMetricsStore 使用 PostgreSQL time_bucket 进行聚合
`TimescaleMetricsStore` 应将指标持久化到 `metrics` 表（或超表），并使用 TimescaleDB 的 `time_bucket()` 函数进行服务器端聚合。当 TimescaleDB 未安装时，应回退到标准 PostgreSQL date_trunc 聚合

#### 场景：将指标持久化到数据库
- **当** 调用 `record_counter(name="requests_total", value=1, tags={"agent_id": "abc"})`
- **则** 应插入一行到 metrics 表，包含时间戳、名称、值和标签

#### 场景：使用 time_bucket 聚合查询
- **当** 调用 `query_metrics(name="requests_total", window="5m", aggregation="sum")` 且 TimescaleDB 可用
- **则** 应使用 `time_bucket('5 minutes', timestamp)` 进行分组

#### 场景：无 TimescaleDB 时的回退
- **当** TimescaleDB 扩展未安装
- **则** 存储应使用 `date_trunc('minute', timestamp)` 作为回退，结果等效

### 需求：WebSocket 端点推送指标快照
系统应暴露一个 WebSocket 端点 `/ws/monitoring`，接受来自仪表板客户端的连接，每 5 秒推送一次 `MetricsSnapshot` JSON（可通过 `METRICS_PUSH_INTERVAL` 配置）

#### 场景：客户端连接并接收快照
- **当** WebSocket 客户端连接到 `/ws/monitoring`
- **则** 应每 `METRICS_PUSH_INTERVAL` 秒接收包含所有指标窗口的 JSON 快照

#### 场景：客户端订阅特定指标
- **当** 已连接客户端发送 `{"action": "subscribe", "metrics": ["error_rate", "p95_latency"]}`
- **则** 后续快照应仅包含请求的指标

#### 场景：客户端发送 ping
- **当** 已连接客户端发送 `{"action": "ping"}`
- **则** 服务器应以 `{"type": "pong"}` 响应

#### 场景：服务器关闭通知
- **当** 应用正在关闭
- **则** 服务器应在关闭连接前向所有已连接客户端发送 `{"type": "shutdown"}`

#### 场景：过期连接清理
- **当** WebSocket 客户端未发送关闭帧即断开连接
- **则** 连接应在下次发送尝试失败时从活动集中移除

### 需求：按需指标查询的 REST API
系统应暴露 `GET /api/monitoring/metrics` 端点，接受指标名称、时间窗口、聚合函数和标签过滤器的查询参数

#### 场景：查询特定指标
- **当** 调用 `GET /api/monitoring/metrics?name=requests_total&window=5m&aggregation=sum`
- **则** 应返回最近 5 分钟的聚合指标值

#### 场景：使用标签过滤器查询
- **当** 调用 `GET /api/monitoring/metrics?name=requests_total&window=15m&aggregation=sum&agent_id=abc`
- **则** 应返回按指定 agent_id 标签过滤的总和

#### 场景：同时查询多个指标
- **当** 调用 `GET /api/monitoring/metrics?names=requests_total,error_count&window=5m`
- **则** 应在单个响应中返回两个指标

#### 场景：列出可用指标
- **当** 在无 `name` 或 `names` 参数的情况下调用 `GET /api/monitoring/metrics`
- **则** 应返回所有可用指标名称列表及其跨所有窗口的当前值

### 需求：MonitoringService 编排指标收集和推送
`MonitoringService` 应管理 MetricsStore、WebSocket ConnectionManager 和后台推送任务。应在应用生命周期启动期间初始化，并在关闭时优雅停止

#### 场景：服务随应用启动
- **当** FastAPI 应用启动时
- **则** `MonitoringService` 应初始化配置的 MetricsStore 并启动后台推送任务

#### 场景：服务优雅停止
- **当** FastAPI 应用关闭时
- **则** 推送任务应被取消，应向所有 WebSocket 客户端发送关闭消息，所有连接应被关闭

#### 场景：推送任务广播快照
- **当** 推送任务计时器触发（每 `METRICS_PUSH_INTERVAL` 秒）
- **则** 应调用 `MetricsStore.get_snapshot()`，序列化为 JSON，并广播到所有已连接的 WebSocket 客户端
