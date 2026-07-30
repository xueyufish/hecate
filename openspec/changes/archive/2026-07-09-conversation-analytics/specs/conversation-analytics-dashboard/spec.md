## ADDED Requirements — 新增需求

### Requirement: Conversation analytics overview page — 需求：对话分析概览页面
系统应提供一个 React 仪表板页面 `/ops-center/conversations`，显示对话分析：总会话数、已评分会话数、平均质量评分、质量分布（high/medium/low）和反馈摘要。

#### Scenario: Overview page loads with summary cards — 场景：概览页面加载摘要卡片
- **WHEN** 用户导航到 `/ops-center/conversations`
- **THEN** 页面获取 `GET /api/ops-center/conversations/overview` 并显示摘要卡片：总会话数、已评分会话数、平均质量评分、反馈比率

#### Scenario: Time range filter — 场景：时间范围过滤器
- **WHEN** 用户选择不同的时间范围（24h / 7d / 30d）
- **THEN** 页面使用更新的 `start_date` 和 `end_date` 参数重新获取概览数据

### Requirement: Quality distribution chart — 需求：质量分布图
系统应显示质量评分分布图，展示质量评分的直方图（桶：0.0–0.2、0.2–0.4、0.4–0.6、0.6–0.8、0.8–1.0）。图表应使用颜色编码：低分红色（<0.4）、中等黄色（0.4–0.7）、高分绿色（>0.7）。

#### Scenario: Quality distribution displayed — 场景：显示质量分布
- **WHEN** 用户查看对话分析仪表板
- **THEN** 页面显示柱状图，展示每个质量评分桶中的对话数量

#### Scenario: Click bucket to filter conversations — 场景：点击桶过滤对话
- **WHEN** 用户点击一个质量评分桶（例如 0.2–0.4）
- **THEN** 页面过滤对话列表，仅显示该评分范围内的对话

### Requirement: Topic distribution display — 需求：主题分布显示
系统应将主题分布显示为饼图或柱状图，展示每个主题的对话数量。每个主题应显示其标签和平均质量评分。

#### Scenario: Topic distribution displayed — 场景：显示主题分布
- **WHEN** 用户查看对话分析仪表板
- **THEN** 页面显示图表，展示每个主题的对话数量（例如，"technical_support: 45, billing: 30, unclassified: 25"）

#### Scenario: Click topic to filter conversations — 场景：点击主题过滤对话
- **WHEN** 用户在分布图中点击一个主题
- **THEN** 页面过滤对话列表，仅显示该主题的对话

### Requirement: Low-quality conversation list — 需求：低质量对话列表
系统应显示 quality_score 低于可配置阈值（默认 0.5）的对话列表。列表应显示对话 ID、agent 名称、质量评分、主题、轮次数和最后活跃时间。点击对话应导航到轮级详细信息视图。

#### Scenario: Low-quality list displayed — 场景：显示低质量列表
- **WHEN** 用户查看对话分析仪表板
- **THEN** 页面显示 quality_score < 0.5 的对话表，按 quality_score 升序排列

#### Scenario: Click conversation to view details — 场景：点击对话查看详细信息
- **WHEN** 用户在低质量列表中点击一个对话行
- **THEN** 页面导航到显示轮级质量评分的详细信息视图

### Requirement: Turn-level quality detail view — 需求：轮级质量详细信息视图
系统应为单个对话提供详细信息视图，显示轮级质量评分。每轮应显示：轮次索引、用户消息预览、assistant 消息预览、helpfulness 评分、coherence 评分、instruction_adherence 评分、总体评分、推理文本和用户反馈（如果有）。

#### Scenario: Detail view shows turn scores — 场景：详细信息视图显示轮次评分
- **WHEN** 用户查看一个 5 轮对话的详细信息页面
- **THEN** 页面显示 5 个轮次卡片，每个显示消息预览、质量评分和推理

#### Scenario: Detail view shows user feedback — 场景：详细信息视图显示用户反馈
- **WHEN** 一个轮次有用户反馈（positive/negative）
- **THEN** 轮次卡片与自动评分一起显示反馈评分和评论

### Requirement: Feedback metrics display — 需求：反馈指标显示
系统应在仪表板中显示反馈指标：总反馈提交数、positive/negative 比率和随时间变化的反馈趋势。

#### Scenario: Feedback summary displayed — 场景：显示反馈摘要
- **WHEN** 用户查看对话分析仪表板
- **THEN** 页面显示反馈量（总提交数）、反馈比率（positive / total）和反馈趋势图

### Requirement: Sidebar navigation entry — 需求：侧边栏导航入口
系统应在侧边栏中现有的 "Ops Center" 部分下添加一个 "Conversations" 子导航项，链接到 `/ops-center/conversations`。

#### Scenario: Sidebar displays Conversations link — 场景：侧边栏显示 Conversations 链接
- **WHEN** 侧边栏渲染
- **THEN** 在 "Ops Center" 部分下，"Conversations"、"Agents" 和 "Tools" 项都作为同级链接可见

### Requirement: Empty state handling — 需求：空状态处理
系统应在所选时间范围或过滤条件下没有对话数据时显示空状态消息。

#### Scenario: No conversations in time range — 场景：时间范围内无对话
- **WHEN** 用户选择的时间范围内没有对话
- **THEN** 页面显示 "No conversation data available for this period" 及插图

#### Scenario: No scored conversations — 场景：无已评分对话
- **WHEN** 存在对话但尚未有被评分
- **THEN** 页面显示 "Quality scores will appear here once conversations are evaluated"
