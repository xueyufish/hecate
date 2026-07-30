## Context — 背景

Hecate 的引擎层在 `engine/templates.py` 中有 9 个模板构建器函数，为常见模式生成 `GraphConfig` 实例：chat、three-layer、fan-out、conditional、reflection、sequential、broadcast、negotiation 和 debate。`data/orchestration_templates/` 目录包含 8 个由 API 加载的 JSON 模板文件。画布前端（React Flow）已支持模板自定义模式、用于加载模板的 `dslToReactFlow()` 和用于保存的 `reactFlowToDsl()`。

**当前状态**：不存在统一的模式词汇表。模板使用自由形式的 `category` 字符串（"pipeline"、"broadcast"、"delegation"、"customer-service"、"content"）。没有 `PatternType` 枚举，没有模式推断，也没有从模式选择生成图谱的 API。两种模式（negotiation、debate）在 templates.py 中有构建器函数，但缺少 JSON 模板文件。

**约束**：
- 引擎层零外部依赖（仅 `jsonschema` 例外）
- 画布页面使用 `useState`（无 Zustand 存储）
- Graph DSL 模式在 `schemas/graph-dsl.schema.json` 中版本化管理
- API 模板从静态 JSON 文件加载（非数据库）

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 提供规范的 `CollaborationPattern` 枚举，作为 6 种模式类型的单一事实来源
- 启用从任何 `GraphConfig` 进行后端模式推断
- 支持通过可配置参数从模式选择生成图谱
- 通过 REST API 暴露模式列表和生成
- 提供带卡片网格 UI 和配置对话框的前端模式选择器
- 填补 2 个缺失的 JSON 模板（negotiation、debate）
- 增强现有模板 API，包含推断的 `pattern_type`

**非目标：**
- 将用户创建的模式持久化到数据库（未来的 P3+）
- 用于在生成前修改模式模板的可视化图谱编辑器
- 模式组合（将多个模式合并到一个图谱中）
- 用户自定义模式创建
- 对 Pregel 运行时、编译器或核心引擎类型（GraphConfig、Edge 等）的变更

## Decisions — 决策

### D1：新建 `engine/patterns.py` 模块（不扩展 templates.py）

**决策**：创建一个新的 `engine/patterns.py` 模块，包含 `CollaborationPattern` 枚举、`infer_pattern()` 和 `build_graph_from_pattern()`。

**理由**：`templates.py` 是一个工厂函数集合（9 个函数，964 行）。模式分类和推断是根本不同的关注点 — 一个生成图谱，另一个分析图谱。将它们分离有助于保持 `templates.py` 专注于图谱构建，并使模式系统可独立测试。构建器函数在适用时委托给现有的模板函数。

**备选方案**：
- 添加到 `types.py`：已拒绝 — `types.py` 是纯数据定义（数据类/枚举，无逻辑）
- 添加到 `templates.py`：已拒绝 — 会将图谱构建与图谱分析混合；文件已有 964 行

### D2：模式构建器委托给现有模板函数

**决策**：`build_graph_from_pattern()` 应委托给 `templates.py` 中相应的 `build_*` 函数（例如，SEQUENTIAL 使用 `build_sequential_pipeline()`）。它作为一个规范化参数接口的外观。

**理由**：避免重复图谱构建逻辑。9 个模板函数经过充分测试，编码了正确的图谱拓扑。模式构建器将统一参数模式转换为函数特定的参数。

### D3：模式推断使用结构启发式方法（非机器学习）

**决策**：`infer_pattern()` 使用基于节点类型、边触发器和通道配置的确定性结构规则。

**理由**：模式具有清晰的结构特征 — FAN_OUT/MERGE 节点 → parallel、handoff 触发器 → handoff、共享 TOPIC → broadcast、带条件的循环 → negotiation/debate。无需机器学习或模糊匹配。

### D4：模式选择器作为对话框（非内联面板）

**决策**：模式选择器以模态对话框形式打开，包含 3×2 卡片网格，随后是第二步配置对话框。

**理由**：与现有模板选择器交互模式（对话框覆盖）一致。两步流程（选择 → 配置）比将所有内容塞入一个面板更清晰。与 Coze 和 Dify 的模板/模式选择器一致。

### D5：无需 Graph DSL 模式变更

**决策**：不要向 Graph DSL JSON Schema 添加 `pattern_type` 字段。模式类型在运行时推断，不存储在图谱定义中。

**理由**：保持模式推断与 DSL 分离可避免模式版本化复杂性，并保持图谱定义的可移植性。从模式生成的图谱与手工构建的图谱无法区分 — 两者都是有效的 Graph DSL。

**备选方案**：
- 向模式添加可选的 `pattern_type`：已拒绝 — 添加的元数据在图谱被编辑时可能过时；推断更可靠

### D6：用于模式端点的新 API 文件

**决策**：创建 `api/management/collaboration_patterns.py` 用于模式列表和生成端点，与 `orchestration_templates.py` 分开。

**理由**：模板是静态 JSON 文件；模式是动态生成系统。不同的关注点，不同的端点，不同的缓存策略。模板最终可能迁移到数据库存储（P3+），而模式将保持为引擎层逻辑。

## Risks / Trade-offs — 风险 / 权衡

**[模式推断准确性]** → 启发式规则可能错误分类边缘情况图谱（例如，同时包含 FAN_OUT 和 handoff 边的图谱）。**缓解措施**：`infer_pattern()` 对模糊图谱返回 `None`；模板 API 优雅地处理 `null` 模式类型。

**[构建器参数爆炸]** → 6 种具有不同参数的模式使 API 表面复杂化。**缓解措施**：每种模式都有定义良好的 JSON Schema 参数；前端根据 `GET /api/collaboration-patterns` 返回的模式动态渲染字段。

**[前端对话框复杂度]** → 6 种不同的配置表单可能导致代码重复。**缓解措施**：使用由 API 的模式参数模式驱动的动态表单渲染器；只有"阶段/工作者列表"UI 需要自定义组件。

**[模板目录增长超过 8]** → 添加 negotiation.json 和 debate.json 将目录增加到 10 个模板。**缓解措施**：模板是轻量级 JSON 文件；此规模下无性能问题。
