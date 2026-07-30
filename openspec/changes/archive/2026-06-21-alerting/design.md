## Context — 背景

Hecate 的观测堆栈目前提供追踪（8.1）、实时监控（8.2）、成本仪表板（8.3）和审计日志（8.7），但操作员必须手动检查仪表板才能发现问题。对 Dify、Coze、LangFuse、LiteLLM、Grafana/Prometheus 和 Datadog 的竞品分析显示，告警是企业平台的基本期望。

现有基础设施提供了坚实的基础：`TraceModel` 记录每个 span 的状态、延迟、token 使用量和模型元数据；`CostService` 从追踪中聚合成本；`MonitoringService` 演示了带有 WebSocket 推送的 asyncio 后台任务模式；`ScheduleManager` 展示了用于多节点安全的建议锁用法。此变更在这些现有组件之上构建告警功能。

关键约束：PostgreSQL 是主要数据存储（建议锁可用）；无 Redis 或 Celery；`[scheduling]` 依赖组是可选的，不能成为必需；所有代码遵循 models → services → api 分层，engine 层不从 services/api 导入。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 8 种告警类型，覆盖完整目录描述（错误率、延迟 p95、TTFT、token 使用量、每日成本、月度成本预测、工具失败率、成功率）。
- Grafana 标准告警生命周期：PENDING → FIRING → RESOLVED，带手动 ACK。
- 三级严重性升级：CRITICAL（webhook + SMS）、WARNING（webhook + 应用内）、INFO（应用内 + 邮件）。
- 飞书（卡片）、企业微信（markdown）、钉钉（markdown）、Slack（Block Kit）和邮件（HTML）的内置消息模板。
- 维护窗口静默，在计划停机期间抑制通知。
- 通过 PostgreSQL 建议锁实现多节点安全评估。
- 每工作空间规则隔离，带可选范围过滤器（agent_id、model）。

**非目标：**
- 异常检测（统计基线、z-score、EWMA 偏差）——推迟到未来的 8.6a 功能。v1 仅为阈值 + 预测。
- 基于表达式的规则引擎（PromQL 或自定义 DSL）——规则使用固定的 AlertType 枚举带结构化参数。
- SMS 和电话通道——v1 提供 webhook、WebSocket 和邮件。SMS/电话需要电信提供商集成（Twilio/阿里云 SMS）并推迟。
- 跨规则告警关联或去重——每个规则独立评估。
- 实时流式评估——v1 使用定期轮询（60 秒默认）。流式是未来的优化。

## Decisions — 决策

### D1: 固定 AlertType 枚举而非 PromQL 表达式引擎

规则使用固定枚举（`AlertType`）带结构化参数（`threshold`、`window_minutes`、`for_minutes`、`filters`），而不是像 PromQL 这样的查询语言表达式。

**考虑的替代方案：**
- **PromQL 引擎**（Dify/Grafana 模式）：极其灵活，但需要实现查询解析器、表达式评估器和时序数据库适配器。对于一个有意义的信号集合有限且已知的 LLM 平台来说，这是不合理的复杂性。
- **JSON 表达式树**：JSON 格式的迷你 DSL（例如 `{op: "gt", left: {metric: "error_rate", window: "5m"}, right: 0.05}`）。仍然需要表达式评估器，并且在信号类型可枚举时相比固定枚举没有实际优势。

**理由：** 所有 LLM 原生平台（Coze、LiteLLM、Helicone）都使用固定告警类型。PromQL 是基础设施监控平台（Grafana）的模式，其指标是开放式的。Hecate 的信号是一个封闭集合（8 种类型），所以固定枚举更简单、类型安全且 API 友好。新类型通过扩展枚举和注册新的 SignalProvider 来添加。

### D2: Grafana 标准四状态生命周期

告警事件遵循：`OK → PENDING → FIRING → RESOLVED → OK`，加上 `ACKED` 作为手动确认事件的终态。

**考虑的替代方案：**
- **两状态（OK/FIRING）**：阈值突破时立即触发。风险来自瞬时尖峰导致的告警疲劳（凌晨 3 点 10 秒的错误爆发无缘无故呼叫值班人员）。Dify 和 LiteLLM 通过外部依赖 Prometheus 的 `for` 子句来规避此问题，但由于我们自己拥有评估器，应该内置此功能。
- **无 ACK 的三状态**（OK/PENDING/FIRING）：失去跟踪人工响应的能力。企业工作流需要 ACK 来停止升级。

