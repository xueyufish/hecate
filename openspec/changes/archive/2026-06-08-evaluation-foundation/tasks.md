## Tasks — 任务

### Task 1: Evaluation types and Evaluator ABC — 任务 1：评估类型和 Evaluator ABC
- [x] 创建 `src/hecate/services/evaluation/__init__.py` 带模块文档字符串
- [x] 创建 `src/hecate/services/evaluation/types.py` 包含 `Score`（metric_name、value 0.0–1.0、reasoning、source）、`EvalInput`（query、retrieved_contexts、generated_answer、expected_answer、tool_calls、metadata）、`EvalOutput`（scores、metadata、duration_ms）、`LLMConfig`（model、temperature、api_base）、`EvaluationRunResult`（run_id、dataset_id、每个项的分数、每个指标的平均值、total_duration_ms）
- [x] 创建 `src/hecate/services/evaluation/evaluator.py` 包含抽象 `Evaluator` 类（name、description 属性；抽象异步 `evaluate(input: EvalInput) -> EvalOutput`）
- [x] 在 `Score.__post_init__` 中添加 `value` 范围验证（0.0–1.0）
- [x] 添加 `source` 枚举验证（"llm_judge"、"deterministic"、"human"）

**文件**：`src/hecate/services/evaluation/types.py`、`src/hecate/services/evaluation/evaluator.py`
**规约**：evaluation-framework

### Task 2: Database models and Alembic migration — 任务 2：数据库模型和 Alembic 迁移
- [x] 创建 `src/hecate/models/evaluation.py` 包含 4 个 SQLAlchemy 模型：
  - `EvaluationDatasetModel`（id UUID、name、description、metadata_ JSON、created_at、updated_at、items 关系）
  - `EvaluationItemModel`（id UUID、dataset_id FK、query TEXT NOT NULL、expected_answer TEXT nullable、context JSON nullable、metadata_ JSON nullable、created_at、updated_at）
  - `EvaluationRunModel`（id UUID、dataset_id FK、evaluator_configs JSON、status ENUM pending/running/completed/failed、started_at、completed_at、created_at）
  - `EvaluationScoreModel`（id UUID、run_id FK、item_id FK、metric_name VARCHAR、value FLOAT、reasoning TEXT nullable、source VARCHAR、created_at）
- [x] 创建 Pydantic schemas：`EvaluationDatasetCreateSchema`、`EvaluationDatasetUpdateSchema`、`EvaluationDatasetReadSchema`、`EvaluationItemCreateSchema`、`EvaluationItemReadSchema`、`EvaluationRunCreateSchema`、`EvaluationRunReadSchema`、`EvaluationScoreReadSchema`
- [x] 生成 Alembic 迁移：`alembic revision --autogenerate -m "add evaluation tables"`
- [x] 验证迁移干净应用：`alembic upgrade head`

**文件**：`src/hecate/models/evaluation.py`、`alembic/versions/`
**规约**：evaluation-dataset

### Task 3: Dataset service — 任务 3：数据集服务
- [x] 创建 `src/hecate/services/evaluation/dataset_service.py` 包含 `EvaluationDatasetService` 类
- [x] 实现异步 `create_dataset(name, description, metadata) -> EvaluationDatasetReadSchema`
- [x] 实现异步 `get_dataset(dataset_id) -> EvaluationDatasetReadSchema`
- [x] 实现异步 `list_datasets(page, page_size) -> PaginatedResult`
- [x] 实现异步 `update_dataset(dataset_id, name, description, metadata) -> EvaluationDatasetReadSchema`
- [x] 实现异步 `delete_dataset(dataset_id) -> None`（级联删除项）
- [x] 实现异步 `add_items(dataset_id, items: list[EvaluationItemCreateSchema]) -> int`
- [x] 实现异步 `list_items(dataset_id, page, page_size) -> PaginatedResult`
- [x] 实现异步 `delete_item(dataset_id, item_id) -> None`
- [x] 实现异步 `import_json(dataset_id, json_data: list[dict]) -> ImportStats`
- [x] 实现异步 `export_json(dataset_id) -> list[dict]`
- [x] 在 add_items 上验证 query 非空

**文件**：`src/hecate/services/evaluation/dataset_service.py`
**规约**：evaluation-dataset

### Task 4: RAG evaluators (Ragas-backed) — 任务 4：RAG 评估器（Ragas 后端）
- [x] 在 `pyproject.toml` 中将 `ragas` 添加到 `[rag]` 可选依赖，带锁定版本
- [x] 创建 `src/hecate/services/evaluation/rag_evaluators.py`
- [x] 实现 `ContextPrecisionEvaluator(Evaluator)`——使用 Ragas `ContextPrecision` 指标，如果未安装 ragas 则引发 `ImportError`
- [x] 实现 `ContextRecallEvaluator(Evaluator)`——使用 Ragas `ContextRecall` 指标
- [x] 实现 `FaithfulnessEvaluator(Evaluator)`——使用 Ragas `Faithfulness` 指标
- [x] 实现 `AnswerRelevancyEvaluator(Evaluator)`——使用 Ragas `AnswerRelevancy` 指标
- [x] 每个评估器接受 `llm_config: LLMConfig | None` 并使用它配置 Ragas 的 LLM
- [x] 将所有 Ragas 调用包装在 try/except 中以将 Ragas 错误转换为 Hecate 评估错误

