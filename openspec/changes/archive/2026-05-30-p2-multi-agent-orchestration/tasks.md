## 1. 引擎层 — Agent 执行基础

- [x] 1.1 在 `src/hecate/engine/ports.py` 的 `EnginePort` 中添加 `agent_execute` 抽象方法，签名：`async def agent_execute(self, agent_id: UUID, messages: list[dict], channel_snapshot: dict, context: dict | None = None) -> dict`
- [x] 1.2 在 `src/hecate/engine/workers/agent_worker.py` 中实现 `AgentWorker`——`Worker` 子类，通过调用 `port.agent_execute()` 处理 AGENT 类型节点，agent_id 来自节点配置
- [x] 1.3 在 `graph_dsl.py` 的 AGENT 节点配置解析中添加 `invocation_mode` 字段支持——接受 `"tool"` 或 `"direct"`（默认）
- [x] 1.4 更新 `graph-dsl.schema.json`，向 agent 节点配置 schema 添加 `invocation_mode`, `agent_id` 字段
- [x] 1.5 添加 handoff 边触发支持——将边上的 `type: "handoff"` 字段解析为 `Edge` 数据类中的 `trigger="handoff"`
- [x] 1.6 在 `GraphCompiler._detect_unreachable()` 中添加 handoff 循环检测——在循环 handoff 链上抛出 `GraphCompilationError`

## 2. 服务层 — Agent 执行 & Handoff

- [x] 2.1 在服务层实现 `EnginePortAdapter.agent_execute()`——按 ID 解析 `AgentModel`，构建隔离上下文（persona + 工具 + 知识库），通过 `ConversationService` 调用 LLM，返回响应 dict
- [x] 2.2 创建 `src/hecate/services/orchestration/handoff.py`——`HandoffToolProvider`，基于图 handoff 边生成 `handoff_to_agent` 工具 schema 并注入到 agent 工具列表
- [x] 2.3 实现 handoff 工具执行——当 LLM 调用 `handoff_to_agent(target=X)` 时，通过 `WorkerResult` 返回 `Command(goto=X)`
- [x] 2.4 创建 `src/hecate/services/orchestration/agent_tool.py`——`AgentToolProvider`，当 `invocation_mode: "tool"` 时将目标 agent 暴露为可调用工具，从 agent persona 生成工具 schema
- [x] 2.5 实现 agent-as-tool 执行——当父 LLM 调用 `agent_{name}` 工具时，通过 `agent_execute()` 执行目标 agent 并返回结果作为工具响应
- [x] 2.6 将 `AgentWorker` 接入 `PregelRuntime`——更新 `WorkflowTestRunner._TestWorker`，在非模拟模式下使用真实的 `AgentWorker`

## 3. API 层 — 编排模板

- [x] 3.1 创建 `src/hecate/api/management/orchestration_templates.py`——`GET /api/orchestration-templates` 列表端点，返回模板元数据（id, name, description, category, preview）
- [x] 3.2 添加 `GET /api/orchestration-templates/{template_id}` 详情端点，返回完整 Graph DSL JSON
- [x] 3.3 在 `src/hecate/main.py` 中注册编排模板路由
- [x] 3.4 在 `src/hecate/data/orchestration_templates/` 中创建模板数据文件——`customer-service-triage.json`, `content-pipeline.json`, `hierarchical-supervisor.json`

## 4. 测试 — 后端

- [x] 4.1 创建 `tests/test_engine/test_agent_worker.py`——使用 mock 端口测试 AgentWorker（有效 agent_id、缺失 agent_id、上下文隔离）
- [x] 4.2 创建 `tests/test_engine/test_handoff.py`——测试 handoff 工具生成、Command(goto) 结果、循环检测
- [x] 4.3 向 `tests/test_engine/test_graph_dsl.py` 添加 handoff 边解析测试——解析 handoff 触发、验证源/目标为 agent 节点
- [x] 4.4 创建 `tests/test_services/test_orchestration/test_agent_tool.py`——测试 AgentToolProvider schema 生成和工具执行
- [x] 4.5 创建 `tests/test_api/test_orchestration_templates.py`——测试列出模板、获取模板详情、404 处理
- [x] 4.6 创建 `tests/test_api/test_e2e_multi_agent.py`——端到端测试：创建 agent → 构建多 agent 图 → 使用 mock 测试运行 → 验证 agent 节点执行顺序

## 5. 前端 — 画布多 Agent 支持

- [x] 5.1 更新 `web/src/lib/dsl-bridge.ts`——在 `dslToReactFlow()` 和 `reactFlowToDsl()` 双向转换中支持 handoff 边触发
- [x] 5.2 更新 `web/src/components/workflow/node-types.tsx`——增强 AgentNode 组件以显示 agent 名称、模型和调用模式徽章
- [x] 5.3 创建 `web/src/components/workflow/agent-palette.tsx`——侧边栏面板，从 `GET /api/agents` 列出可用 agent，可拖入画布
- [x] 5.4 更新 `web/src/components/workflow/canvas-area.tsx`——添加 agent 放置处理器、边类型选择对话框（handoff vs invoke-as-tool）、handoff 虚线边渲染
- [x] 5.5 创建 `web/src/components/workflow/template-picker.tsx`——模态对话框，从 `GET /api/orchestration-templates` 加载模板，将选中模板应用到画布
- [x] 5.6 更新 `web/src/app/(dashboard)/workflows/[id]/page.tsx`——向工具栏添加模板选择器按钮，将 agent 调色板集成到侧边栏，在测试运行期间添加执行状态高亮

## 6. 文档和集成

- [x] 6.1 使用 handoff 边类型和 agent 节点配置字段更新 `schemas/graph-dsl.schema.json`
- [x] 6.2 更新 `docs/features/feature-catalog.md`——将 2.1, 2.2, 2.7 标记为已实现
- [x] 6.3 运行完整测试套件、ruff、mypy——确保全部通过
