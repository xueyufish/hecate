## ADDED Requirements

### Requirement: IM 会话按渠道路由 Agent 与版本

IM 会话创建与消息处理 SHALL 经 im 类型渠道实体解析目标：按 IM 渠道类型与应用实例标识查找对应渠道行，得到 `agent_id` 与 `bind_mode`，并按绑定模式解析版本（解析发生在每条消息处理时，使改绑无需重建会话即可生效）。消息入队 SHALL 携带解析出的 `agent_id`。当消息所属 IM 实例没有配置渠道行时，系统 SHALL 拒绝该消息并记录显式错误日志，SHALL NOT 回退到任何默认 agent（含零 UUID 占位）。

#### Scenario: 渠道绑定的飞书实例路由到指定 Agent 版本

- **WHEN** 飞书应用实例 E 配置了 im 渠道行（绑定 Agent A，pinned 到 v2），绑定用户发来消息
- **THEN** 会话 SHALL 路由到 Agent A，且执行 SHALL 使用 v2 的冻结配置

#### Scenario: published 绑定在改绑后即时生效

- **WHEN** 渠道行为 published 模式，Agent A 发布了新版本 v4，同一会话的下一条消息到达
- **THEN** 该消息 SHALL 按 v4 解析执行，无需重建会话

#### Scenario: 未配置渠道的 IM 实例被显式拒绝

- **WHEN** 某飞书应用实例没有对应的 im 渠道行，其 webhook 收到消息
- **THEN** 系统 SHALL NOT 将消息路由到任何 Agent，SHALL 记录包含实例标识的显式错误日志
