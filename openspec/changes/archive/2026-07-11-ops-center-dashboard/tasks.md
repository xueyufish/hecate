## 1. Backend — OpsCenterOverviewService — 后端 — OpsCenterOverviewService

- [x] 1.1 创建 `src/hecate/services/ops_center/overview.py`，包含 `OpsCenterOverviewService` 类（构造函数接受 `AsyncSession`）
- [x] 1.2 实现 `get_overview(start_date, end_date)` → 通过 `asyncio.gather(return_exceptions=True)` 并行调用 `AgentHealthService.get_fleet_overview()`、`ToolAnalyticsService.get_overview()`、`ConversationAnalyticsService.get_overview()`。返回包含 `agent_health`、`tool_analytics`、`conversation_analytics`（失败时为 null）和 `errors` 列表的统一字典。
- [x] 1.3 实现 `get_recent_activity(start_date, end_date, limit=20)` → 查询 critical/warning agent、最近的工具错误、低质量对话。合并并按时间戳排序。返回包含源、严重性、标题、时间戳、链接的活动项列表。

## 2. Backend — Tests — 后端 — 测试

- [x] 2.1 测试所有三个源可用时的 `get_overview()`（验证所有三个部分已填充，errors 为空）
- [x] 2.2 测试一个源失败时的 `get_overview()`（验证失败部分为 null、errors 列表中有错误消息、其他部分仍然填充）
- [x] 2.3 测试所有源失败时的 `get_overview()`（验证所有部分为 null、errors 列表已填充、HTTP 200 而非 500）
- [x] 2.4 测试混合事件时的 `get_recent_activity()`（验证按时间戳排序、最多 20 个条目）

## 3. Backend — API — 后端 — API

- [x] 3.1 创建 `src/hecate/api/management/ops_center_overview.py` 路由器，前缀为 `/api/ops-center`
- [x] 3.2 实现 `GET /overview` 端点（start_date、end_date 查询参数）→ 返回概览字典
- [x] 3.3 实现 `GET /recent-activity` 端点（start_date、end_date、limit 查询参数）→ 返回活动列表
- [x] 3.4 在 `main.py` 中注册 `ops_center_overview_router`

## 4. Frontend — Overview Page — 前端 — 概览页面

- [x] 4.1 创建 `web/src/app/(dashboard)/ops-center/page.tsx`，包含三个摘要卡片（Agent Health、Tool Analytics、Conversation Quality）
- [x] 4.2 添加 Agent Health 卡片（总 agent 数、healthy/warning/critical 计数及颜色编码徽章、集群错误率、集群 P95 延迟）
- [x] 4.3 添加 Tool Analytics 卡片（总执行次数、成功率、P95 延迟、错误计数）
- [x] 4.4 添加 Conversation Quality 卡片（总会话数、已评分会话数、平均质量评分、反馈比率）
- [x] 4.5 添加局部故障处理：对失败的 null 部分显示"数据不可用"及重试指示器
- [x] 4.6 添加最近活动源（时间排序列表，包含严重性徽章和指向子仪表板的链接）
- [x] 4.7 添加指向子仪表板的快速链接按钮（Agent Health、Tool Analytics、Conversations）
- [x] 4.8 添加时间范围选择器（24h / 7d / 30d），重新获取概览数据
- [x] 4.9 添加空状态处理（"此时间段无 Ops Center 数据"）

## 5. Frontend — Sidebar — 前端 — 侧边栏

- [x] 5.1 将 "Ops Center" 侧边栏链接从 `/ops-center/tools` 更改为 `/ops-center`（新的概览页面）

## 6. Verification — 验证

- [x] 6.1 运行 `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 错误
- [x] 6.2 运行 `mypy src/` — 0 错误
- [x] 6.3 运行 `python -m pytest tests/test_ops_center/test_ops_center_overview.py -q` — 全部通过
- [x] 6.4 端到端验证：导航到 /ops-center，确认所有三个摘要卡片显示、活动源已填充、快速链接工作
