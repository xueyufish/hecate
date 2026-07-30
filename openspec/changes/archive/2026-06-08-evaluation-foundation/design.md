## Context — 上下文

Hecate 拥有完整的 RAG 管道（`services/rag/`——解析器、分块器、嵌入、带 RRF 分数融合的混合搜索）和智能体运行时（`engine/pregel.py`——带 LLM/工具/条件工作器的图执行）。RAG 服务已经暴露了 `search_with_score_breakdown()` 返回带 `score`、`dense_score`、`sparse_score` 字段的 `HybridSearchResult`。然而，**没有评估基础设施**——无法衡量检索质量、响应忠实度或智能体行为随时间的变化。

研究笔记（`docs/research/notes/eval-security-prompt-comparison.md`、`docs/research/notes/langfuse.md`）记录了三个参考平台：Ragas（RAG 指标）、LangFuse（Score 数据模型 + LLM-as-Judge）和 DSPy（提示优化）。Ragas 最适合 7.1 RAG 评估——它提供了经过实战测试的指标（faithfulness、context precision/recall、answer relevancy），从头实现这些指标需要数周时间。

当前的架构约束：
- `models/` 层：SQLAlchemy ORM + Pydantic schemas，异步优先
- `services/` 层：业务逻辑，依赖于 `models/` 和 `engine/ports`
- `api/` 层：FastAPI 路由器，依赖于 `services/`
- `engine/` 层：零外部依赖——评估保持在 `services/` 中
- 数据库：带 Alembic 迁移的异步 SQLAlchemy
- 所有公共方法需要类型注解，`from __future__ import annotations`

## Goals / Non-Goals — 目标 / 非目标

**Goals — 目标：**
- 建立 P3 的 40+ 评估器将扩展的 Evaluator ABC 框架
- 实现 4 个 RAG 评估器（context precision、context recall、faithfulness、answer relevancy）
- 实现 3 个智能体评估器（correctness、relevancy、completeness）
- 提供带 PostgreSQL 持久化的评估数据集 CRUD
- 提供用于数据集管理和评估执行的 REST API
- 支持每个评估器的 LLM 配置（每个评估器可以使用不同的模型）

**Non-Goals — 非目标：**
- P3 评估功能（40+ 评估器、AI 合成数据集、在线/离线任务、仪表板、人工标注）
- 工作流级别评估（7.3）
- 提示优化（7.6a、7.6b）
- 评估管理的前端 UI
- 实时在线评估/采样
- CI/CD 集成

## Decisions — 决策

### D1: Evaluator ABC vs 仅 Ragas

**Decision — 决策**：自建的 `Evaluator` ABC 框架，Ragas 作为可选后端。

**Rationale — 理由**：P3 需要 40+ 评估器——许多将是自定义的（领域特定、企业策略检查）。Ragas 仅覆盖 RAG 指标。自建的 ABC 带可插拔后端给我们：
- 所有评估器的一致接口（RAG、智能体、自定义）
- Ragas 作为可选的 `[rag]` 依赖——未安装 Ragas 的用户仍可获得智能体评估器
- 易于添加新评估器，无需修改框架代码

**Alternatives considered — 考虑的替代方案**：
- 仅 Ragas：无法扩展到智能体评估器或自定义企业指标
- 完全自建：需要从头实现和验证 faithfulness/context precision——数周工作，无明显好处

### D2: Ragas 作为可选依赖

**Decision — 决策**：`ragas` 声明在 `pyproject.toml` 的 `[rag]` 可选依赖组中。

**Rationale — 理由**：Ragas 是一个重量级依赖（引入了 LangChain、多个 LLM 客户端库）。使其可选：
- 保持基础安装轻量
- 如果未安装 `ragas`，RAG 评估器引发 `ImportError` 并带帮助消息
- 仅需要智能体评估的用户不需要 Ragas
- 遵循现有模式：`[llm]`、`[rag]`、`[temporal]`、`[security]`、`[dev]` 组已存在

### D3: 每个评估器的 LLM 配置

**Decision — 决策**：每个 `Evaluator` 实例接受 `llm_config` 参数，指定模型、温度和 API 基础地址。

**Rationale — 理由**：
- 评估 LLM 应不同于生产 LLM 以避免偏差
- 一些评估器需要更强的模型（faithfulness），其他可以使用更便宜的模型（格式检查）
- 与 Hecate 的多模型架构一致（LiteLLM 路由）
- 默认：使用智能体的配置模型作为回退

### D4: 数据模型——4 个表

**Decision — 决策**：四个新的 SQLAlchemy 模型：

| 模型 | 用途 |
|-------|---------|
| `EvaluationDatasetModel` | 带元数据的命名数据集 |
| `EvaluationItemModel` | 单个测试用例（query、expected_answer、context） |
| `EvaluationRunModel` | 评估器对数据集的执行 |
| `EvaluationScoreModel` | 来自一个评估器对一个项的单个分数 |

**Rationale — 理由**：遵循 LangFuse 的 Score 数据模型模式（数据集 → 运行 → 分数）。规范化设计允许：
- 跨多个运行重用数据集
- 跟踪随时间的分数历史
- 每个项的分数下钻
- 未来对在线评估的支持（直接分数跟踪）

### D5: 评估执行——同步批处理

**Decision — 决策**：评估运行同步执行（批处理）——非流式，非异步后台。

**Rationale — 理由**：对于 P2，评估是一种离线活动——用户显式运行并等待结果。P3 添加了在线评估（7.2c）和计划任务（13.9），将引入异步/后台执行。保持 P2 简单避免了过早的复杂性。

### D6: API 设计

**Decision — 决策**：`/api/evaluation/` 下的三个资源组：

| 端点 | 用途 |
|----------|---------|
| `/api/evaluation/datasets` | 数据集 CRUD |
| `/api/evaluation/datasets/{id}/items` | 数据集内的项管理 |
| `/api/evaluation/runs` | 创建/列出/获取评估运行 |
| `/api/evaluation/runs/{id}/scores` | 特定运行的分数 |

遵循现有的 Hecate API 模式（FastAPI 路由器、Pydantic schemas、异步端点）。

## Risks / Trade-offs — 风险 / 权衡

| 风险 | 影响 | 缓解措施 |
|------|--------|------------|
| Ragas API 不稳定性（v0.x） | RAG 评估器在升级时中断 | 锁定 Ragas 版本；在适配器层中包装 Ragas 调用；自建 ABC 将框架与 Ragas 内部隔离 |
| LLM-as-Judge 成本 | 每次评估运行对每个项的每个评估器调用 LLM | 每个评估器的 LLM 配置允许使用更便宜的模型；数据集大小由用户控制 |
| LLM-as-Judge 延迟 | 大数据集需要数分钟评估 | 记录预期延迟；P3 添加异步/后台执行 |
| 评估质量取决于法官 LLM | 弱法官 LLM 产生不可靠的分数 | 默认使用强模型（GPT-4o）；记录评估质量与法官 LLM 能力成正比 |
| 数据库迁移（4 个新表） | 标准迁移风险 | 遵循现有的 Alembic 模式；无需对现有表进行 schema 更改 |
