## ADDED Requirements — 新增需求

### 需求：ContextOffloader 将溢出的消息保存到环境存储

系统应在 `services/context/offloader.py` 中提供一个 `ContextOffloader` 类，它将溢出的消息序列化到 Agent 的 `AgentEnvironment` 文件系统。卸载的消息应作为 JSON 写入 `memory/sessions/{session_id}/offloaded_{timestamp}.json`，保留完整的消息结构（role、content、tool_calls、tool_call_id）。卸载器应返回一个紧凑的引用桩消息，替换活动上下文中被卸载的块。

#### 场景：卸载将消息写入环境文件
- **当** 使用 30 条消息和会话 ID `"s123"` 调用 `ContextOffloader.offload(messages, session_id, environment)` 时
- **则** 一个 JSON 文件应写入 `memory/sessions/s123/offloaded_{timestamp}.json`
- **且** JSON 应包含完整的 30 条消息列表，所有字段都保留
- **且** 应使用环境的 `write_file` 方法执行写入

#### 场景：卸载返回紧凑的引用桩
- **当** `ContextOffloader.offload(...)` 成功完成时
- **则** 返回值应是一个带有 `role: "system"` 的单个字典
- **且** `content` 应包含文件路径、主题摘要和 `read_file` 检索提示
- **且** 内容长度不应超过 500 个字符

#### 场景：卸载桩包含主题摘要
- **当** 卸载的消息包含用户消息时
- **则** 引用桩应包含一个启发式主题摘要，来源于每条用户消息的前 200 个字符
- **且** 摘要总计应截断到 500 个字符

#### 场景：卸载桩指示检索方式
- **当** 生成引用桩时
- **则** 内容应包含指向卸载文件的字面指令 `read_file("<path>")`
- **且** 路径应与写入环境的实际文件路径一致

### 需求：没有环境时禁用 ContextOffloader

当没有 `AgentEnvironment` 可用时，系统应优雅地跳过卸载。管道应回退到压缩，不写入任何文件。

#### 场景：execution_context 中没有环境
- **当** `execution_context` 不包含 `"context_offloader"` 或卸载器没有环境时
- **则** 管道应完全跳过卸载步骤
- **且** 现有的压缩行为应不受影响地继续

#### 场景：通过配置禁用卸载
- **当** `CONTEXT_OFFLOAD_ENABLED` 设置为 `false` 时
- **则** 管道应完全跳过卸载步骤
- **且** 不应发生任何文件写入

### 需求：卸载阈值防止微不足道的卸载

系统应仅在 token 溢出量达到或超过 `CONTEXT_OFFLOAD_THRESHOLD_TOKENS`（默认 6000）时才卸载。这防止为微不足道的微小溢出写入文件。

#### 场景：低于阈值的溢出跳过卸载
- **当** 消息选择丢弃的总 token 数少于 `CONTEXT_OFFLOAD_THRESHOLD_TOKENS` 时
- **则** 管道应跳过卸载
- **且** 压缩应作为回退继续

#### 场景：达到或超过阈值的溢出触发卸载
- **当** 消息选择丢弃的总 token 数至少达到 `CONTEXT_OFFLOAD_THRESHOLD_TOKENS` 时
- **且** 环境可用且卸载已启用
- **则** 丢弃的消息应被卸载到环境中

### 需求：卸载文件以时间戳命名以便排序

系统应使用模式 `offloaded_{YYYYMMDDHHMMSS}.json` 命名卸载文件，以便在不解析文件内容的情况下实现按时间排序。

#### 场景：文件名包含时间戳
- **当** 卸载文件在 2026-07-21 14:30:22 UTC 创建时
- **则** 文件名应为 `offloaded_20260721143022.json`
- **且** 同一秒内的后续卸载应附加计数器后缀（例如 `offloaded_20260721143022_1.json`）

### 需求：卸载的内容可通过 read_file 检索

系统应确保卸载的 JSON 文件可通过 Agent 现有的 `read_file` 工具访问。不需要注册新工具。

#### 场景：Agent 检索卸载的内容
- **当** Agent 调用 `read_file("memory/sessions/s123/offloaded_20260721143022.json")` 时
- **则** 环境应返回 JSON 内容作为字节
- **且** 内容应解析为包含原始消息列表的有效 JSON
