## 1. API 层 — Agent 配置端点

- [x] 1.1 向 `src/hecate/api/agents.py` 添加 `PATCH /api/agents/{id}`，接受部分 AgentModel 更新，返回更新后的 agent
- [x] 1.2 增强 `POST /api/agents` 以返回创建 agent 的完整响应（包括所有配置字段）
- [x] 1.3 添加 `GET /api/agents/validate-name?name=X` 端点，用于检查 agent 名称是否可用（前端进行名称唯一性验证）

## 2. 前端 — Agent 配置表单

- [x] 2.1 创建 `web/src/app/(dashboard)/agents/new/page.tsx`——新 agent 创建页面，加载 AgentCreateSchema 的空表单
- [x] 2.2 创建 `web/src/app/(dashboard)/agents/[id]/edit/page.tsx`——现有 agent 编辑页面，加载现有 agent 数据到表单
- [x] 2.3 创建 `web/src/components/agents/agent-form.tsx`——可复用的 agent 配置表单组件，包含 3 个部分（基本信息、模型、工具/知识库）
- [x] 2.4 创建 `web/src/components/agents/model-presets.ts`——常见模型建议列表（名称、提供者、上下文窗口），用于自动补全下拉列表
- [x] 2.5 在表单中实现客户端验证——必填字段标记、persona 长度计数器、温度范围滑块、模型名称格式
- [x] 2.6 实现表单提交——`POST /api/agents`（新建时）、`PATCH /api/agents/{id}`（编辑时）、成功重定向到 agent 列表页面、错误显示
- [x] 2.7 实现 Agent 复制功能——添加到编辑页面的操作按钮，调用 `POST /api/agents` 并复制所有字段（名称添加"Suffix"）

## 3. 前端 — 内联测试面板

- [x] 3.1 创建 `web/src/components/agents/agent-test-panel.tsx`——可折叠面板，位于 agent 表单下方，包含消息输入框和对话显示区域
- [x] 3.2 实现在测试面板中发送测试消息——`POST /api/conversations` 附带测试消息，显示 agent 回复为内联聊天
- [x] 3.3 一键重置测试对话——清除聊天历史以重新开始

## 4. 前端 — 画布集成

- [x] 4.1 更新 AGENT 节点上下文菜单——添加"编辑"操作，路由到 `/agents/{agentId}/edit`
- [x] 4.2 更新 AGENT 节点上下文菜单——添加"复制"操作，路由到 `/agents/new?copyFrom={agentId}`
- [x] 4.3 添加面包屑导航——工作流画布 → Agent 配置器的导航，在配置页面标题中显示父工作流链接

## 5. 测试

- [x] 5.1 创建 `tests/test_api/test_agents_patch.py`——测试部分更新、完整更新、无效字段、agent 未找到
- [x] 5.2 更新现有 agent API 测试——确保 POST 返回完整响应
