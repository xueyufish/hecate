## Why — 动机

模型调试页面 `/settings/models/debug` 已经存在但功能简陋 — 它仅支持带温度和 max_tokens 的单次测试。对于一个生产级平台，用户需要**流式响应**以实时看到输出，**系统提示词支持**以测试真实的 Agent 配置，**响应时间测量**以比较不同提供商，以及**测试历史**以追踪实验。这些在 AgentArts、Coze 等工具中都是模型调试的基础功能。

## What Changes — 变更内容

- 添加**流式支持** — 响应逐步显示，而非完成后一次性展示
- 添加**系统提示词字段** — 像真实 Agent 一样使用系统消息进行测试
- 添加**响应时间测量** — 以毫秒为单位显示延迟
- 添加**测试历史** — 在 localStorage 中保存最近 10 次测试以便比较
- 添加**Token 用量可视化** — 显示提示词与补全 token 比例的进度条
- 改进**错误显示** — 展示提供商特定的错误详情及建议

## Capabilities — 能力

### New Capabilities — 新增能力
- `model-debug-playground`：增强的模型测试 UI，支持流式、系统提示词、延迟追踪和测试历史

### Modified Capabilities — 修改的能力
- （无 — 现有规范尚未正式化）

## Impact — 影响范围

- **仅前端** — 无需后端更改（流式使用现有 `/v1/chat/completions` 端点）
- `web/src/app/(dashboard)/settings/models/debug/page.tsx` — 使用增强功能重写
- **测试**：无需新的后端测试；前端手动验证
