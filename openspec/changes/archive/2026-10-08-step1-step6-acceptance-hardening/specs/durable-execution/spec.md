## ADDED Requirements

### Requirement: 指定 checkpoint 读取按会话隔离

checkpoint 存储 SHALL 同时校验 session 与 checkpoint ID；请求其他 session 的 ID MUST 返回不存在，不泄露其通道状态。

#### Scenario: 跨会话 ID
- **WHEN** 请求 session A 时提供 session B 的 checkpoint ID
- **THEN** 返回不存在，session B 状态不会被恢复