**理由：** `for` 持续时间（待定期）被 Grafana、Prometheus、Datadog 和 Alertmanager 普遍采用。它是最重要的降噪机制。ACK 是 PagerDuty/Opsgenie 的标准，防止升级无限运行。

### D3: 专用 asyncio 评估器 + PG 建议锁

告警评估器作为专用的 `asyncio.Task` 在 FastAPI 生命周期中启动，而不是通过现有的 `ScheduleManager`（APScheduler）。

**考虑的替代方案：**
- **复用 ScheduleManager**：已有建议锁支持和 cron 调度。但它需要 `[scheduling]` 可选依赖组（apscheduler + croniter）。使告警依赖调度会创建不必要的耦合——即使未安装调度，告警也应工作。
- **Temporal 工作流**：Hecate 已经对一些路径使用 Temporal。周期性的 Temporal 工作流可以评估告警。但 Temporal 对于简单的定期轮询来说过于重量级，而且 Temporal 本身在可选依赖组中。
- **APScheduler 独立**：当 asyncio 可以原生完成时，这为告警添加了新的依赖。

**理由：** `MonitoringService._push_loop()` 已经确立了在应用程序生命周期中使用 asyncio 后台任务的模式。评估器遵循相同的模式。PG 建议锁（`pg_try_advisory_lock`）确保在多工作节点部署中只有一个节点评估——大约 15 行 SQL。默认间隔为 60 秒，可通过 `ALERT_EVAL_INTERVAL` 配置。

### D4: TraceModel 作为单一信号源

所有 8 种信号类型查询 `TraceModel`（以及成本信号的 `CostService`），而不是内存中的 `MetricsCollector`。

**考虑的替代方案：**
- **MetricsCollector（内存中）**：快速，但短暂——重启时数据丢失。无法在历史窗口上评估规则。不是多进程安全的（每个工作节点有自己的收集器）。不适合规则跨越 5-30 分钟窗口的告警。
- **双源混合**：对于次分钟级告警使用 MetricsCollector，对于窗口化告警使用 TraceModel。增加了复杂性而没有明确的 v1 收益——60 秒的评估节奏就足够了。

**理由：** TraceModel 是持久化的、有索引的、多进程安全的，并且已经驱动了 CostService。每个我们需要的信号都是可推导的：错误率（`COUNT(status=error)/COUNT(*)`）、延迟 p95（`end_time - start_time` 的百分位数）、TTFT（`AVG(metadata->ttft_ms)`）、token 使用量（`SUM(usage->total_tokens)`）、工具失败率（`COUNT(status=error AND type=TOOL)/COUNT(type=TOOL)`）。使用单一源使 SignalProvider 注册表保持统一。

### D5: 预算预测的加权移动平均

`cost_monthly_forecast` 告警类型使用每日成本的指数加权移动平均（EWMA）外推到月底，而不是简单的线性投影。

**考虑的替代方案：**
- **简单线性投影**（LiteLLM 模式：`daily_avg = total_spend / days_elapsed; projected = daily_avg * total_days`）：忽略趋势变化。如果支出在月中加速，投影滞后。如果支出下降，则高估。
- **季节性分解**（Datadog 异常检测）：分解为趋势 + 季节性 + 残差。对于 v1 来说太复杂，需要新部署的实例尚未具备的数周基线数据。

**理由：** EWMA 对最近日期加权更重（指数衰减），因此它能捕捉趋势变化（加速/减速），而不需要季节性基线数据。公式：过去 7 天的 `recent_avg = sum(cost[i] * 0.5^(7-i)) / sum(0.5^(7-i))`，然后 `projected_monthly = recent_avg * days_in_month`。这是预算管理工具（AWS Budgets、CloudHealth）的标准方法，且易于实现和理解。

### D6: 每个平台的内置消息模板

每种 IM 通道类型（飞书、企业微信、钉钉、Slack）都有内置的消息模板，将告警事件格式化为平台的原生消息格式，而不是要求用户制作原始 webhook 负载。

