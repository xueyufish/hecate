## 1. 前端：内存块编辑器组件

- [x] 1.1 创建 `web/src/components/agent/memory-block-editor.tsx` 组件，展示内存块列表，支持内联编辑、保存/取消按钮和删除确认
- [x] 1.2 添加块内容预览（前 100 字符），带展开/折叠查看完整内容
- [x] 1.3 添加获取块时的加载状态和错误状态显示
- [x] 1.4 实现内联编辑模式：点击内容 → textarea + Save/Cancel 按钮

## 2. 前端：内存块模板

- [x] 2.1 在 `memory-block-editor.tsx` 中定义 4 个模板数据：persona、user_profile、domain_context、task_tracker
- [x] 2.2 添加模板下拉/按钮，一键从模板创建块
- [x] 2.3 处理模板标签已存在时的 409 冲突错误（显示用户友好消息）

## 3. 前端：创建自定义块表单

- [x] 3.1 添加"Add Block"按钮，打开包含字段的表单：label（必填）、content、position（默认 0）、limit（默认 2000）
- [x] 3.2 表单验证：label 必填，最大长度 100；content 最大长度 50000；limit > 0
- [x] 3.3 提交时调用 `POST /api/agents/{id}/memory-blocks`，处理 409 重复标签

## 4. 前端：Agent 配置器集成

- [x] 4.1 在 `AgentConfigurator` 组件中添加"Memory"选项卡（Tools 后的第 5 个选项卡）
- [x] 4.2 Memory 选项卡在编辑模式下显示 `MemoryBlockEditor` 组件（agent_id 可用时）
- [x] 4.3 在创建模式下，Memory 选项卡显示"Save agent first, then add memory blocks"

## 5. 前端：Agent 详情页面

- [x] 5.1 在 Agent 详情页面添加"Memory Blocks"部分，显示块标签和内容预览
- [x] 5.2 添加"Edit in Configurator"链接，跳转到 `/agents/[id]` 并激活 Memory 选项卡
- [x] 5.3 当 Agent 没有块时显示"No memory blocks configured"及添加链接

## 6. 前端：聊天页面内存指示器

- [x] 6.1 在聊天页面的 useEffect 中获取 Agent 的内存块（与 knowledge_base_ids 一起）
- [x] 6.2 在聊天头部显示内存块标签作为徽章（类似 KB 徽章）
- [x] 6.3 当 Agent 没有内存块时不显示任何内容

## 7. 验证

- [x] 7.1 在 `web/` 目录运行 `npm run lint` — 零错误（1 个预先存在的警告）
- [x] 7.2 在 `web/` 目录运行 `npm run build` — 零错误
