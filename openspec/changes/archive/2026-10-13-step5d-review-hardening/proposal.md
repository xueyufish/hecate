# Proposal

## Why

复核 origin/main 的 step5d 交付发现：定时 workflow 丢失输入 channel 并忽略调度 workspace，HTTP 停止转换丢失关联，结构化输出校验晚于成功投影，流式成功缺摘要，关联不完整时内部事件字段未剥离。现有新增测试多在服务桩层断言，无法证明这些实际入口行为。

## What Changes

- 保留停止载荷及结构化流式输出的 Task/Run 关联；最终输出校验和修复纳入入口服务终态边界。
- 流式收集有界最终内容摘要，在入口服务统一映射和持久化事件，避免只由 HTTP 消费者承担落库。
- 无论关联是否完整，剥离内部事件字段；记录明确的关联缺口。
- 定时 workflow 保留 input_data 的 channel 语义，执行前拒绝跨 workspace 目标，使用共享工具注册表。
- 以真实入口/执行服务回归补足证据；纠正文档中无 Run 的独立 workflow 和安装/升级证据的完成声明。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `platform-entry-execution`: 明确最终验证边界、双模式关联与摘要、缺关联时的内部字段隔离。
- `scheduled-tasks`: 明确输入 channel 与 workspace 隔离的兼容语义，登记当前独立 workflow 的 Run 身份缺口。

## Impact

涉及 execution 入口服务、HTTP chat 协议适配、workflow 平台 adapter、定时执行器、相应测试及 step5d 文档。复用既有 EventStore、TaskRunRegistry 和状态词汇，不新增运行依赖、数据库表或执行身份。独立 workflow 的 Run 需要部署模型决策，暂不伪造 Agent/Deployment 或破坏已有调度配置。
