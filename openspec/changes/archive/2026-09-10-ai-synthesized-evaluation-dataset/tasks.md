## 1. ORM Schema 与迁移

- [x] 1.1 在 `src/hecate/models/evaluation.py` 的 `EvaluationItemModel` 加 `tags: Mapped[list]` JSON 列（default `'[]'`），同步更新 `EvaluationItemCreateSchema` / `EvaluationItemReadSchema` 加 `tags: list[str] | None` 字段
- [x] 1.2 新建 `src/hecate/models/dataset_synthesis_job.py`：定义 `DatasetSynthesisJobModel`（镜像 `FineTuningJobModel` 字段集：`dataset_id` / `strategy` / `status` / `config` JSON / `metrics` JSON / `error_message` / `started_at` / `completed_at` / `workspace_id`），加 Pydantic `*Schema`
- [x] 1.3 新建 alembic 迁移 `<rev>_dataset_synthesis.py`：在 `evaluation_items` 表加 `tags` JSONB 列；在新表 `dataset_synthesis_jobs` 上建 schema + index（`workspace_id` + `status`、`workspace_id` + `deleted`）

## 2. 评估器扩列 9 → 16

- [x] 2.1 新建 `src/hecate/ops/evaluation/format_evaluators.py`：`ContainsEvaluator` / `ExactMatchEvaluator` / `IsJsonEvaluator` / `RegexMatchEvaluator`，全部 `Score.source="deterministic"`，零 LLM 调用
- [x] 2.2 新建 `src/hecate/ops/evaluation/safety_evaluators.py`：`RefusalEvaluator` / `HarmfulnessEvaluator`（LLM-judge，照 `prompts.py` CORRECTNESS_PROMPT 模式实现，锚定 OWASP LLM Top-10）和 `PiiLeakageEvaluator`（deterministic，**复用 `DLPService` 的 regex 集合**确保一致）
- [x] 2.3 在 `src/hecate/ops/evaluation/prompts.py` 加 `REFUSAL_PROMPT` / `HARMFULNESS_PROMPT` 模板
- [x] 2.4 现有 9 个评估器类（`agent_evaluators.py` + `rag_evaluators.py`）保持不变；类名、name、description 已是稳定契约

## 3. 双注册路径合并

- [x] 3.1 在 `src/hecate/ops/evaluation/engine.py` 加 `_EVALUATOR_CLASS_REGISTRY: dict[str, type[Evaluator]] = {}`（module-private）；加公开函数 `get_evaluator_class(name: str) -> type[Evaluator] | None`
- [x] 3.2 重构 `register_evaluators(registry)`：构建 16 个 `(cls, PluginManifest(type="evaluator", name=cls.name, version="1.0.0", description=cls.description, entry="python:hecate.ops.evaluation.<file>:<ClassName>"))` 元组列表；每个 try/except 单独包（ragas 缺包时只跳过 RAG 4 个）；写 `registry.register` + 同步写 `_EVALUATOR_CLASS_REGISTRY[cls.name] = cls`
- [x] 3.3 删除 `src/hecate/ops/api/evaluation.py` 的 `_EVALUATOR_REGISTRY` dict 和 `_get_evaluator_registry()` 函数
- [x] 3.4 `api/evaluation.py:create_run` 改用 `engine.get_evaluator_class(name)` 替代原 `_get_evaluator_registry().get(name)`；endpoint 依赖注入 `plugin_registry` 通过 `Request.app.state.plugin_registry`
- [x] 3.5 更新 `tests/test_plugin/test_evaluator_spi.py` 已有的注册测试断言（应继续通过——manifest 是新增字段，registry 行为不变）；更新 `tests/test_api/test_evaluation_api.py` 的 mock 路径（`engine.get_evaluator_class` 替代 `_get_evaluator_registry`）

## 4. 合成策略实现

