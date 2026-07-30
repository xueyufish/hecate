## Context — 背景

变更 1（`otel-trace-bridge-tool-analytics`）将 OTel spans 桥接到了 TraceModel 并构建了 `ToolAnalyticsService`。TraceModel 现在接收真实的执行数据：根会话 spans（`type="trace"`、`name="session:{id}"`）、LLM spans（`type="generation"`）和工具 spans（`type="tool"`）。每个 span 都带有 `agent_id`、`status`（started/completed/error）、`start_time`、`end_time` 和 `session_id`。

现有的 `ToolAnalyticsService`（`services/ops_center/tool_analytics.py`）展示了 SQL 聚合模式：构造函数接受 `AsyncSession`，方法运行 `func.count()` / `func.max()` 查询并带 `~TraceModel.deleted` 过滤器，P95 在 Python 中计算以实现跨方言兼容性，所有方法返回 `dict[str, Any]`。

`ModelMonitoringService`（`services/monitoring/`）对来自 TraceModel 的每个模型指标遵循类似的模式。

**关键数据源**：根追踪 spans（`type="trace"`）代表完整的 agent 会话执行。每个都有 `agent_id`、`status`、`start_time`、`end_time`。这些是 agent 健康的主要信号。

## Goals / Non-Goals — 目标 / 非目标

**Goals（目标）：**

- 基于 TraceModel 的每个 agent 健康指标：总会话数、错误率、平均/P95 会话延迟、成功率、最后活跃时间戳
- 三级健康状态分类：`healthy` / `warning` / `critical`，具有可配置阈值
- 可配置的健康评分（0-100），结合错误率和延迟
- SLA 违规检测：标记超出阈值边界的 agent
- 集群概览：聚合分布（N healthy/warning/critical），最差的 agent
- 每个 agent 的向下钻取：时间序列趋势、最近的追踪、分数分解
- REST API + 前端仪表板，遵循 `ToolAnalyticsService` 模式

**Non-Goals（非目标）：**

- 用户满意度分数 / 升级率 — 依赖于对话反馈基础设施（变更 3：`conversation-analytics`）。健康评分公式为未来的满意度数据保留了占位权重。
- 实时 WebSocket 推送 — 仅 REST 轮询（与工具分析相同）
- 告警集成 — 现有的 AlertService 处理告警路由；健康指标是只读查询
- 统一 Ops Center 仪表板（8.9） — 变更 4 聚合 8.9a/b/c 数据源
- 对话质量评分（8.9b v2） — 单独变更

## Decisions — 决策

### Decision 1: SQL-derived health metrics from root trace spans — 决策 1：从根追踪 spans 的 SQL 派生健康指标

**选择**：查询 `type="trace"` 的 TraceModel（根会话 spans）以获取每个 agent 的会话级指标。每个根 span 代表一次完整的 agent 执行，包含 `agent_id`、`status`、`start_time`、`end_time`。

**理由**：根追踪 spans 由 `PregelRuntime.execute()`（变更 1）创建。它们从执行上下文中携带 `agent_id`。计算根 span = 会话计数；错误根 span = 失败会话。这与 `ToolAnalyticsService` 和 `ModelMonitoringService` 使用相同的数据源 — 无需新基础设施。

**考虑的替代方案**：
- **查询每个 agent 的所有 span 类型**：噪音太大。工具和 LLM spans 是子级信号；会话级（根追踪）是集群健康的正确粒度。
- **使用 MetricsStore（实时监控）**：不同的范式（时间窗口计数器 vs. SQL 聚合）。需要在 PregelRuntime 中接入 MetricsStore 记录。对于仪表板来说过度设计。

### Decision 2: Three-level health status taxonomy with configurable thresholds — 决策 2：具有可配置阈值的三级健康状态分类

**选择**：基于两个维度计算每个 agent 的 `healthy` / `warning` / `critical` 状态：
- 错误率：>5% 时 warning，>15% 时 critical（可配置）
- P95 会话延迟：>10s 时 warning，>30s 时 critical（可配置）

