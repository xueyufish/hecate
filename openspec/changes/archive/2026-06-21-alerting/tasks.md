## 1. 模型层

- [x] 1.1 创建 `src/hecate/models/alert.py`，包含 `AlertType` 枚举（8 个值）、`AlertState` 枚举（4 个值）、`AlertSeverity` 枚举（3 个值）、`ChannelType` 枚举（7 个值）
- [x] 1.2 实现 `AlertRuleModel(BaseModel)`，包含所有字段：name、description、alert_type、threshold、window_minutes、for_minutes、severity、filters（JSON）、enabled、escalation_policy_id、channel_ids（JSON）、workspace_id
- [x] 1.3 实现 `AlertEventModel(BaseModel)`，字段：rule_id、state、current_value、fired_at、resolved_at、acked_at、acked_by、escalation_step、workspace_id
- [x] 1.4 实现 `AlertSilenceModel(BaseModel)`，字段：start_at、end_at、matchers（JSON）、created_by、reason、workspace_id
- [x] 1.5 实现 `EscalationPolicyModel(BaseModel)`，字段：name、steps（JSON）、repeat_interval_min、workspace_id
- [x] 1.6 实现 `NotificationChannelModel(BaseModel)`，字段：name、channel_type、config（JSON）、enabled、workspace_id
- [x] 1.7 实现 Pydantic 模式：5 个模型（rule、event、silence、escalation_policy、channel）各自的 Create/Update/Read

## 2. 迁移

- [x] 2.1 创建 Alembic 迁移，创建 5 个表（alert_rules、alert_events、alert_silences、escalation_policies、notification_channels），在 workspace_id、rule_id、state 上建索引
- [x] 2.2 创建名为 "Standard Escalation" 的默认升级策略种子，步骤为 [{delay_min: 0, channel_types: ["webhook_feishu", "websocket"]}, {delay_min: 15, channel_types: ["email"]}]，repeat_interval_min=60，幂等检查
- [x] 2.3 验证迁移从当前头（e66673e35d7c）链接且干净地应用

## 3. 信号提供者

- [x] 3.1 创建 `src/hecate/services/signal_provider.py`，包含 `SignalProvider` ABC：`async def get_value(self, rule: AlertRuleModel, window_minutes: int) -> float`
- [x] 3.2 实现 `ErrorRateProvider`——查询 TraceModel 获取窗口内带过滤器的 COUNT(status='error')/COUNT(*)
- [x] 3.3 实现 `LatencyP95Provider`——查询 TraceModel 获取窗口内 (end_time - start_time) 的第 95 百分位
- [x] 3.4 实现 `LatencyTTFTProvider`——查询 TraceModel 获取窗口内 WHERE type='GENERATION' 的 AVG(metadata->ttft_ms)
- [x] 3.5 实现 `TokenUsageProvider`——查询 TraceModel 获取窗口内 WHERE type='GENERATION' 的 SUM(usage->total_tokens)
- [x] 3.6 实现 `CostDailyProvider`——委托 CostService.get_cost_summary() 获取当日数据
- [x] 3.7 实现 `CostMonthlyForecastProvider`——计算过去 7 天每日成本的 EWMA，外推到月份长度
- [x] 3.8 实现 `ToolFailureRateProvider`——查询 TraceModel 获取窗口内 COUNT(status='error' AND type='TOOL')/COUNT(type='TOOL')
- [x] 3.9 实现 `SuccessRateProvider`——查询 TraceModel 获取窗口内 COUNT(status='completed')/COUNT(*)
- [x] 3.10 实现 `SignalProviderRegistry`——映射 AlertType → SignalProvider 实例，带 `get_provider(alert_type)` 方法

## 4. 告警服务

- [x] 4.1 创建 `src/hecate/services/alert_service.py`，包含 `AlertService` 类（注入 AsyncSession）
- [x] 4.2 实现规则 CRUD：create_rule、list_rules、get_rule、update_rule、delete_rule（软删除）
- [x] 4.3 实现事件查询：list_events（按 state、rule_id、workspace 过滤）、get_event、acknowledge_event（设置 state=acked、acked_at、acked_by）
- [x] 4.4 实现静默 CRUD：create_silence、list_silences（带活跃过滤器）、delete_silence
- [x] 4.5 实现升级策略 CRUD：create_policy、list_policies、get_policy、update_policy、delete_policy
- [x] 4.6 实现通知通道 CRUD：create_channel、list_channels、update_channel、delete_channel
- [x] 4.7 实现 `is_silenced(event)` 辅助函数——查询匹配事件规则和严重性的活跃静默

## 5. 通知调度器

- [x] 5.1 创建 `src/hecate/services/notification_dispatcher.py`，包含 `NotificationDispatcher` 类
- [x] 5.2 实现 `FeishuTemplate`——将 AlertEvent 格式化为飞书卡片 JSON，包含严重性、规则名称、当前值、阈值、ACK 按钮
- [x] 5.3 实现 `WeComTemplate`——将 AlertEvent 格式化为企业微信 markdown 负载
- [x] 5.4 实现 `DingTalkTemplate`——将 AlertEvent 格式化为钉钉 markdown 负载
- [x] 5.5 实现 `SlackTemplate`——将 AlertEvent 格式化为 Slack Block Kit JSON，包含 ACK 按钮
- [x] 5.6 实现 `GenericWebhookTemplate`——将 AlertEvent 格式化为纯 JSON 负载
- [x] 5.7 实现 `EmailTemplate`——将 AlertEvent 格式化为 HTML 邮件，包含主题和正文
- [x] 5.8 实现 `dispatch(event, channels)`——对每个通道，按 channel_type 选择模板，渲染负载，通过 httpx（webhook）或 aiosmtplib（email）或 ConnectionManager.broadcast（websocket）发送
- [x] 5.9 实现 webhook 重试逻辑：3 次重试带指数退避（1s、2s、4s）处理 5xx/超时，记录最终结果