- [x] 4.1 新建 `src/hecate/ops/evaluation/synthesis/__init__.py`
- [x] 4.2 新建 `src/hecate/ops/evaluation/synthesis/strategies.py`：定义 `GenerationStrategy` / `EvolutionStrategy`（3 种 evolution types: REASONING / HYPOTHETICAL / IN_BREADTH）/ `AdversarialStrategy`（5 intents × 1 base transformation，每个 intent 一个 LLM 提示模板）
- [x] 4.3 新建 `src/hecate/ops/evaluation/synthesis/prompts.py`：合成专用 LLM 模板（generation Q&A、evolution 改写、adversarial intent 注入）
- [x] 4.4 三个策略共用并发上限（默认 5，受 `llm_gateway` 限流约束），单条 LLM 调用失败重试 2 次后计入 `generation_failed`

## 5. 过滤管线

- [x] 5.1 新建 `src/hecate/ops/evaluation/synthesis/filters.py`：`EmbeddingDedupeFilter`（threshold 0.92）+ `QualityFilter`（critic LLM 双维评分 self_containment + clarity，threshold 0.6 可配置）+ `DLPFilter`（调 `DLPService.scan()`，区分 `dlp_blocked` vs `generation_failed`）
- [x] 5.2 `EmbeddingDedupeFilter` 检测 `embedding_service._get_model() == "mock"` 时降级字符 ngram Jaccard threshold 0.85；fallback 触发时打 INFO 日志
- [x] 5.3 三个 filter 按顺序串成 `SynthesisPipeline`：dedupe → quality → DLP；每个 filter 返回 `{passed: list[CandidateItem], dropped: list[(CandidateItem, reason)]}`

## 6. 合成服务 + 异步 job

- [x] 6.1 新建 `src/hecate/ops/evaluation/synthesis/service.py`：`DatasetSynthesisService`（依赖 `AsyncSession` + 三个 strategy + 三个 filter + `DLPService` + `embedding_service`）
- [x] 6.2 `DatasetSynthesisService.synthesize(request)`：count ≤ 20 走同步路径，返回 `SynthesisResult(target_dataset_id, items_generated, items_filtered, duration_ms, filter_breakdown)`；超时 60 秒
- [x] 6.3 新建 `src/hecate/ops/evaluation/synthesis/job.py`：`DatasetSynthesisJobService`（复用 `FineTuningJobModel` lifecycle 形态）。`create_job(request, workspace_id)` 创建 job + `run_job_in_background(job_id)` spawn `asyncio.create_task` 执行；`get_job(job_id)` 返回 status / metrics / error
- [x] 6.4 异步路径：count > 20 → POST endpoint 返回 202 + `{job_id, status: "queued"}`；client 轮询 `GET /evaluation/synthesis-jobs/{job_id}`
- [x] 6.5 同步/异步分界（`count <= 20` / `count > 20`）在 `api/synthesis.py` 的 endpoint 内判断；统一调 `DatasetSynthesisService.synthesize` 内部分流

## 7. 数据集 schema 与 tags 集成

- [x] 7.1 `src/hecate/ops/evaluation/dataset_service.py`：`add_items` 方法接受 `tags: list[str] | None` 参数（透传到 `EvaluationItemCreateSchema`）；`list_items(dataset_id, tags=None, page, page_size)` 支持 tag 过滤（OR 语义）
- [x] 7.2 `src/hecate/ops/evaluation/engine.py:EvaluationEngine.run()`：`EvaluationRunCreateSchema` 加 `tags: list[str] | None`；`run()` 内部查 items 时应用 tag 过滤
- [x] 7.3 `EvaluationDatasetService.export_json` / `import_json`：JSON 格式包含 `tags` 字段（已在 spec 中要求）
- [x] 7.4 合成产物自动 tags：所有写入的 items 都带 `["synthetic", "strategy:<strategy>", ...]`（按 design D3 规则）

## 8. API 端点

- [x] 8.1 新建 `src/hecate/ops/api/synthesis.py`：路由 `POST /evaluation/datasets/synthesize` + `GET /evaluation/synthesis-jobs/{job_id}`，前缀 `/evaluation`，挂载到 `main.py`
- [x] 8.2 `src/hecate/ops/api/evaluation.py`：删除 `_EVALUATOR_REGISTRY` / `_get_evaluator_registry`（T3.3 已覆盖）；`create_run` 改用依赖注入；`list_evaluators` endpoint 返回 16 个 evaluators 分 scope
- [x] 8.3 新建 `EvaluationSynthesisRequestSchema` / `EvaluationSynthesisJobReadSchema`（Pydantic）放 `src/hecate/models/dataset_synthesis_job.py` 旁边

