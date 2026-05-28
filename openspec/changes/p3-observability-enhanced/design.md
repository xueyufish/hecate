## Context

P1 的可观测性只有基础日志和 LangFuse 集成。P3 需要完整链路追踪、成本归因、结构化日志、性能监控。

## Goals / Non-Goals

**Goals:**

1. 增强 LangFuse 集成 — Trace→Span→Generation 完整链路
2. 实现结构化日志 — JSON 格式，支持 ELK 集成
3. 实现 Prometheus 指标 — 请求量、延迟、错误率、Token 用量
4. 实现 Grafana Dashboard — 预置监控面板

**Non-Goals:**

1. 不实现 ELK 部署（用户自行部署）
2. 不实现 Grafana 部署（用户自行部署）

## Decisions

### D1: LangFuse 作为主要追踪后端

**选择**：扩展现有 LangFuse 集成

**理由**：
- P1 已集成
- 支持 Trace→Span→Generation 层级
- 成本追踪原生支持

### D2: Prometheus 作为指标后端

**选择**：prometheus-client 库

**理由**：
- Python 生态标准
- 与 Grafana 无缝集成
- 轻量级

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| 追踪增加延迟 | 异步上报 |
| 指标存储增长 | 设置 TTL |
| 日志量过大 | 采样策略 |
