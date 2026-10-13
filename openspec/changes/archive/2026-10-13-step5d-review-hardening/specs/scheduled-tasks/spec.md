## ADDED Requirements

### Requirement: 定时 Workflow 输入与租户边界保持

定时 workflow SHALL 将 input_data 作为 workflow channel 输入传递，MUST NOT 将整个输入替换为序列化聊天文本。显式调度 workspace 与 workflow 所属 workspace 不匹配时 SHALL 在执行前拒绝，并使用与不存在目标一致的安全文案。输入 MUST NOT 覆盖运行器的 sys 或内部身份通道。仅有 Task 的当前独立 workflow 关联 SHALL 明确标记缺少 Run，不得声称已完成 Task/Run 登记认证。

#### Scenario: 输入 channel 原样生效
- **WHEN** 调度参数提供 messages 及自定义业务 channel
- **THEN** 执行图读取原始消息和业务值，生成输出基于这些 channel

#### Scenario: 跨 workspace 拒绝
- **WHEN** 调度任务引用其他 workspace 的 workflow
- **THEN** 执行服务和模型/工具不被调用，失败信息不暴露外部 workflow 内容

#### Scenario: 保留系统通道
- **WHEN** input_data 提供 sys 或内部通道值
- **THEN** 请求被拒绝，不能覆盖运行器生成的身份与执行配置
