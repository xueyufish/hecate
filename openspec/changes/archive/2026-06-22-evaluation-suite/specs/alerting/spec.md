## ADDED Requirements — 新增需求

### Requirement: Evaluation regression alert type — 需求：评估回归告警类型
The system SHALL support `evaluation_regression` as an AlertType. This alert fires when an evaluation run's scores drop significantly below a baseline run, indicating a quality regression.

系统应支持 `evaluation_regression` 作为 AlertType。当评估运行的得分显著低于基线运行时，此告警触发，表示质量回归。

#### Scenario: Regression detected triggers alert — 场景：检测到回归触发告警
- **WHEN** a run comparison detects that one or more metrics have regressed beyond the threshold (default 5%)
- **THEN** an `AlertEventModel` is created with `alert_type="evaluation_regression"`, `current_value` set to the average regression delta, and `severity="warning"`

- **当**运行比较检测到一个或多个指标回归超过阈值（默认 5%）
- **则**创建 `AlertEventModel`，`alert_type="evaluation_regression"`，`current_value` 设置为平均回归差值，`severity="warning"`

#### Scenario: Evaluation regression alert notification — 场景：评估回归告警通知
- **WHEN** the AlertEvaluator processes an `evaluation_regression` event
- **THEN** it SHALL dispatch notifications through the standard escalation policy, including the regressed metric names and delta values in the message template

- **当** AlertEvaluator 处理 `evaluation_regression` 事件
- **则**应通过标准升级策略发送通知，在消息模板中包含回归的指标名称和差值
