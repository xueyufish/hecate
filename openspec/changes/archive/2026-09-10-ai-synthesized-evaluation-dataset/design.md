## Context

Hecate 的评估栈（7.1 / 7.2a）已经提供了 `EvaluationDatasetService`、`EvaluationEngine`、9 个内置评估器和 4 张 ORM 表（datasets / items / runs / scores）。但合成评估数据的能力完全缺失——这意味着每个 agent 上线前都要手工编写百条评估样本，是 7.2b 在 Sprint 8 Opening Queue 中被列为首发项的原因（roadmap §Evaluation Suite 注释："AgentArts 评估页是产品完成度最高的面，Hecate 缺任务化/标注/报告三件"）。

`DatasetSynthesisJobModel` 的形态直接复用 `FineTuningJobModel`（已存在的 9 行模型 + 12 行 schema）：`status` 字符串字段 (`queued` / `running` / `completed` / `failed`)、`config` JSON、`metrics` JSON、`error_message`、`started_at` / `completed_at`。Embedding 服务来自 `packages/hecate-memory/src/hecate_memory/rag/embedding.py::EmbeddingService`，已是 pyproject workspace member；当前仅包内使用，跨包 import 是新依赖方向但无新 wheel 依赖。`DLPService` 来自 `src/hecate/ops/dlp/service.py`，可直接调用。LLM 调用统一走 `hecate_llm.service.llm_service`（评估器已在用）。

约束：合成管线运行时间通常分钟级（30 条 × 2-3 次 LLM 调用），不能在请求内同步执行；embedding 在没有 FlagEmbedding wheel 时降级 mock 模式，cosine dedupe 必须 fallback 到 ngram 模糊去重。

## Goals / Non-Goals

**Goals:**

- 提供 LLM 驱动的合成管线，从种子 dataset / 主题描述 / 对抗意图生成评估 items
- 同步 / 异步双轨：≤20 条同步返回，>20 条异步 job 模式（复用 `FineTuningJobModel` 形态）
- 持久化到新建 dataset 并打 `tags` JSON 列做溯源（合成策略 / 对抗意图 / 种子来源）
- 内置评估器扩列到 16 个，覆盖 AWS Bedrock AgentCore 同级核心面
- 合并 `PluginRegistry` 与 `api/evaluation.py` 之间的双注册路径

**Non-Goals:**

- 不实现多轮对话合成（Bedrock AgentCore user simulation 形态）——多轮属于 7.3 Workflow Evaluation 范畴
- 不实现 KG 驱动的合成（Ragas 形态）——重型管线，需要 DocumentModel/KG 前置，超 M 工作量
- 不实现 RAG-only evolution 类型（`MULTICONTEXT` / `CONCRETIZING` / `CONSTRAINED` / `COMPARATIVE`）——v1 不锚定 items 到 context
- 不实现完整 Promptfoo 30+ strategies 覆盖——只覆盖 5 个高频 intent × 1 base transformation
- 不修复 evaluation-dataset spec 历史漂移的 `version` / `baseline_run_id` / `is_locked` / `default_threshold` / `assertions` 字段——留独立 change
- 不引入新的 workspace member 依赖（hecate-memory 已是 member）

## Decisions

### D1: 同步 / 异步分界 = 20 条

**决策**：`count <= 20` 同步执行并在响应体内返回结果；`count > 20` 异步 job 模式返回 202 + `job_id`。

**理由**：30 条合成 ≈ 60-90 次 LLM 调用，按 5 并发 ≈ 12-18 秒。同步路径覆盖最常见的少量测试场景，异步路径覆盖大规模回归测试。分界值 20 与 Ragas TestsetGenerator 默认 batch 大小对齐。

**替代方案**：
- 全部异步：API 简单但客户端要实现轮询，体验差
- 全部同步：≤20 没问题，>20 必然超时
- 分界 50：与 Ragas 默认值差太远

### D2: 异步 job 复用 `FineTuningJobModel` 形态

**决策**：新建 `DatasetSynthesisJobModel`，字段直接镜像 `FineTuningJobModel`（`status` / `config` JSON / `metrics` JSON / `error_message` / `started_at` / `completed_at` / `workspace_id`）。Lifecycle 走四态：`queued` → `running` → `completed` / `failed`。

**理由**：`FineTuningJobModel` 已是平台异步任务的事实模板，复用形态让前端轮询组件、告警规则、审计查询都只需一个模式。

**替代方案**：
- 用 Temporal：项目已有 Temporal 但 worker 模板在 `runtime/temporal/`，是 runtime 引擎专用，不适合通用作业；引入会跨概念边界
- 用 Celery：未在项目中使用，引入新依赖
- 用 FastAPI BackgroundTasks：进程级，崩溃即丢任务——合成任务运行分钟级，不可接受

