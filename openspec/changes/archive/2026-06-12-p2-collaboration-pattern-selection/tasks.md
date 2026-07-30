## 1. Engine Pattern Module — 引擎模式模块

- [x] 1.1 创建 `src/hecate/engine/patterns.py`，包含 `CollaborationPattern` StrEnum（6 个值：SEQUENTIAL、PARALLEL、HANDOFF、BROADCAST、NEGOTIATION、DEBATE）
- [x] 1.2 实现 `infer_pattern(config: GraphConfig) -> CollaborationPattern | None`，包含所有 6 种模式的结构启发式规则
- [x] 1.3 实现 `build_graph_from_pattern(pattern: CollaborationPattern, config: dict) -> GraphConfig`，委托给 `templates.py` 中现有的 `build_*` 函数
- [x] 1.4 为 `CollaborationPattern` 枚举值和字符串表示编写单元测试
- [x] 1.5 为 `infer_pattern()` 编写单元测试 — 使用现有模板 GraphConfig 测试每种模式检测场景
- [x] 1.6 为 `build_graph_from_pattern()` 编写单元测试 — 测试每种模式生成具有正确拓扑的有效 GraphConfig

## 2. JSON Template Gaps — JSON 模板缺口

- [x] 2.1 创建 `src/hecate/data/orchestration_templates/negotiation.json` — 提议者 → 响应者 → 条件（协议检查）→ 循环或结束，类别为 "negotiation"
- [x] 2.2 创建 `src/hecate/data/orchestration_templates/debate.json` — debater_a → debater_b 交替进行，带轮次计数器，可选的法官，类别为 "debate"
- [x] 2.3 通过 `GET /api/orchestration-templates` 和 `GET /api/orchestration-templates/{id}` 验证两个新模板正确加载

## 3. Pattern API Endpoints — 模式 API 端点

- [x] 3.1 创建 `src/hecate/api/management/collaboration_patterns.py`，包含路由器
- [x] 3.2 实现 `GET /api/collaboration-patterns` — 返回 6 种模式定义，包含 id、name、description、parameters（JSON Schema）和预览元数据
- [x] 3.3 实现 `POST /api/collaboration-patterns/{pattern}/generate` — 验证模式枚举，调用 `build_graph_from_pattern()`，返回 Graph DSL JSON
- [x] 3.4 在主应用路由器设置中注册 collaboration_patterns 路由器
- [x] 3.5 增强 `GET /api/orchestration-templates`，通过 `infer_pattern()` 为每个模板包含推断的 `pattern_type` 字段
- [x] 3.6 为 `GET /api/collaboration-patterns` 编写 API 测试 — 验证返回 6 个条目且元数据正确
- [x] 3.7 为 `POST /api/collaboration-patterns/{pattern}/generate` 编写 API 测试 — 测试每种模式的有效生成及错误情况（无效模式、缺少参数）
- [x] 3.8 编写 API 测试，验证模板列表中的 `pattern_type` 字段与预期模式匹配

## 4. Frontend Pattern Selector Component — 前端模式选择器组件

- [x] 4.1 创建 `web/src/components/workflow/pattern-selector.tsx` — 模态对话框，6 种模式的 3×2 卡片网格，每张卡片显示图标、名称、描述和迷你拓扑预览
- [x] 4.2 向 `web/src/lib/workflow-types.ts` 添加模式元数据类型 — `CollaborationPattern`、`PatternDefinition`、`PatternGenerateRequest` 接口
- [x] 4.3 添加用于获取模式列表和生成图谱的 API 客户端函数 — 在组件中直接使用 `api.get()` / `api.post()`

## 5. Frontend Pattern Configuration Dialog — 前端模式配置对话框

- [x] 5.1 创建 `web/src/components/workflow/pattern-config-dialog.tsx` — 每种模式渲染的动态配置表单，带"生成"按钮
- [x] 5.2 为需要可变长度列表的模式（Sequential 阶段、Parallel 工作者、Handoff 专家）实现动态阶段/工作者列表 UI

## 6. Canvas Integration — 画布集成

- [x] 6.1 向工作流画布页面（`web/src/app/(dashboard)/workflows/[id]/page.tsx`）添加"模式"工具栏按钮，与现有的"模板"按钮并列
- [x] 6.2 连接模式选择流程：点击模式 → 打开选择器 → 选择模式 → 打开配置 → 生成 → `dslToReactFlow()` → 填充画布
- [ ] 6.3 在生成模式替换现有画布节点时添加确认对话框
- [x] 6.4 模式生成后自动进入模板自定义模式（重用现有 `isCustomizing` 状态）
- [x] 6.5 验证 TypeScript 编译通过，无新错误

## 7. Verification — 验证

- [x] 7.1 运行 `ruff check src/hecate/ tests/` — 0 错误
- [x] 7.2 运行 `ruff format --check src/ tests/` — 通过
- [x] 7.3 运行 `mypy src/` — 0 错误
- [x] 7.4 运行 `python -m pytest tests/test_engine/test_patterns.py tests/test_api/test_collaboration_patterns.py` — 32/32 测试通过
- [x] 7.5 在 web/ 中运行 `npx tsc --noEmit` — 0 新错误（dsl-bridge 测试中有 1 个预先存在的错误）
