## Why — 为什么

Hecate 拥有完整的 RAG 管道（导入 → 分块 → 嵌入 → 混合搜索）和智能体运行时（图执行 → LLM 调用 → 工具调用），但**无法衡量其质量**。没有评估，就没有用于改进检索准确性、响应忠实度或智能体行为的反馈循环。这是 P3 的 40+ 内置评估器、评估数据集和报告仪表板的先决条件。

## What Changes — 变更内容

- **评估器框架**：抽象基类 `Evaluator`，带有异步 `evaluate()` 接口、每个评估器的 LLM 配置和结构化的 `Score` 输出
- **评估数据集管理**：包含（query、expected_answer、context）元组的测试数据集的 CRUD，存储在 PostgreSQL 中
- **RAG 评估器（7.1）**：Context Precision、Context Recall、Faithfulness、Answer Relevancy——通过可选的 Ragas 集成实现（`[rag]` 依赖）
- **智能体评估器（7.2）**：Correctness、Relevancy、Completeness——使用 LLM-as-Judge 模式自行实现
- **评估执行引擎**：对数据集运行评估任务，聚合分数，生成 `EvaluationRun` 结果
- **REST API**：`/api/evaluation/datasets`、`/api/evaluation/runs`、`/api/evaluation/scores` 端点
- **数据库模型**：SQLAlchemy async 中的 `EvaluationDatasetModel`、`EvaluationItemModel`、`EvaluationRunModel`、`EvaluationScoreModel`

## Capabilities — 能力

### New Capabilities — 新能力
- `evaluation-framework`：Evaluator ABC、Score 类型、评估执行引擎、每个评估器的 LLM 配置
- `evaluation-dataset`：数据集 CRUD、项管理、数据导入/导出
- `rag-evaluation`：特定于 RAG 的评估器（context precision、context recall、faithfulness、answer relevancy），带可选的 Ragas 后端
- `agent-evaluation`：特定于智能体的评估器（correctness、relevancy、completeness），使用 LLM-as-Judge
- `evaluation-api`：用于数据集、运行和分数的 REST API 端点

### Modified Capabilities — 修改的能力
<!-- 没有现有能力需求正在更改 -->

## Impact — 影响

- **新代码**：`services/evaluation/`（服务、Evaluator ABC、数据集管理器、类型）、`models/evaluation.py`（4 个新 ORM 模型）、`api/management/evaluation.py`（REST 路由）
- **新依赖**：`ragas` 作为 `pyproject.toml` 中的可选 `[rag]` 依赖
- **数据库迁移**：4 个新表（evaluation_datasets、evaluation_items、evaluation_runs、evaluation_scores）
- **无破坏性变更**：所有新增代码，无现有 API 修改