## 6. 告警评估器

- [x] 6.1 创建 `src/hecate/services/alert_evaluator.py`，包含 `AlertEvaluator` 类
- [x] 6.2 使用 `pg_try_advisory_lock` / `pg_advisory_unlock` 以常量锁 ID 实现建议锁获取/释放
- [x] 6.3 实现 `_evaluate_cycle()`——加载已启用规则 + 活跃静默，对每条规则：查询信号提供者、比较阈值、管理状态转换（pending → firing → resolved）
- [x] 6.4 实现状态转换逻辑：无事件 + 条件满足 → 创建 PENDING 事件；PENDING + for_minutes 已过 + 仍满足 → FIRING（设置 fired_at）；PENDING/FIRING + 不满足 → RESOLVED（设置 resolved_at）
- [x] 6.5 实现升级步骤推进：对 FIRING 事件，从 fired_at + 延迟计算当前步骤，如果步骤推进则发送；如果 repeat_interval 已过则重新发送步骤 0
- [x] 6.6 实现发送前静默检查：查询活跃静默，如果匹配则跳过发送
- [x] 6.7 实现后台循环：asyncio 任务，可配置间隔（默认 60s），在应用程序生命周期中启动/停止
- [x] 6.8 在 main.py 生命周期中与 MonitoringService 一起注册评估器启动/停止

## 7. TTFT 检测

- [x] 7.1 定位 engine/workers/llm_worker.py 中的 LLMWorker 流式调用路径
- [x] 7.2 添加 TTFT 测量：为第一个数据块到达打时间戳，计算 ttft_ms = (first_chunk_time - start_time) * 1000
- [x] 7.3 将 ttft_ms 写入 TraceModel metadata_["ttft_ms"]，将 total_latency_ms 写入 GENERATION span 的 metadata_["total_latency_ms"]
- [x] 7.4 确保非流式调用设置 ttft_ms = total_latency_ms（第一个字节 = 最后一个字节）

## 8. API 层

- [x] 8.1 创建 `src/hecate/api/management/alerts.py`，包含 5 个路由器：rules_router、events_router、silences_router、channels_router、escalation_policies_router
- [x] 8.2 实现规则路由器：POST/GET/PUT/DELETE /api/alerts/rules，带 AuthContext + AsyncSession 依赖
- [x] 8.3 实现事件路由器：GET /api/alerts/events（带 state、rule_id 过滤器），POST /api/alerts/events/{id}/ack
- [x] 8.4 实现静默路由器：POST/GET/DELETE /api/alerts/silences（带活跃过滤器）
- [x] 8.5 实现通道路由器：POST/GET/PUT/DELETE /api/alerts/channels，POST /api/alerts/channels/{id}/test（发送测试通知）
- [x] 8.6 实现升级策略路由器：POST/GET/PUT/DELETE /api/alerts/escalation-policies
- [x] 8.7 在 main.py 中使用正确的导入和 include_router 调用注册所有 5 个路由器

## 9. 配置

- [x] 9.1 向 core/config.py 添加告警设置：ALERT_EVAL_INTERVAL（int，默认 60）、ALERT_SMTP_HOST、ALERT_SMTP_PORT、ALERT_SMTP_USER、ALERT_SMTP_PASSWORD、ALERT_SMTP_FROM、ALERT_ENABLED（bool，默认 True）
- [x] 9.2 将 aiosmtplib 添加到 pyproject.toml [observability] 可选依赖组

## 10. 测试

- [x] 10.1 创建 `tests/test_services/test_alert_service.py`——测试模型创建、规则 CRUD、事件查询、ACK、静默 CRUD、升级策略 CRUD、通道 CRUD
- [x] 10.2 测试信号提供者：mock TraceModel 数据，验证每个提供者返回正确的聚合值（error_rate、latency_p95、ttft、token_usage、cost_daily、cost_forecast、tool_failure_rate、success_rate）
- [x] 10.3 测试评估器状态转换：条件满足 → PENDING → FIRING（for_minutes 后）→ RESOLVED（条件清除），ACK 停止升级
- [x] 10.4 测试评估器建议锁：锁不可用 → 跳过周期
- [x] 10.5 测试升级步骤推进：触发时步骤 0，延迟后步骤 1，repeat_interval 后重复
- [x] 10.6 测试静默抑制：静默事件 → 不发送；过期静默 → 恢复发送
- [x] 10.7 测试通知调度器：每个模板渲染正确的负载格式（飞书卡片、企业微信 markdown、钉钉 markdown、Slack Block Kit、通用 JSON、邮件 HTML）
- [x] 10.8 测试 webhook 重试：mock 5xx 响应 → 3 次退避重试；mock 成功 → 无重试
- [x] 10.9 测试预算预测：稳定成本 → 线性投影；加速成本 → EWMA > 线性；无数据 → 0.0

## 11. 验证

- [x] 11.1 运行 ruff check src/hecate/ tests/——零错误
- [x] 11.2 运行 ruff format --check src/ tests/——零更改
- [x] 11.3 运行 mypy src/——零错误
- [x] 11.4 运行 pytest tests/ -q——所有测试通过
