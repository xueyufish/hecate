## Why — 为什么

Hecate 拥有全链路追踪（8.1）、实时监控（8.2）和成本仪表板（8.3），但无法在出现问题时主动通知操作员。如今，问题只有在人工手动检查仪表板时才能被发现。告警系统填补了这一空白——自动评估针对追踪数据的阈值和预算预测规则，并通过企业 IM 通道（飞书/企业微信/钉钉/Slack）、邮件和应用内 WebSocket 发送通知。这完成了观测栈（8.x），并且是任何企业级平台的硬性要求。

## What Changes — 变更内容

- **添加 AlertRuleModel** — 每工作空间规则，包含 8 种告警类型（error_rate、latency_p95、latency_ttft、token_usage、cost_daily、cost_monthly_forecast、tool_failure_rate、success_rate）、阈值、评估窗口、`for` 持续时间、严重性和范围过滤器（agent_id、model）。
- **添加 AlertEventModel** — 告警实例，具有 Grafana 标准状态机：PENDING → FIRING → RESOLVED，加上手动 ACK。
- **添加 AlertSilenceModel** — 维护窗口，在时间范围内抑制指定规则或严重性的通知。
- **添加 EscalationPolicyModel** — 多步升级（例如，步骤 0：webhook，15 分钟后步骤 1：SMS，30 分钟后步骤 2：电话）带重复间隔。
- **添加 NotificationChannelModel** — 可配置的通知目标，包含 7 种通道类型（webhook_feishu、webhook_wecom、webhook_dingtalk、webhook_slack、webhook_generic、websocket、email）以及每个 IM 平台的内置消息模板。
- **添加 AlertEvaluator** — asyncio 后台任务，带 PostgreSQL 建议锁，每 60 秒运行一次，查询 TraceModel/CostService，评估规则，管理状态转换，并发送通知。
- **添加 SignalProvider 注册表** — 可插拔信号源，每个 AlertType 一个，每个查询 TraceModel 或 CostService 获取相关指标。
- **添加 NotificationDispatcher** — 将触发事件通过升级策略在正确的时间路由到正确的通道，为飞书（卡片）、企业微信（markdown）、钉钉（markdown）、Slack（Block Kit）和邮件（HTML）提供内置消息模板。
- **在 LLMWorker 中检测 TTFT** — 在流式 LLM 调用期间将 `ttft_ms` 和 `total_latency_ms` 写入 TraceModel metadata 以支持 latency_ttft 告警类型。
- **添加 5 个 API 路由器** — `/api/alerts/rules`、`/api/alerts/events`、`/api/alerts/silences`、`/api/alerts/channels`、`/api/alerts/escalation-policies` 带完整 CRUD。
- **添加告警配置设置** — ALERT_EVAL_INTERVAL、SMTP 连接设置、默认升级策略。

## Capabilities — 能力

### New Capabilities — 新增能力

- `alerting`：告警规则管理、评估引擎、通知发送、升级策略、静默窗口和告警事件生命周期。

### Modified Capabilities — 修改的能力

- `full-chain-tracing`：LLMWorker 在流式响应期间将 `ttft_ms` 检测到 TraceModel metadata 中。

## Impact — 影响

- **新文件**：`models/alert.py`（5 个模型 + 4 个枚举 + 模式）、`services/alert_service.py`、`services/alert_evaluator.py`、`services/notification_dispatcher.py`、`services/signal_provider.py`、`api/management/alerts.py`（5 个路由器）、`alembic/versions/xxxx_add_alerting.py`。
- **修改的文件**：`engine/workers/llm_worker.py`（TTFT 检测）、`main.py`（路由器注册 + 评估器启动）、`core/config.py`（告警设置）、`tests/conftest.py`（模块导入）。
- **新依赖**：`aiosmtplib`（邮件通道，`[observability]` 组）。
- **数据库**：5 个新表（alert_rules、alert_events、alert_silences、escalation_policies、notification_channels）带索引和一个种子默认升级策略。
- **API**：`/api/alerts/` 下的 5 个新路由器组。
- **后台**：在应用程序生命周期中启动的新 asyncio 任务用于告警评估。
