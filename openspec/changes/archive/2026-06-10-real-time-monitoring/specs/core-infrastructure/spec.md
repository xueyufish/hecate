## MODIFIED Requirements — 修改的需求

### 需求：从环境变量和 .env 文件加载设置
`Settings` 类（pydantic-settings）应包含监控相关的设置：`METRICS_STORE_TYPE`（默认 `"in_memory"`，值：`"in_memory"` | `"timescale"`）、`METRICS_PUSH_INTERVAL`（默认 `5`，秒）和 `MAX_METRICS_BUFFER_SIZE`（默认 `100000`，每个环形缓冲区的最大条目数）

#### 场景：默认监控配置
- **当** 未设置监控相关的环境变量
- **则** `METRICS_STORE_TYPE="in_memory"`、`METRICS_PUSH_INTERVAL=5`、`MAX_METRICS_BUFFER_SIZE=100000`

#### 场景：启用 TimescaleDB 指标存储
- **当** `METRICS_STORE_TYPE=timescale`
- **则** 应使用 `TimescaleMetricsStore`，连接到配置的 `DATABASE_URL`

### 需求：带 OpenTelemetry 插装的 FastAPI 应用入口点
`main.py` 模块应注册监控 WebSocket 路由和 REST 端点，并在应用生命周期期间启动/停止 `MonitoringService`

#### 场景：启动时注册监控路由
- **当** FastAPI 应用启动时
- **则** `/ws/monitoring` WebSocket 路由和 `/api/monitoring/metrics` REST 端点应可访问

#### 场景：生命周期中的 MonitoringService 生命周期
- **当** 应用生命周期启动时
- **则** 应调用 `MonitoringService.start()`；在生命周期关闭时，应调用 `MonitoringService.stop()`
