## 1. Workflow Canvas — Core Components

- [x] 1.1 Translate `web/src/components/workflow/node-types.tsx` — node labels ("开始"/"结束"/"工具模式")
- [x] 1.2 Translate `web/src/components/workflow/node-palette.tsx` — type labels ("对话"/"条件"/"工具调用"/"知识检索"/"变量设置")
- [x] 1.3 Translate `web/src/components/workflow/canvas-area.tsx` — handoff edge label ("移交" → "Handoff") + equality check
- [x] 1.4 Translate `web/src/components/workflow/config-panel.tsx` — form labels + fix typo "knowledge-rerieval"
- [x] 1.5 Translate `web/src/components/workflow/agent-palette.tsx` — "已有 Agent"/"暂无 Agent"/"加载中..."
- [x] 1.6 Translate `web/src/components/workflow/template-picker.tsx` — "编排模板"/"暂无可用模板"/"节点"/"连线"

## 2. Workflow Canvas — DSL Bridge & Types

- [x] 2.1 Translate `web/src/lib/dsl-bridge.ts` — NODE_TYPE_LABELS ("对话"/"工具调用"/"条件"/"知识检索"/"变量设置") + "移交" edge labels + equality checks
- [x] 2.2 Translate `web/src/lib/__tests__/dsl-bridge.test.ts` — update assertions for translated labels ("移交" → "Handoff", "工具调用" → "Tool Call")

## 3. Workflow Canvas — Pages

- [x] 3.1 Translate `web/src/app/(dashboard)/workflows/page.tsx` — "工作流"/"新建工作流"/"暂无工作流"/"编辑"/"操作"/"版本"/"创建时间"
- [x] 3.2 Translate `web/src/app/(dashboard)/workflows/new/page.tsx` — "新建工作流"/"名称"/"描述"/"创建"/"返回" + error messages
- [x] 3.3 Translate `web/src/app/(dashboard)/workflows/[id]/page.tsx` — toolbar buttons ("保存"/"验证"/"测试运行"/"编排模板"/"输入"/"历史"/"返回") + status messages + alert() calls

## 4. Other Pages — Agent Management

- [x] 4.1 Translate `web/src/app/(dashboard)/agents/page.tsx` — "Agent 管理"/"创建 Agent"/"导入 Agent"/table headers + status messages

## 5. Other Pages — Knowledge Base

- [x] 5.1 Translate `web/src/app/(dashboard)/knowledge/page.tsx` — "知识库"/"创建知识库"/table headers + empty state
- [x] 5.2 Translate `web/src/app/(dashboard)/knowledge/[id]/page.tsx` — status labels ("等待中"/"解析中"/"已完成"/"失败") + upload/crawl UI
- [x] 5.3 Translate `web/src/app/(dashboard)/knowledge/new/page.tsx` — "创建知识库"/"名称"/"描述"/"创建" + error messages

## 6. Other Pages — Model Management

- [x] 6.1 Translate `web/src/app/(dashboard)/settings/models/page.tsx` — "模型服务商"/"添加服务商"/"连通测试"/"模型列表"/table headers + form labels + alert() calls
- [x] 6.2 Translate `web/src/app/(dashboard)/settings/models/debug/page.tsx` — "模型调试"/"测试配置"/"测试结果"/form labels + status messages

## 7. Other Pages — Auth & Layout

- [x] 7.1 Translate `web/src/app/login/page.tsx` — "登录"/"邮箱"/"密码"/"注册"/"登录中..." + error messages
- [x] 7.2 Translate `web/src/app/register/page.tsx` — "注册"/"邮箱"/"密码"/"确认密码"/"登录" + error messages
- [x] 7.3 Translate `web/src/app/page.tsx` — "Hecate Agent 平台"/"企业级自托管 Agent 平台"
- [x] 7.4 Translate `web/src/components/sidebar.tsx` — "Agent 管理"/"工作流"/"知识库"/"设置"/"退出登录"
- [x] 7.5 Translate `web/src/app/layout.tsx` — title "Hecate - Agent 平台"/description
- [x] 7.6 Translate `web/src/lib/api-client.ts` — "请重新登录" error message
- [x] 7.7 Translate `web/src/components/auth-guard.tsx` — "加载中..."

## 8. Verification

- [x] 8.1 Run `grep -r '[\u4e00-\u9fff]' web/src/ --include='*.ts' --include='*.tsx'` — verify zero Chinese characters remain
- [x] 8.2 Run `cd web && npm test` — verify all tests pass
- [x] 8.3 Run `cd web && npm run build` — verify build succeeds