### D3: 合成产物写入新建 dataset，不追加到现有 dataset

**决策**：`POST /evaluation/datasets/synthesize` body 包含 `target_dataset_name`；服务端自动创建新 `EvaluationDatasetModel` 写入。

**理由**：合成产物需要独立审阅、过滤、迭代——混入现有 dataset 会污染 ground truth。`synthetic-{strategy}-{topic}-{timestamp}` 自动命名便于追溯，但用户传 `target_dataset_name` 时优先用用户值。

**替代方案**：
- 追加到 `seed_dataset_id`：原 dataset 污染，不可接受
- 写到 staging dataset + promote：和 7.4 human annotation 衔接更顺，但 v1 加 promote 流是 scope creep
- 用户先建空 dataset 再合成：多一次 API call，UX 差

### D4: Embedding dedupe 通过 `hecate_memory.rag.embedding.embedding_service` 跨包调用

**决策**：`EmbeddingDedupeFilter` 直接 `from hecate_memory.rag.embedding import embedding_service`，调用 `await embedding_service.encode(texts)`。当 `_get_model()` 返回 `"mock"` 时降级到字符 ngram Jaccard（threshold 0.85）。

**理由**：embedding 服务已是 pyproject workspace member，无新依赖；懒加载 mock 模式让无 FlagEmbedding wheel 的开发环境也能跑流程（只是 dedupe 精度降级）。

**替代方案**：
- 在 `core/composition` 加 `embedding_provider.py` 类似 `memory_provider.py`：工程标准但过度——embedding 只在 synthesis 一处被用，引入 SPI 是 over-engineering
- 新写轻量 `embedding_service.py` 在 ops 包内：配置爆炸 + 第三方依赖
- 完全跳过 dedupe：只靠 quality filter，接受重复样本——对回归测试不利

### D5: DLP 过滤在写入前执行

**决策**：`DLPFilter` 在 `add_items()` 之前调用，区分 `dlp_blocked` 和 `generation_failed` 两类失败原因。

**理由**：写入后再发现 DLP 违规会留下半污染 dataset。错误分类便于运营排查——DLP blocked 是 workspace 策略问题，generation failed 是 LLM 问题，归因不同。

**替代方案**：
- 写入后扫整个 dataset：漏检窗口 + 重复扫描开销
- 不做 DLP 过滤：违反 `runtime/security/hooks` 的 OWASP 对齐原则

### D6: 内置评估器按 scope 四层 + Builtin.* 命名

**决策**：四层 = `result` (5) / `process` (2) / `rag` (4) / `safety` (5)。命名沿用现有 lowercase 短名（`correctness` / `refusal` / `pii_leakage` 等），**不**加 `Builtin.` 前缀——保持现状命名风格，AWS `Builtin.*` 风格留作未来重构的考量。

**理由**：现状命名（`correctness` / `relevancy`）已经稳定，加 `Builtin.` 前缀会破坏 API 兼容（`POST /evaluation/runs` body 中的 `evaluators` 字段）。registry 内部按 `scope` 字段分组即可，外部命名不动。

**替代方案**：
- 借 AWS 风格 `Builtin.Helpfulness`：破坏 API 兼容、改动面大
- 拆 `evaluators/` 子包：现状 9 个评估器塞在 `agent_evaluators.py` + `rag_evaluators.py` 还没到拆包规模；扩到 16 后可以拆但不是本变更硬要求
- 扩到 41 个（archived 7.2a 设计）：已确认 ROI 负

### D7: 双注册路径合并为 PluginRegistry 权威 + class index sidecar

**决策**：删除 `api/evaluation.py` 的 `_EVALUATOR_REGISTRY` 私有 dict；`engine.py` 内新增 module-private `_EVALUATOR_CLASS_REGISTRY: dict[str, type[Evaluator]]` 与 `get_evaluator_class(name: str)` 公开函数。`register_evaluators` 同时写 `PluginRegistry`（instance + manifest）和 `_EVALUATOR_CLASS_REGISTRY`（class）。

**理由**：`PluginRegistry` 已是项目唯一的扩展点机制（channels / auth_provider / notifier / tool 都走它）；评估器作为 SPI 类型却绕过它单独维护 dict 是历史遗留。

**关键设计选择**：为什么不做完全统一（让 `PluginRegistry` 同时存 instance 和 class）？——`PluginRegistry` API（`register(manifest, instance)` / `get_by_type(type)`）已经被 channels / auth 等多处消费，修改它影响面远超本变更。sidecar 模式最小爆炸面。

**替代方案**：
- 改造 `PluginRegistry` 支持存 class：破坏现有契约，`wiring.py` 多处需要改
- 完全删除 instance 注册（API 拿不到 instance 就只能 class 自己 new）：现有 `test_evaluator_spi.py` 测试需要重写

