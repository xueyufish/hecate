## 1. 前端基础设施

- [x] 1.1 安装 @xyflow/react、zod 依赖到 web/ 项目
- [x] 1.2 创建 `web/src/lib/workflow-types.ts` — Zod schema 镜像 `graph-dsl.schema.json`（version, name, state, nodes, edges, entry）
- [x] 1.3 创建 `web/src/lib/dsl-bridge.ts` — `dslToReactFlow()` 和 `reactFlowToDsl()` 双向转换函数 + validateDsl()
- [x] 1.4 `validateDsl()` integrated into workflow-types.ts — 校验是否符合 Zod schema，返回错误列表
- [x] 1.5 编写 dsl-bridge 和 dsl-validator 单元测试（round-trip、条件分支、unreachable 警告）

## 2. 节点类型定义

- [x] 2.1 创建 `web/src/components/workflow/node-types.tsx` — ConversationNode（对话节点卡片）
- [x] 2.2 创建 ConditionNode — Condition 节点卡片（多输出 handle）
- [x] 2.3 创建 ToolCallNode — Tool Call 节点卡片
- [x] 2.4 创建 AgentNode — Sub-Agent 节点卡片
- [x] 2.5 创建 KnowledgeRetrievalNode — Knowledge Retrieval 节点卡片（新类型）
- [x] 2.6 创建 VariableSetNode — Variable Set 节点卡片（新类型）
- [x] 2.7 创建 StartNode + EndNode — 开始/结束节点
- [x] 2.8 创建 `web/src/components/workflow/node-palette.tsx` — 左侧节点类型拖拽面板

## 3. 节点配置面板

- [x] 3.1 创建 `web/src/components/workflow/config-panel.tsx` — 右侧配置面板容器（选中节点时显示）
- [x] 3.2 实现 Conversation 配置表单：model 输入、system prompt textarea
- [x] 3.3 实现 Condition 配置表单：expression 输入
- [x] 3.4 实现 Tool Call 配置表单：tool_name 输入
- [x] 3.5 实现 Agent 配置表单：agent_ref 输入
- [x] 3.6 实现 Knowledge Retrieval 配置表单：kb_ids 输入 + query_template textarea + top_k 数字
- [x] 3.7 实现 Variable Set 配置表单：variable_name 输入 + value 输入

## 4. 画布编辑器页面

- [x] 4.1 创建 `web/src/app/(dashboard)/workflows/page.tsx` — 工作流列表页（表格 + 创建按钮）
- [x] 4.2 创建 `web/src/app/(dashboard)/workflows/new/page.tsx` — 新建工作流页面（名称输入 + DSL 编辑）
- [x] 4.3 创建 `web/src/app/(dashboard)/workflows/[id]/page.tsx` — 画布编辑器页面（React Flow + 节点面板 + 配置面板 + 工具栏）
- [x] 4.4 实现画布工具栏：保存按钮、验证按钮、测试运行按钮
- [x] 4.5 实现自动保存：画布变更时 debounced 2s 调用 PUT /api/workflows/{id}
- [x] 4.6 添加侧边栏"工作流"导航入口

## 5. Workflow API 后端

- [x] 5.1 检查并完善 `api/management/workflows.py` — POST /api/workflows 创建工作流 + 编译 DSL + 创建 version 1
- [x] 5.2 实现 GET /api/workflows 分页列表
- [x] 5.3 实现 GET /api/workflows/{id} 返回工作流 + 当前版本 DSL
- [x] 5.4 实现 PUT /api/workflows/{id} 更新工作流（DSL 变更时编译并创建新版本，名称变更不创建版本）
- [x] 5.5 实现 DELETE /api/workflows/{id} 软删除
- [x] 5.6 实现 GET /api/workflows/{id}/versions 版本历史列表
- [x] 5.7 实现 POST /api/workflows/{id}/validate — 调用 compiler dry-run 返回 valid/errors
- [x] 5.8 在 main.py 注册工作流路由（如未注册）
- [x] 5.9 编写 Workflow API 单元测试 — validate + test-run + version bumping（7 tests in test_workflow_extended.py）

## 6. 引擎扩展 — 新节点类型

- [x] 6.1 更新 `schemas/graph-dsl.schema.json` 添加 `knowledge-retrieval` 和 `variable-set` 节点类型
- [x] 6.2 更新 `engine/types.py` NodeType enum 添加 KNOWLEDGE_RETRIEVAL 和 VARIABLE_SET
- [x] 6.3 更新 `engine/graph_dsl.py` — 已通过 JSON Schema + NodeType enum lookup 自动支持，无需代码改动
- [x] 6.4 更新 test_runner.py _TestWorker — knowledge-retrieval mock 返回检索文档 + variable-set mock 写入 channel 变量（6 node-type-specific handlers）
- [x] 6.5 编写新节点类型引擎单元测试 — 9 tests in test_new_node_types.py（parse + compile + execute for each type + mixed pipeline）

## 7. 测试运行

- [x] 7.1 创建 `src/hecate/services/workflow/test_runner.py` — 封装 PregelRuntime 执行 + 每节点状态收集
- [x] 7.2 实现 POST /api/workflows/{id}/test-run — 接受 input payload，调用 test_runner，返回节点执行结果
- [x] 7.3 实现 mock 模式（body.mock=true）— 替换 LLM 调用为 canned response
- [x] 7.4 实现 GET /api/workflows/{id}/runs — 测试运行历史列表（需要持久化模型，P2+）
- [x] 7.5 前端测试运行面板：运行按钮 + 结果显示（嵌入画布编辑器页面）
- [x] 7.6 编写 test-runner 和 test-run API 单元测试 — 5 service tests + covered by E2E

## 8. 集成测试与验证

- [x] 8.1 E2E 测试：创建工作流 → 添加节点 → 连接边 → 保存 → DSL round-trip 一致
- [x] 8.2 E2E 测试：测试运行工作流 → 验证节点状态和输出
- [x] 8.3 E2E 测试：条件分支 → 验证 true/false 路径执行正确 + 新节点类型 knowledge-retrieval + variable-set
- [x] 8.4 运行 ruff check + ruff format + mypy + pytest 全量验证 — 662 tests passing
- [x] 8.5 Next.js build 验证前端编译通过
