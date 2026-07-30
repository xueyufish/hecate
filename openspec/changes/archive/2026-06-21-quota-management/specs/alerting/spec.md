## ADDED Requirements — 新增需求

### Requirement: Quota soft-limit alert type — 需求：配额软限制告警类型
The system SHALL support `quota_soft_limit_reached` as an AlertType. This alert fires when a quota's `soft_limit` threshold is crossed during post-LLM usage recording.

系统应支持 `quota_soft_limit_reached` 作为 AlertType。当在 LLM 后用量记录期间配额的 `soft_limit` 阈值被超过时，此告警触发。

#### Scenario: Soft limit crossed creates alert event — 场景：超过软限制创建告警事件
- **WHEN** a post-LLM recording causes quota usage to cross the soft_limit threshold for the first time in a period
- **THEN** an `AlertEventModel` is created with `alert_type="quota_soft_limit_reached"`, `current_value` set to the utilization percentage, and `severity="warning"`

- **当** LLM 后记录导致配额用量在周期内首次超过 soft_limit 阈值
- **则**创建 `AlertEventModel`，`alert_type="quota_soft_limit_reached"`，`current_value` 设置为利用率百分比，`severity="warning"`

#### Scenario: Soft limit alert notification dispatched — 场景：发送软限制告警通知
- **WHEN** the AlertEvaluator processes a `quota_soft_limit_reached` event
- **THEN** it SHALL dispatch notifications through the standard escalation policy, including the quota name and current usage in the message template

- **当** AlertEvaluator 处理 `quota_soft_limit_reached` 事件
- **则**应通过标准升级策略发送通知，在消息模板中包含配额名称和当前用量