### D8: Adversarial strategy 用 intent × 1 base transformation 笛卡尔积

**决策**：v1 只做 5 个 intents × 每个 1 个 base transformation（共 5 个 strategy 实现），不做 Promptfoo 那种 N × M 笛卡尔积。

**理由**：每加一个 base transformation 是一个独立的 LLM 提示模板，5 × 1 已经覆盖 OWASP LLM01:2025 五个高频场景。N × M 在没有自动化评估生成质量的情况下是 ROI 负。

**替代方案**：
- 全量 Promptfoo 风格：30+ strategies，scope 爆炸
- 仅 prompt injection 一种：覆盖太窄

### D9: Tags 列加在 EvaluationItemModel

**决策**：`EvaluationItemModel` 加 `tags: Mapped[list]` JSON 列（与现有 `metadata_` 同类型），`EvaluationItemCreateSchema` / `EvaluationItemReadSchema` 加 `tags: list[str] | None`。Alchemy 迁移新增一列。

**理由**：`metadata_` JSON 列虽然能存 tags 但语义错位——metadata 是 item 级元数据（来源、生成时间等），tags 是可索引/可过滤的分类标签。独立列便于：
1. `WHERE tags @> '["intent:..."]'` JSONB 查询
2. 未来可能建 tags 索引

**替代方案**：
- 全用 `metadata_`：零 schema 变更，但 tags 查询退化为 `metadata_->>'tags'` 字符串匹配
- 新建独立 `ItemTagModel` 表（多对多）：重，过度规范化

## Risks / Trade-offs

[R1] **Embedding 跨包依赖方向**：当前 `src/hecate/ops/*` 从未 import `hecate_memory.*`，引入跨包耦合 → 在 synthesis 子包内 lazy import，运行时检测失败时降级 ngram，不影响其他代码路径

[R2] **同步路径 60 秒硬上限**：≤20 条 × 3 次 LLM 调用 ≈ 60 秒在 LLM 网关速率限制下可能超时 → 加 client-side timeout 提示；实测 5 并发下 15-20 条通常 30-40 秒完成，超时触发降级到 async 路径（API 自动重提交）

[R3] **`tags` JSON 列的索引性能**：JSONB 索引在小数据集无问题；10k+ items 时 `tags @> '["x"]'` 全表扫描 → 暂不建索引，规模触发时改用 GIN（独立 migration）

[R4] **Quality filter 的 critic LLM 评分稳定性**：critic 评分受模型温度影响，可能同一输入两次跑分差 0.1+ → 默认 temperature=0.0（沿用 `LLMConfig` 默认值），scoring threshold 0.6 留 0.4 buffer

[R5] **DLP regex 与 `pii_leakage` evaluator 一致性保证**：测试必须 import `DLPService` patterns，验证两个使用同一 regex 集合 → 单测集中覆盖，跨过修后单测必败

[R6] **PluginRegistry 合并破坏第三方插件兼容性**：现有 `register_evaluators(registry)` 签名不变，但 manifest 字段是新增的——第三方插件如果只传 `type` + `name` 不传 `version`，会被新代码 → 接受（manifest 的 `version` 字段是 optional 有 default）

[R7] **OWASP LLM Top-10 锚定的未来漂移**：OWASP 每年更新榜单，固定锚定 2025 年版 → spec 不写硬版本号，写"当前 OWASP LLM Top-10"由 documentation 维护映射；代码层 `injection-detection` 已锚定 2025 不动

[R8] **Alchemy 迁移在已有 `evaluation_items` 表加列**：长事务风险 → 列加在表末尾，加 `nullable=True` default `'[]'::jsonb`，零停机

## Migration Plan

**Alchemy 迁移**（单文件 `alembic/versions/<rev>_dataset_synthesis.py`）：

1. `ALTER TABLE evaluation_items ADD COLUMN tags JSON DEFAULT '[]'::jsonb`（nullable，最终 NOT NULL via 数据回填）
2. `CREATE TABLE dataset_synthesis_jobs (...)` 镜像 `fine_tuning_jobs` 结构

**回滚策略**：down_revision 还原两 schema change；前端未上线前直接 revert 即可。已写入 dataset 的 `tags` 数据不删除（保留 metadata）。

**API 部署顺序**：
1. 部署 alambic migration（加列 + 建表）
2. 部署新 backend（旧 API 仍可用——`create_run` 行为兼容）
3. 发布 synthesis API + 更新 `GET /api/evaluation/evaluators` 返回 16 个
4. 前端接入 synthesis UI（独立前端变更）

## Open Questions

无。所有 7 个 A 类、5 个 B 类、5 个 D 类决策已在 explore 阶段拍板。剩余 P1-P5 实施期决定项不需在 design 中列举——tasks.md 会标注。
