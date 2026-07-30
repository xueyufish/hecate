## ADDED Requirements — 新增需求

### Requirement：ContextEngine ABC 定义可插拔的上下文管理 — ContextEngine ABC 定义可插拔的上下文管理
引擎 SHALL 在 `engine/context.py` 中定义一个 `ContextEngine` ABC，包含方法：`select_messages`、`compress`、`estimate_tokens`。

#### Scenario：在预算内选择消息
- **WHEN** 使用消息历史和 token 预算调用 `select_messages(history, budget)`
- **THEN** 它 SHALL 返回一个在预算内的消息列表

#### Scenario：压缩消息
- **WHEN** 使用消息列表调用 `compress(messages)`
- **THEN** 它 SHALL 返回消息的压缩版本（更少的 token）

#### Scenario：估算 token 数量
- **WHEN** 使用消息列表调用 `estimate_tokens(messages)`
- **THEN** 它 SHALL 返回 token 总数的整数估算值

### Requirement：InMemoryContextEngine 提供默认实现 — InMemoryContextEngine 提供默认实现
一个 `InMemoryContextEngine` SHALL 使用适用于测试和单机部署的简单启发式方法实现 ContextEngine。

#### Scenario：在预算内选择最近的消息
- **WHEN** 使用 10 条消息且预算允许 5 条调用 `select_messages(history, budget)`
- **THEN** 它 SHALL 返回最近的 5 条消息

#### Scenario：通过截断最旧的来压缩
- **WHEN** 使用超过阈值且预算为 5 的 10 条消息调用 `select_messages(history, budget)`
- **THEN** 它 SHALL 返回最近的 5 条消息

#### Scenario：简单的 token 估算
- **WHEN** 使用消息列表调用 `estimate_tokens(messages)`
- **THEN** 它 SHALL 基于字符数返回整数估算（`len(text) // 4`）

#### Scenario：空列表的 token 估算
- **WHEN** 使用空列表调用 `estimate_tokens([])`
- **THEN** 它 SHALL 返回 `0`

### Requirement：ContextEngine ABC 不可直接实例化 — ContextEngine ABC 不可直接实例化
直接实例化 ContextEngine SHALL 引发 TypeError。

#### Scenario：直接实例化失败
- **WHEN** 调用 `ContextEngine()`
- **THEN** 它 SHALL 引发 `TypeError`