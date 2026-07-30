## ADDED Requirements — 新增需求

### Requirement: PregelRuntime 中的 StreamMode.MESSAGES 支持 — StreamMode.MESSAGES support in PregelRuntime
PregelRuntime 应支持第三种 StreamMode（MESSAGES），在调度阶段从流式 Workers yield 单个令牌事件。

#### Scenario: MESSAGES 模式 yield 令牌事件 — MESSAGES mode yields token events
- **当** PregelRuntime 以 `stream_mode=StreamMode.MESSAGES` 执行
- **并且** Worker 在执行期间 yield 中间令牌
- **则** PregelRuntime 应在 yield 超步结果之前，为每个中间令牌 yield `{"type": "message", "content": "<token>"}`

#### Scenario: MESSAGES 模式对非流式 Workers 回退到 VALUES — MESSAGES mode falls back to VALUES for non-streaming workers
- **当** PregelRuntime 以 `stream_mode=StreamMode.MESSAGES` 执行
- **并且** Worker 不 yield 中间令牌
- **则** PregelRuntime 应在超步之后 yield `{"type": "values", "state": <snapshot>}`，与 VALUES 模式相同
