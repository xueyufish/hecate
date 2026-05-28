## Why

P1 的可观测性只有基础日志和 LangFuse 集成，缺乏完整链路追踪、成本归因、结构化日志、性能监控。P3 需要企业级可观测体系。

## What Changes

- **增强 LangFuse 集成**：Trace→Span→Generation 完整链路，成本归因分析
- **新增 `StructuredLogger`**：JSON 结构化日志，支持 ELK 集成
- **新增 Prometheus 指标**：请求量、延迟、错误率、Token 用量
- **新增 Grafana Dashboard**：预置监控面板
- **扩展 `EvidenceTracker`**：与 LangFuse Trace 关联

## Capabilities

### New Capabilities

- `enhanced-tracing`: LangFuse 完整链路追踪 + 成本归因
- `structured-logging`: JSON 结构化日志 + ELK 集成
- `prometheus-metrics`: Prometheus 指标采集
- `grafana-dashboard`: 预置监控面板

### Modified Capabilities

（无）

## Impact

- **新增代码**：`services/observability/` 目录，约 600-800 行
- **新增依赖**：`prometheus-client`（可选）
- **修改代码**：`services/conversation.py`（集成追踪）
- **架构影响**：可观测性成为横切关注点