**文件**：`src/hecate/services/evaluation/rag_evaluators.py`、`pyproject.toml`
**规约**：rag-evaluation

### Task 5: Agent evaluators (LLM-as-Judge) — 任务 5：智能体评估器（LLM-as-Judge）
- [x] 创建 `src/hecate/services/evaluation/agent_evaluators.py`
- [x] 实现 `CorrectnessEvaluator(Evaluator)`——LLM-as-Judge 比较生成答案与预期答案，当无 expected_answer 时返回 Score(metric_name="correctness", value=-1.0)
- [x] 实现 `RelevancyEvaluator(Evaluator)`——LLM-as-Judge 评估响应与查询的相关性
- [x] 实现 `CompletenessEvaluator(Evaluator)`——LLM-as-Judge 评估查询方面的覆盖度
- [x] 每个评估器使用 `LLMConfig` 配置法官 LLM，回退到默认模型
- [x] 将 LLM-as-Judge 提示模板定义为 `services/evaluation/prompts.py` 中的模块级常量

**文件**：`src/hecate/services/evaluation/agent_evaluators.py`、`src/hecate/services/evaluation/prompts.py`
**规约**：agent-evaluation

### Task 6: Evaluation engine — 任务 6：评估引擎
- [x] 创建 `src/hecate/services/evaluation/engine.py` 包含 `EvaluationEngine` 类
- [x] 实现异步 `run(evaluators: list[Evaluator], dataset: EvaluationDatasetModel) -> EvaluationRunResult`
- [x] 在嵌套循环中对每个数据集项执行每个评估器
- [x] 捕获每个评估器-每个项的异常——记录错误，记录带 reasoning="Evaluator error: {message}" 的失败 Score
- [x] 计算所有项的每个指标平均值
- [x] 以毫秒为单位跟踪总执行时间
- [x] 在数据库中创建 `EvaluationRunModel` 和 `EvaluationScoreModel` 记录

**文件**：`src/hecate/services/evaluation/engine.py`
**规约**：evaluation-framework

### Task 7: REST API endpoints — 任务 7：REST API 端点
- [x] 创建 `src/hecate/api/evaluation.py` 包含 FastAPI 路由器（prefix="/api/evaluation", tags=["evaluation"]）
- [x] 实现 `POST /datasets`——创建数据集，返回 201
- [x] 实现 `GET /datasets`——列出数据集带分页
- [x] 实现 `GET /datasets/{dataset_id}`——获取单个数据集
- [x] 实现 `PUT /datasets/{dataset_id}`——更新数据集
- [x] 实现 `DELETE /datasets/{dataset_id}`——级联删除数据集，返回 204
- [x] 实现 `POST /datasets/{dataset_id}/items`——添加项，返回 201 带数量
- [x] 实现 `GET /datasets/{dataset_id}/items`——列出项带分页
- [x] 实现 `DELETE /datasets/{dataset_id}/items/{item_id}`——删除项，返回 204
- [x] 实现 `POST /runs`——创建并执行评估运行（dataset_id + evaluator 名称），返回 201 带 EvaluationRunResult
- [x] 实现 `GET /runs`——列出运行，可选按 dataset_id 过滤
- [x] 实现 `GET /runs/{run_id}`——获取运行带摘要统计
- [x] 实现 `GET /runs/{run_id}/scores`——获取运行的所有分数
- [x] 在 `src/hecate/main.py` 中注册路由器

**文件**：`src/hecate/api/evaluation.py`、`src/hecate/main.py`
**规约**：evaluation-api

### Task 8: Tests — 任务 8：测试
- [x] 创建 `tests/test_services/test_evaluation/test_types.py`——Score 验证（范围、source 枚举）、EvalInput/Output 构造
- [x] 创建 `tests/test_services/test_evaluation/test_evaluator_abc.py`——Evaluator 不可实例化，带 evaluate() 的子类可工作
- [x] 创建 `tests/test_services/test_evaluation/test_dataset_service.py`——CRUD、add_items 验证、导入/导出、分页（使用 db_session）
- [x] 创建 `tests/test_services/test_evaluation/test_engine.py`——批处理执行、错误隔离、分数聚合（使用模拟评估器）
- [x] 创建 `tests/test_api/test_evaluation_api.py`——API 端点测试（使用 client fixture）
- [x] 智能体评估器测试使用模拟 LLM 响应（无真实 API 调用）
- [x] 如果未安装 ragas，RAG 评估器测试跳过（使用 `pytest.importorskip("ragas")`）

**文件**：`tests/test_services/test_evaluation/`、`tests/test_api/test_evaluation_api.py`
**规约**：全部

### Task 9: Verification — 任务 9：验证
- [x] 运行 `ruff check src/hecate/services/evaluation/ src/hecate/models/evaluation.py src/hecate/api/evaluation.py tests/test_services/test_evaluation/ tests/test_api/test_evaluation_api.py`
- [x] 运行 `ruff format --check src/ tests/`
- [x] 运行 `mypy src/hecate/services/evaluation/ src/hecate/models/evaluation.py src/hecate/api/evaluation.py`
- [x] 运行 `python -m pytest tests/test_services/test_evaluation/ tests/test_api/test_evaluation_api.py -v`
- [x] 运行完整套件：`python -m pytest tests/ -q`