状态 = 两个维度中更差的那个（如果任一维度为 critical，agent 为 critical）。

**理由**：二维状态避免了误报（例如，高延迟但零错误 = warning，不是 critical）。最差维度是行业标准（Salesforce Agentforce 使用每个维度阈值的综合健康评分）。

**考虑的替代方案**：
- **单一健康评分阈值**：可操作性较低。操作员无法判断降级是延迟驱动还是错误驱动。
- **机器学习异常检测**：v1 过度设计。现有的 `ModelMonitoringService` 使用 z-score 漂移检测 — 那是模型级的。Agent 级从基于阈值开始，以后可以添加 z-score。

### Decision 3: Weighted health score formula (0–100) — 决策 3：加权健康评分公式（0-100）

**选择**：健康评分 = 维度评分的加权和：
- 错误率维度（权重：50%）：`max(0, 100 - error_rate * 500)` — 0% 错误 = 100，20% 错误 = 0
- 延迟维度（权重：30%）：`max(0, 100 - (p95_latency_ms / critical_threshold_ms) * 100)` — 低于 warning = ~100，达到 critical = 0
- 活动维度（权重：20%）：`min(100, session_count / expected_sessions * 100)` — 根据最近基线归一化

所有权重可通过 `AGENT_HEALTH_SCORE_WEIGHTS` 设置（JSON 字典）配置。

**理由**：加权评分透明且可调优。操作员无需代码更改即可调整权重。50/30/20 的默认值优先考虑错误率（影响最大），然后是延迟，最后是活动。这与 Salesforce Agentforce 的"综合健康评分"方法一致。

**考虑的替代方案**：
- **纯阈值状态（无评分）**：粒度不够。评分使得在集群视图中按降级严重性对 agent 排序成为可能。
- **基于 ML 的评分**：黑盒。可配置公式是可审计和可调试的。

### Decision 4: Fleet overview as aggregate query — 决策 4：集群概览作为聚合查询

**选择**：`get_fleet_overview()` 运行单个 SQL 查询，按 `agent_id` 分组根追踪，在 SQL 中计算每个 agent 的聚合，然后在 Python 中对状态进行分类并计数分布。

**理由**：集群视图的单个往返。遵循 `ToolAnalyticsService.get_overview()` 模式（SQL 聚合 + Python 后处理 P95 和派生指标）。

### Decision 5: No persistence — compute on demand — 决策 5：不持久化 — 按需计算

**选择**：健康状况和评分在每个 API 请求时计算。没有健康快照表，没有后台刷新任务。

**理由**：使用索引 `agent_id` + `type` + `start_time` 列的 TraceModel 查询很快（10 万行 <100ms）。仪表板轮询间隔为 30-60 秒。添加快照表 + 刷新任务是过早优化。如果性能在大规模下降，可以稍后添加物化视图或缓存层，无需更改 API。

**考虑的替代方案**：
- **后台健康快照任务**：增加复杂性（调度器、快照表、过期数据）。当前规模不需要。
- **Redis 缓存**：相同的权衡。v1 保持简单。

## Risks / Trade-offs — 风险 / 权衡

- **[风险] 缺少满意度/升级指标** → 健康评分仅使用错误率 + 延迟 + 活动。满意度评分权重（当前 0%）保留用于将来与变更 3（conversation-analytics）集成。在非目标中记录。
- **[风险] 近期零活动的 agent** → 在时间窗口内没有根追踪的 agent 获得 `status=unknown`、`score=None`，从集群分布计数中排除。防止扭曲健康计数。
- **[权衡] Python 端 P95 计算** → 与 ToolAnalyticsService 相同。跨方言兼容（SQLite/PostgreSQL/MySQL）。每个 agent 超过 10 万条追踪时，考虑使用 SQL 百分位函数。v1 不关注此问题。
- **[权衡] 无历史健康追踪** → 没有健康评分随时间变化的时间序列。集群趋势端点显示底层指标（错误率、延迟）但不显示计算出的评分。添加健康历史表是未来的增强。
