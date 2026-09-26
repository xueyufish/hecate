# model-cost-management Delta

## ADDED Requirements

### Requirement: LLM 调用参数透传与用量核算

Runtime 的 LLM 调用适配层 SHALL 把调用配置中的模型限制参数（至少 `temperature`、`max_tokens`，以及 provider 支持的 `timeout`、`num_retries`）透传给底层 LLM 服务，使其真实到达 provider。用量核算 SHALL 优先采用 provider 返回的 usage（区分输入、输出；缓存用量在 provider 提供时计入），provider usage 缺失时 SHALL 以"输入消息与累计输出全文的确定性估算"一次性结算并在日志与事件中标记估算（`estimated=true`）。核算结果 SHALL 与流式分块方式无关（分块不变性）。纯工具调用的往返（无输出文本）SHALL 仍产生输入侧用量。

#### Scenario: 模型限制参数到达 provider

- **WHEN** agent 配置 `temperature=0.2`、`max_tokens=512` 后经 runtime 执行一次 LLM 调用
- **THEN** 底层 LLM 服务收到的调用参数包含这两个值（以 mock 断言调用参数为准）

#### Scenario: usage 可用时按分项核算

- **WHEN** provider 返回 `prompt_tokens` / `completion_tokens` usage
- **THEN** 记录的用量来自 usage 数值，而非文本长度估算

#### Scenario: 改变分块方式不改变总费用

- **WHEN** 同一请求分别以大片段与小片段流式返回（内容总量相同、无 provider usage）
- **THEN** 两种情况下记录的估算用量一致

#### Scenario: 纯工具调用产生用量

- **WHEN** 一次 LLM 调用仅返回 tool_calls 而无文本输出
- **THEN** 该调用仍记录输入侧用量（非零）

#### Scenario: 缺失 usage 标记估算

- **WHEN** provider 未返回 usage，适配层完成估算结算
- **THEN** 结算记录（日志/事件）携带 `estimated=true` 标记
