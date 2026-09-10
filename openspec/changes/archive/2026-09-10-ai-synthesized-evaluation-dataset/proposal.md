## Why

Hecate 当前评估套件（7.1 / 7.2a）只覆盖 *如何跑评估* 和 *跑哪些评估器*——`EvaluationDatasetService` + `EvaluationEngine` + 9 个内置评估器。没有 *如何造评估数据* 的能力，而生产用户场景下人工写评测集成本极高（每个 agent 起步百条），是 AgentArts 等竞品公认的产品完成度短板（Sprint 8 Opening Queue 也因此把"任务化、标注、报告"列为最低补齐项）。本变更（7.2b）补 AI 合成评估数据集能力，让用户从种子 items / 主题描述 / 对抗意图一键生成带 `expected_answer` 的合成 dataset，并落到 `EvaluationDatasetModel`。

与此同时，需要校正 7.2a 在 roadmap 上标注"✅"但代码仅实现 9/41 个评估器的历史欠账，把内置评估器扩到 16 个（AWS Bedrock AgentCore 同级核心面），并合并当前 `PluginRegistry` 与 `api/evaluation.py` 中并行的双注册路径。

## What Changes

- **新增 `dataset-synthesis` API 与服务**：从种子 dataset / 主题描述 / 对抗意图生成评估 items（query / expected_answer / context），写入新建 dataset 并打 `tags` 溯源。同步 ≤20 条 / 异步 job >20 条（复用 `FineTuningJobModel` 形态）。
- **新增合成策略**：三种——`generation`（直接生成 Q&A）、`evolution`（DeepEval 七种中的非 RAG 部分）、`adversarial`（OWASP LLM01:2025 + Promptfoo Top-5 风格的 plugin×strategy 解耦，v1 覆盖 5 个高频 strategy）。
- **新增三层过滤管线**：`EmbeddingDedupeFilter`（接 `hecate-memory` 的 `EmbeddingService`，mock 时降级 ngram fuzzy）→ `QualityFilter`（critic LLM 自评 0-1，threshold 0.6）→ `DLPFilter`（写入前过 `DLPService`，区分 blocked vs failed）。
- **数据集 schema 新增 `tags JSON` 列**（`EvaluationItemModel`）：合成产物溯源标记 `["synthetic", "strategy:xxx", "intent:yyy"]`，也用于评估时按 tags 切片。
- **内置评估器 9 → 16**：新增 `refusal` / `harmfulness` / `pii_leakage` 三个安全类，`contains` / `regex_match` / `is_json` / `exact_match` 四个确定性格式类。按作用域四层（Result / Process / Tool / Safety）+ `Builtin.*` 命名规范组织。
- **合并双注册路径**：删除 `api/evaluation.py` 的并行 `_EVALUATOR_REGISTRY` dict，`PluginRegistry` 成为权威 + 单一 class index sidecar（`engine.get_evaluator_class(name)`）。
- **同步校正 6 处文档/spec 漂移**：`openspec/specs/builtin-evaluators/spec.md` + `openspec/specs/evaluation-dataset/spec.md` 整篇重写对齐到 16 个评估器现状；`docs/design/ops-center-design.md`（3 处）+ `docs/design/extension-architecture.md`（2 处）+ `docs/design/adr/016-platform-spi-architecture.md`（1 处）把 "41" 改成 "16"。
- **新增 alembic 迁移**：`EvaluationItemModel` 加 `tags JSON` 列 + 新增 `DatasetSynthesisJobModel` 表。

无 **BREAKING** 变更。API 端点签名变化：`create_run` 由隐式全局 registry 改成显式依赖注入，但 endpoint URL 与 request body 不变（向后兼容）。`EvaluationDatasetService` 不变。

## Capabilities

### New Capabilities

- `dataset-synthesis`：合成评估数据集的能力——种子源解析、策略生成、过滤管线、异步 lifecycle、写入新 dataset 并打 tags 溯源。对应 `specs/dataset-synthesis/spec.md`。

### Modified Capabilities

- `builtin-evaluators`：扩列 9 → 16（新增 7 个）；按作用域四层 + `Builtin.*` 命名重新组织；统一注册路径。对应 `specs/builtin-evaluators/spec.md` 的 delta（MODIFIED Requirements）。
- `evaluation-dataset`：数据集 schema 新增 `tags JSON` 列（`EvaluationItemModel`），导入/导出 JSON 包含 tags；评估时按 tags 切片。对应 `specs/evaluation-dataset/spec.md` 的 delta（MODIFIED Requirements）。

## Impact

**新代码 / 文件**：
- `src/hecate/ops/evaluation/synthesis/` 子包（`__init__.py` / `service.py` / `strategies.py` / `filters.py` / `job.py` / `prompts.py`）
- `src/hecate/ops/evaluation/safety_evaluators.py`（`RefusalEvaluator` / `HarmfulnessEvaluator` / `PiiLeakageEvaluator`）
- `src/hecate/ops/evaluation/format_evaluators.py`（`ContainsEvaluator` / `RegexMatchEvaluator` / `IsJsonEvaluator` / `ExactMatchEvaluator`）
- `src/hecate/ops/api/synthesis.py`（合成 API 路由，前缀 `/evaluation`）
- `src/hecate/models/dataset_synthesis_job.py`（`DatasetSynthesisJobModel` ORM + Pydantic schemas）
- `alembic/versions/<rev>_dataset_synthesis.py`（`tags` 列 + 新表迁移）

**修改文件**：
- `src/hecate/ops/evaluation/engine.py`（`register_evaluators` 走 `PluginManifest` 列表 + 暴露 `get_evaluator_class`）
- `src/hecate/ops/evaluation/agent_evaluators.py` / `rag_evaluators.py`（为每个类加 `version` / 描述元数据，类不变）
- `src/hecate/ops/api/evaluation.py`（删除 `_EVALUATOR_REGISTRY` 私有 dict，`create_run` 依赖注入 `plugin_registry` + `get_evaluator_class`）
- `src/hecate/core/composition/wiring.py`（`register_evaluators` 调用点不变，新增合成管线启动 hook）
- `src/hecate/models/evaluation.py`（`EvaluationItemModel` 加 `tags` 列 + `EvaluationItemCreateSchema` 加字段）

**依赖**：跨包引用 `hecate_memory.rag.embedding.embedding_service`（已在 pyproject workspace）。FlagEmbedding 缺失时自动 mock，synthesis 的 dedupe 降级 ngram——这是 graceful degradation，不破坏流程。

**API**：
- 新增：`POST /evaluation/datasets/synthesize`（请求同步时直接返回 items；异步时返回 job_id）；`GET /evaluation/synthesis-jobs/{job_id}`（查询异步任务状态）
- 改动：`POST /evaluation/runs`（行为不变，签名不变；内部 registry 读取路径重写）
- 文档：`GET /api/evaluation/evaluators` 列表从 9 → 16

**测试影响**：
- `tests/test_plugin/test_evaluator_spi.py`：现有测试应继续通过（`PluginRegistry` API 未变）
- `tests/test_api/test_evaluation_api.py`：registry 读取路径变了，需更新单测 mock
- 新增：`tests/test_services/test_evaluation/test_synthesis/` 完整子包覆盖合成管线、过滤、job lifecycle
- 新增：`tests/test_services/test_evaluation/test_safety_evaluators.py` + `test_format_evaluators.py`

**文档/spec**：
- 重写 `openspec/specs/builtin-evaluators/spec.md`
- 重写 `openspec/specs/evaluation-dataset/spec.md`
- 更新 6 处活 docs 漂移