**考虑的替代方案：**
- **仅通用 JSON**：用户配置 Jinja2 模板或原始负载。最大灵活性但用户体验极差——每个用户重新发明相同的飞书卡片 JSON。对于企业产品不可行。
- **单一统一模板**：一种消息格式发送到所有通道。失去平台特定功能（飞书交互式卡片、Slack Block Kit ACK 按钮）。

**理由：** 每个中国 IM 平台都有不同的负载格式（飞书使用带交互元素的 `card` JSON；企业微信使用格式有限的 `markdown`；钉钉使用带 @ 提醒的 `markdown`；Slack 使用带有 Block Kit 的 `blocks` 数组）。预构建模板意味着用户只需粘贴 webhook URL 即可获得带操作按钮的格式正确的告警。模板被定义为 Python 函数，接受 `AlertEventModel` 并返回平台特定的 JSON 负载。

### D7: 升级策略作为可重用实体

`EscalationPolicyModel` 是一个单独的表，规则通过 `escalation_policy_id` 引用它，而不是将升级步骤直接嵌入 `AlertRuleModel`。

**考虑的替代方案：**
- **嵌入规则中**：更简单的模式（少一个表），但升级逻辑通常在多个规则之间共享。一个生产部署可能有 50 条规则但只有 2-3 个升级策略（例如"标准"和"仅严重"）。在 50 条规则中重复步骤容易出错。
- **全局配置**：单个基于 YAML/配置的升级策略。不适用于多租户——不同的工作空间可能有不同的值班轮换。

**理由：** 升级策略本质上是可重用的（PagerDuty、Opsgenie 都将它们作为一等实体建模）。通过迁移创建默认策略（步骤 0：webhook + WebSocket，15 分钟后步骤 1：邮件，每 60 分钟重复）。用户可以为每个工作空间创建自定义策略。

### D8: 静默窗口作为时间绑定的匹配器

`AlertSilenceModel` 在 `[start_at, end_at]` 窗口内抑制匹配规则的通知，匹配器基于 `rule_ids` 和/或 `severity`。

**考虑的替代方案：**
- **禁用规则**：临时设置 `rule.enabled = false` 会丢失评估历史且不会自动恢复。还需要手动重新启用。
- **按通道静音**：在通知通道级别静音。过于粗糙——一个通道可能服务多个规则，其中只有一些应该被静音。

**理由：** 这与 Grafana Alertmanager 的静默模型匹配。静默窗口是具有审计跟踪（谁静默的、为什么、何时）的一等实体。评估器在发送通知前检查活跃静默。静默在 `end_at` 自动过期，因此维护窗口无需手动操作。

## Risks / Trade-offs — 风险 / 权衡

- **[评估延迟]** 60 秒轮询意味着告警在阈值突破后最多延迟 60 秒触发。→ v1 可接受。流式评估（在追踪完成时的 webhook）以后可以添加用于次分钟级告警。`for` 持续时间已经增加了数分钟的延迟，因此轮询间隔不是瓶颈。

- **[数据库负载]** 每个评估周期为每条启用的规则查询 TraceModel。如果规则多且追踪量大，这可能很昂贵。→ 缓解：(1) 规则是窗口绑定的（5-30 分钟），所以查询命中具有时间索引的近期数据；(2) SignalProvider 实现使用 `COUNT`/`SUM` 聚合，不是行级扫描；(3) 如果需要可以添加最大规则数守卫。

- **[单评估器节点]** 建议锁意味着只有一个节点评估。如果该节点宕机，告警将被错过。→ 缓解：锁在连接关闭时自动释放（会话级锁），因此健康的节点在一个评估周期内接管。对于 HA，未来的增强可以使用领导者选举机制。

- **[TTFT 准确性]** TTFT 在 LLMWorker 中通过为第一个数据块到达打时间戳来测量。如果 LLM 提供者缓冲方式不同，TTFT 可能不反映真实的首 token 时间。→ 缓解：清晰地记录测量语义。该指标对于相对比较（优化前后）仍然有用。

- **[邮件依赖]** 添加 `aiosmtplib` 引入了可选依赖。→ 缓解：放在 `[observability]` 组中。如果未配置 SMTP，邮件通道优雅降级（记录警告，其他通道仍然触发）。

- **[Webhook 交付可靠性]** 对外部 webhook 的 HTTP POST 可能失败（超时、5xx）。→ 缓解：3 次重试带指数退避。失败的交付被记录但不阻塞评估周期。未来的增强可以添加死信表。
