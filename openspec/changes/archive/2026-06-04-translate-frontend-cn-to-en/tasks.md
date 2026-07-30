## 1. 工作流画布——核心组件

- [x] 1.1 翻译 `web/src/components/workflow/node-types.tsx` —— 节点标签（"开始"/"结束"/"工具模式"）
- [x] 1.2 翻译 `web/src/components/workflow/node-palette.tsx` —— 类型标签（"对话"/"条件"/"工具调用"/"知识检索"/"变量设置"）
- [x] 1.3 翻译 `web/src/components/workflow/canvas-area.tsx` —— handoff 边标签（"移交" → "Handoff"）+ 相等性检查
- [x] 1.4 翻译 `web/src/components/workflow/config-panel.tsx` —— 表单标签 + 修复拼写错误 "knowledge-rerieval"
- [x] 1.5 翻译 `web/src/components/workflow/agent-palette.tsx` —— "已有 Agent"/"暂无 Agent"/"加载中..."
- [x] 1.6 翻译 `web/src/components/workflow/template-picker.tsx` —— "编排模板"/"暂无可用模板"/"节点"/"连线"

## 2. 工作流画布——DSL 桥接与类型

- [x] 2.1 翻译 `web/src/lib/dsl-bridge.ts` —— NODE_TYPE_LABELS（"对话"/"工具调用"/"条件"/"知识检索"/"变量设置"）+ "移交" 边标签 + 相等性检查
- [x] 2.2 翻译 `web/src/lib/__tests__/dsl-bridge.test.ts` —— 更新翻译后标签的断言（"移交" → "Handoff"，"工具调用" → "Tool Call"）

## 3. 工作流画布——页面

- [x] 3.1 翻译 `web/src/app/(dashboard)/workflows/page.tsx` —— "工作流"/"新建工作流"/"暂无工作流"/"编辑"/"操作"/"版本"/"创建时间"
- [x] 3.2 翻译 `web/src/app/(dashboard)/workflows/new/page.tsx` —— "新建工作流"/"名称"/"描述"/"创建"/"返回" + 错误消息
- [x] 3.3 翻译 `web/src/app/(dashboard)/workflows/[id]/page.tsx` —— 工具栏按钮（"保存"/"验证"/"测试运行"/"编排模板"/"输入"/"历史"/"返回"）+ 状态消息 + alert() 调用

## 4. 其他页面——Agent 管理

- [x] 4.1 翻译 `web/src/app/(dashboard)/agents/page.tsx` —— "Agent 管理"/"创建 Agent"/"导入 Agent"/表头 + 状态消息

## 5. 其他页面——知识库

- [x] 5.1 翻译 `web/src/app/(dashboard)/knowledge/page.tsx` —— "知识库"/"创建知识库"/表头 + 空状态
- [x] 5.2 翻译 `web/src/app/(dashboard)/knowledge/[id]/page.tsx` —— 状态标签（"等待中"/"解析中"/"已完成"/"失败"）+ 上传/爬取 UI
- [x] 5.3 翻译 `web/src/app/(dashboard)/knowledge/new/page.tsx` —— "创建知识库"/"名称"/"描述"/"创建" + 错误消息

## 6. 其他页面——模型管理

- [x] 6.1 翻译 `web/src/app/(dashboard)/settings/models/page.tsx` —— "模型服务商"/"添加服务商"/"连通测试"/"模型列表"/表头 + 表单标签 + alert() 调用
- [x] 6.2 翻译 `web/src/app/(dashboard)/settings/models/debug/page.tsx` —— "模型调试"/"测试配置"/"测试结果"/表单标签 + 状态消息

## 7. 其他页面——认证与布局

- [x] 7.1 翻译 `web/src/app/login/page.tsx` —— "登录"/"邮箱"/"密码"/"注册"/"登录中..." + 错误消息
- [x] 7.2 翻译 `web/src/app/register/page.tsx` —— "注册"/"邮箱"/"密码"/"确认密码"/"登录" + 错误消息
- [x] 7.3 翻译 `web/src/app/page.tsx` —— "Hecate Agent 平台"/"企业级自托管 Agent 平台"
- [x] 7.4 翻译 `web/src/components/sidebar.tsx` —— "Agent 管理"/"工作流"/"知识库"/"设置"/"退出登录"
- [x] 7.5 翻译 `web/src/app/layout.tsx` —— 标题 "Hecate - Agent 平台"/描述
- [x] 7.6 翻译 `web/src/lib/api-client.ts` —— "请重新登录" 错误消息
- [x] 7.7 翻译 `web/src/components/auth-guard.tsx` —— "加载中..."

## 8. 验证

- [x] 8.1 运行 `grep -r '[\u4e00-\u9fff]' web/src/ --include='*.ts' --include='*.tsx'` —— 验证零中文字符残留
- [x] 8.2 运行 `cd web && npm test` —— 验证所有测试通过
- [x] 8.3 运行 `cd web && npm run build` —— 验证构建成功