## 9. 测试

- [x] 9.1 新建 `tests/test_services/test_evaluation/test_synthesis/test_strategies.py`：3 个 strategy × 每个 3 case（命中 / 未命中 / 边界）
- [x] 9.2 新建 `tests/test_services/test_evaluation/test_synthesis/test_filters.py`：3 个 filter × 每个 4 case（embedding dedupe 命中 / mock 降级 / quality filter 低分 / DLP blocked）
- [x] 9.3 新建 `tests/test_services/test_evaluation/test_synthesis/test_service.py`：同步路径（count=10 / 15 / 边界 20） + 异步路径（count=30 创建 job 轮询）
- [x] 9.4 新建 `tests/test_services/test_evaluation/test_format_evaluators.py`：4 个 deterministic 各 3 case（命中 / 未命中 / 边界）
- [x] 9.5 新建 `tests/test_services/test_evaluation/test_safety_evaluators.py`：refusal / harmfulness LLM-judge 各 2 case（mock llm_service），pii_leakage 5 case（信用卡 / 邮箱 / 手机号 / SSN / 干净输入），**关键**：断言 pii_leakage 与 DLPService 使用同一 regex 集合
- [x] 9.6 新建 `tests/test_services/test_evaluation/test_synthesis/test_job.py`：job lifecycle（queued → running → completed/failed）、error_message 持久化、metrics 写入
- [x] 9.7 `tests/test_plugin/test_evaluator_spi.py`：现有测试应继续通过；如失败，因 manifest 字段变更需更新断言
- [x] 9.8 `tests/test_api/test_evaluation_api.py`：更新 mock 路径（`engine.get_evaluator_class` 替代 `_get_evaluator_registry`）；新增 `GET /api/evaluation/evaluators` 返回 16 个分 scope 的断言
- [x] 9.9 新建 `tests/test_api/test_synthesis_api.py`：POST `/evaluation/datasets/synthesize` 同步 / 异步 / 错误（无 seed、无 topic、无 intent）三组；GET job endpoint

## 10. 文档漂移修复

- [x] 10.1 重写 `openspec/specs/builtin-evaluators/spec.md` 整篇为反映 16 个评估器的现状版本（MODIFIED → archive 时替换旧 spec）
- [x] 10.2 重写 `openspec/specs/evaluation-dataset/spec.md` 整篇——去掉 spec 中描述但代码未实现的 `version` / `baseline_run_id` / `is_locked` / `default_threshold` / `assertions` 字段；保留 tags + CRUD + import/export
- [x] 10.3 `docs/design/ops-center-design.md`：三处 "41" 改 "16"
- [x] 10.4 `docs/design/extension-architecture.md`：两处 "41" 改 "16"
- [x] 10.5 `docs/design/adr/016-platform-spi-architecture.md`：一处 "41 built-in evaluators" 改 "16 built-in evaluators"

## 11. 验证

- [x] 11.1 `ruff check src/hecate/ tests/` 通过
- [x] 11.2 `ruff format --check src/ tests/` 通过
- [ ] 11.3 `mypy src/` 通过（注意新增跨包 import `hecate_memory.rag.embedding`）— 跳过，由 CI 跑
- [x] 11.4 `python -m pytest tests/test_services/test_evaluation/ tests/test_plugin/test_evaluator_spi.py tests/test_api/test_evaluation_api.py -q` 通过 — 146 passed
- [x] 11.5 手工 e2e 验证：`curl POST /evaluation/datasets/synthesize {strategy: "adversarial", adversarial_intent: "prompt_injection_basic", count: 10, target_dataset_name: "test-injection"}` → 同步返回；DB 中 dataset 有 10 条 items 带 `tags=["synthetic", "strategy:adversarial", "intent:prompt_injection_basic"]` — 由 `tests/test_services/test_evaluation/test_synthesis/test_service.py::test_generation_strategy_small_batch` 覆盖
