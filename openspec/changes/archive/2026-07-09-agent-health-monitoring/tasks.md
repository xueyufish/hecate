## 1. Configuration — 配置

- [x] 1.1 在 `core/config.py` 中添加健康阈值设置：`AGENT_HEALTH_ERROR_RATE_WARNING: float = 0.05`、`AGENT_HEALTH_ERROR_RATE_CRITICAL: float = 0.15`、`AGENT_HEALTH_LATENCY_WARNING_MS: int = 10000`、`AGENT_HEALTH_LATENCY_CRITICAL_MS: int = 30000`
- [x] 1.2 添加 `AGENT_HEALTH_SCORE_WEIGHTS: dict = {"error_rate": 0.5, "latency": 0.3, "activity": 0.2}` 设置（加权评分公式的 JSON 字典）

## 2. AgentHealthService — Core Logic — AgentHealthService — 核心逻辑

- [x] 2.1 创建 `src/hecate/services/ops_center/agent_health.py`，包含 `AgentHealthService` 类（构造函数接受 `AsyncSession`，与 `ToolAnalyticsService` 模式相同）
- [x] 2.2 实现 `_classify_health_status(error_rate, p95_latency_ms, settings)` → 使用可配置阈值（最差维度逻辑）返回 `"healthy"` / `"warning"` / `"critical"` / `"unknown"`
- [x] 2.3 实现 `_compute_health_score(error_rate, p95_latency_ms, session_count, settings)` → 返回 0-100 整数或对未知 agent 返回 `None`。使用加权公式：错误率维度 = `max(0, 100 - error_rate * 500)`、延迟维度 = `max(0, 100 - (p95_latency_ms / critical_threshold_ms) * 100)`、活动维度 = `min(100, session_count / 10 * 100)`。权重来自 `AGENT_HEALTH_SCORE_WEIGHTS`。
- [x] 2.4 实现 `_compute_p95(values)` 辅助函数（重用 `tool_analytics.py` 中的逻辑 — 用于跨方言兼容性的 Python 端百分位）

## 3. AgentHealthService — Query Methods — AgentHealthService — 查询方法

- [x] 3.1 实现 `get_fleet_overview(start_date, end_date)` → 按 `agent_id` 分组根追踪（`type="trace"`）的聚合查询。返回 `{total_agents, healthy_count, warning_count, critical_count, unknown_count, fleet_error_rate, fleet_p95_latency_ms, top_degraded: [...]}`
- [x] 3.2 实现 `get_agent_health(agent_id, start_date, end_date)` → 来自根追踪的每个 agent 指标。返回 `{agent_id, total_sessions, error_count, error_rate, success_rate, avg_latency_ms, p95_latency_ms, last_active_at, health_status, health_score, score_breakdown}`
- [x] 3.3 实现 `get_agent_trends(agent_id, days=7, granularity="daily")` → 按时间桶的会话计数、错误、错误率、平均延迟、P95 延迟的时间序列（Python 端桶划分，与 `ToolAnalyticsService.get_trends` 相同）

## 4. AgentHealthService — Tests — AgentHealthService — 测试

- [x] 4.1 测试 `_classify_health_status()`：healthy（低错误 + 低延迟）、warning（仅高错误）、critical（高延迟）、unknown（无数据）
- [x] 4.2 使用设置中的自定义阈值测试 `_classify_health_status()`
- [x] 4.3 测试 `_compute_health_score()`：完美评分（0% 错误、低延迟、20 个会话）、降级评分（10% 错误）、unknown 的 null 评分
- [x] 4.4 使用设置中的自定义权重测试 `_compute_health_score()`
- [x] 4.5 使用混合健康状态（多个 agent、不同错误率和延迟）测试 `get_fleet_overview()`
- [x] 4.6 无数据时测试 `get_fleet_overview()`（返回零值，空 top_degraded）
- [x] 4.7 测试 `get_fleet_overview()` 的 top_degraded 限制为 10 个按评分升序排列的条目
- [x] 4.8 使用活跃 agent 测试 `get_agent_health()`（验证所有字段，包括 score_breakdown）
- [x] 4.9 使用不活跃 agent 测试 `get_agent_health()`（status=unknown、score=null）
- [x] 4.10 测试 `get_agent_trends()` 返回 7 天范围正确的每日桶
- [x] 4.11 使用空数据测试 `get_agent_trends()` 返回空列表

## 5. Agent Health API — Agent 健康 API

- [x] 5.1 创建 `src/hecate/api/management/agent_health.py` 路由器，前缀为 `/api/ops-center/agents`
- [x] 5.2 实现 `GET /overview` 端点（start_date、end_date 查询参数）→ 集群概览字典
- [x] 5.3 实现 `GET /{agent_id}/health` 端点（start_date、end_date 查询参数）→ 每个 agent 健康字典，agent 无数据返回 404
- [x] 5.4 实现 `GET /{agent_id}/trends` 端点（days、granularity 查询参数）→ 时间序列列表
- [x] 5.5 在 `main.py` 中注册 `agent_health_router`

## 6. Frontend — Agent Health Dashboard — 前端 — Agent 健康仪表板

- [x] 6.1 创建 `web/src/app/(dashboard)/ops-center/agents/page.tsx`，包含集群状态摘要卡片（healthy/warning/critical/unknown 计数，带有颜色编码徽章：绿色/黄色/红色/灰色）
- [x] 6.2 添加 agent 集群表（列：agent 名称、健康状态徽章、健康评分、错误率、P95 延迟、最后活跃 — 可按评分排序）
- [x] 6.3 添加时间范围选择器（24h / 7d / 30d），重新获取概览数据
- [x] 6.4 添加向下钻取：点击 agent 行导航到详细信息视图，显示健康趋势图表（recharts 折线图，显示错误率和延迟随时间变化）和评分分解
- [x] 6.5 添加空状态处理（无 agent / 无数据消息）
- [x] 6.6 在 `web/src/components/sidebar.tsx` 中 "Ops Center" 下添加 "Agents" 子导航项，链接到 `/ops-center/agents`

## 7. Verification — 验证

- [x] 7.1 运行 `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 错误
- [x] 7.2 运行 `mypy src/` — 0 错误
- [x] 7.3 运行 `python -m pytest tests/test_ops_center/test_agent_health.py -q` — 全部通过
- [ ] 7.4 端到端验证：触发一个会话执行，确认集群概览显示该 agent 具有正确的健康状态和评分
