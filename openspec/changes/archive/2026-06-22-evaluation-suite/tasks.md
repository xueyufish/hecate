## 1. 注册表重构与基础设施

- [x] 1.1 创建 `services/evaluation/registry.py`，包含 `EvaluatorRegistry` 类、`@register_evaluator(name)` 装饰器、`get_evaluator(name)`、`list_evaluators(category=None)` 和 `list_evaluator_names()` 函数
- [x] 1.2 创建 `services/evaluation/prompt_templates.py`，包含 `JudgePromptTemplate` 数据类（scoring_scale、system_prompt、user_prompt_template、output_format、scoring_rubric）和一个预构建模板库
- [x] 1.3 创建 `services/evaluation/evaluators/` 包目录，包含导入所有评估器模块以触发自动注册的 `__init__.py`
- [x] 1.4 将现有的 4 个 RAG 评估器从 `rag_evaluators.py` 迁移到 `evaluators/rag.py`，使用 `@register_evaluator` 装饰器
- [x] 1.5 将现有的 5 个 Agent 评估器从 `agent_evaluators.py` 迁移到 `evaluators/agent.py`，使用 `@register_evaluator` 装饰器
- [x] 1.6 在原始 `rag_evaluators.py` 和 `agent_evaluators.py` 中添加重新导出垫片以保持向后兼容性
- [x] 1.7 更新 `api/evaluation.py` 以使用新的注册表函数，而不是内联 `_EVALUATOR_REGISTRY` 字典
- [x] 1.8 向 Evaluator ABC 添加 `category` 类属性，默认值为 `"generic"`

## 2. EvalInput 扩展

- [x] 2.1 向 EvalInput 数据类添加可选字段：`conversation_history: list[dict]`、`system_prompt: str | None`、`agent_id: uuid.UUID | None`、`session_id: uuid.UUID | None`（全部默认 None/空）
- [x] 2.2 更新 `EvaluationEngine.run()` 以在可用时从 EvaluationItemModel 元数据填充新的 EvalInput 字段

## 3. 确定性格式评估器（结果层）

- [x] 3.1 创建 `evaluators/format.py`，包含 `ExactMatchEvaluator`——将 generated_answer 与 expected_answer 进行精确匹配比较
- [x] 3.2 实现 `ContainsEvaluator`——检查 generated_answer 是否包含指定的子字符串
- [x] 3.3 实现 `ContainsAnyEvaluator`——检查 generated_answer 是否包含任何指定的子字符串
- [x] 3.4 实现 `RegexMatchEvaluator`——检查 generated_answer 是否匹配正则表达式模式
- [x] 3.5 实现 `IsJSONEvaluator`——验证 generated_answer 是否为有效的 JSON
- [x] 3.6 实现 `FormatCheckEvaluator`——根据模式验证输出格式（键存在、类型检查）
- [x] 3.7 实现 `BLEUScoreEvaluator`——标准 BLEU 分数计算（确定性，无 LLM）
- [x] 3.8 实现 `ROUGEScoreEvaluator`——标准 ROUGE-L 分数计算
- [x] 3.9 实现 `F1ScoreEvaluator`——生成的答案和预期答案之间的 token 级 F1 分数

## 4. 内容质量评估器（结果层，LLM-Judge）

- [x] 4.1 创建 `evaluators/content.py`，包含使用 5 分制评分量规 JudgePromptTemplate 的 `ToxicityDetectionEvaluator`
- [x] 4.2 实现 `SafetyHarmlessnessEvaluator`——评估输出是否安全无害
- [x] 4.3 实现 `InstructionFollowingEvaluator`——检查 system_prompt 中的指令是否被遵循
- [x] 4.4 实现 `CoherenceEvaluator`——评估响应的内部逻辑连贯性
- [x] 4.5 实现 `FluencyEvaluator`——评估语言流畅性和可读性

## 5. 引用与基础评估器（结果层，LLM-Judge）

- [x] 5.1 创建 `evaluators/citation.py`，包含 `CitationRelevanceEvaluator`——检查答案中的引用是否与查询相关
- [x] 5.2 实现 `SourceAttributionEvaluator`——验证生成的答案中正确的来源归属
- [x] 5.3 实现 `GroundednessCheckEvaluator`——检查所有声明是否基于检索到的上下文
- [x] 5.4 实现 `HallucinationDetectionEvaluator`——检测不受上下文支持的虚构声明

## 6. 工具与过程评估器（过程层）

- [x] 6.1 创建 `evaluators/tool.py`，包含 `ToolSelectionAccuracyEvaluator`——评估工具选择正确性（LLM-judge）
- [x] 6.2 实现 `ToolTrajectoryScoringEvaluator`——对工具调用序列进行评分（LLM-judge）
- [x] 6.3 实现 `ToolParameterAccuracyEvaluator`——评估工具调用参数的正确性
- [x] 6.4 实现 `ToolOrderCorrectnessEvaluator`——检查工具调用顺序是否合乎逻辑
- [x] 6.5 实现 `ReasoningQualityEvaluator`——评估整体推理质量
- [x] 6.6 实现 `StepValidityEvaluator`——验证各个推理步骤

## 7. 多轮交互评估器（交互层）

- [x] 7.1 创建 `evaluators/multi_turn.py`，包含 `MultiTurnSuccessEvaluator`——评估跨轮次任务完成情况
- [x] 7.2 实现 `MultiTurnCoherenceEvaluator`——检查跨对话轮次的一致性
- [x] 7.3 实现 `ConversationQualityEvaluator`——整体对话质量评估
- [x] 7.4 实现 `ContextRetentionEvaluator`——评估早期上下文是否在后续轮次中保留

## 8. 通用 LLM-Judge 评估器（通用层）

- [x] 8.1 创建 `evaluators/judge.py`，包含 `SemanticSimilarityEvaluator`——测量答案和预期之间的语义等价性
- [x] 8.2 实现 `RubricScoringEvaluator`——带有可配置评分量规的通用基于量规的评分
- [x] 8.3 实现 `FactualityCheckEvaluator`——检查声明的事实准确性
- [x] 8.4 实现 `LLMRubricEvaluator`——接受自定义量规字符串进行领域特定评估

## 9. 安全与安保评估器（通用层）

- [x] 9.1 创建 `evaluators/safety.py`，包含 `PromptInjectionResistanceEvaluator`——测试输出是否抵抗提示注入
- [x] 9.2 实现 `PIILeakageDetectionEvaluator`——检测生成输出中的 PII 泄露
- [x] 9.3 实现 `JailbreakResistanceEvaluator`——测试对越狱尝试的抵抗能力

## 10. 编程评估器（通用层）

- [x] 10.1 创建 `evaluators/programmatic.py`，包含 `PythonCodeEvaluator`——安全地针对 EvalInput 执行用户提供的 Python 函数
- [x] 10.2 实现 `CustomCallableEvaluator`——将任意异步可调用对象包装为评估器

## 11. 引擎增强

- [x] 11.1 更新 `EvaluationEngine.run()` 以将确定性评估器与 LLM-judge 评估器分离，并通过 `asyncio.gather` 并行运行确定性评估器
- [x] 11.2 向 `EvaluationEngine.run()` 添加 `tags` 参数，用于按标签过滤的项选择
- [x] 11.3 在追踪记录中将评估 LLM 调用标记为 `metadata.purpose="evaluation"` 以实现成本隔离

## 12. 模型变更与迁移

- [x] 12.1 向 `EvaluationDatasetModel` 添加 `version: str`、`baseline_run_id: UUID | None`、`is_locked: bool`、`default_threshold: float | None` 字段
- [x] 12.2 向 `EvaluationItemModel` 添加 `assertions: list | None`、`tags: list | None` JSON 字段
- [x] 12.3 更新数据集和项的 Pydantic 模式（Create/Update/Read）以包含新字段
- [x] 12.4 创建 Alembic 迁移，向 `evaluation_datasets` 和 `evaluation_items` 表添加新列，从当前头链入
- [x] 12.5 将 `EVALUATION_REGRESSION` 添加到 `models/alert.py` 中的 AlertType StrEnum

## 13. 数据集服务更新

- [x] 13.1 更新 `EvaluationDatasetService.create_dataset()` 以接受 `version`、`default_threshold` 参数
- [x] 13.2 添加 `lock_dataset(dataset_id)` 和 `unlock_dataset(dataset_id)` 方法
- [x] 13.3 添加 `set_baseline_run(dataset_id, run_id)` 方法
- [x] 13.4 更新 `add_items()` 以接受并持久化每项的 `assertions` 和 `tags` 字段
- [x] 13.5 强制执行 `is_locked` 检查：拒绝锁定数据集上的项添加/更新/删除，返回 409 Conflict
- [x] 13.6 更新 `import_json()` 和 `export_json()` 以包含 assertions 和 tags

## 14. 回归服务

- [x] 14.1 创建 `services/regression_service.py`，包含 `RegressionService` 类
- [x] 14.2 实现 `compare_runs(baseline_run_id, candidate_run_id, threshold=0.05)`——计算每指标增量，识别回归
- [x] 14.3 实现 `compute_item_pass_fail(item, scores, dataset_default_threshold)`——评估单个项的断言/阈值
- [x] 14.4 实现 `run_regression(dataset_id, evaluators, tags, threshold, baseline_run_id)`——编排评估运行 + 比较 + 通过/失败报告
- [x] 14.5 实现 `_trigger_regression_alert(run_id, regressions)`——检测到回归时创建 AlertEventModel（与 8.6 告警系统集成）

## 15. API 新端点

- [x] 15.1 添加 `GET /api/evaluation/evaluators` 端点——返回所有已注册评估器，包含 category、description、source_type；支持 `?category=` 过滤器
- [x] 15.2 添加 `POST /api/evaluation/runs/compare` 端点——接受 baseline_run_id + candidate_run_id，返回比较报告
- [x] 15.3 添加 `POST /api/evaluation/regression/run` 端点——接受 dataset_id + evaluators + 可选的 tags/threshold/baseline_run_id，返回通过/失败报告
- [x] 15.4 更新 `POST /api/evaluation/runs` 以接受可选的 `tags` 参数用于按标签过滤的运行
- [x] 15.5 更新 `GET /api/evaluation/runs/{run_id}` 以包含通过/失败统计（total_items、passed_items、failed_items、pass_rate）
- [x] 15.6 添加 `PUT /api/evaluation/datasets/{id}/lock` 和 `PUT /api/evaluation/datasets/{id}/unlock` 端点
- [x] 15.7 添加 `PUT /api/evaluation/datasets/{id}/baseline` 端点用于设置基线运行

## 16. 告警集成

- [x] 16.1 向 `services/signal_provider.py` 添加 `EvaluationRegressionSignalProvider`——读取评估运行分数并计算回归指标
- [x] 16.2 更新 `NotificationDispatcher` 消息模板以包含评估回归详情（指标名称、增量值、运行 ID）

## 17. 测试——评估器

- [x] 17.1 使用通过/失败用例测试所有 9 个确定性格式评估器（exact_match、contains、contains_any、regex_match、is_json、format_check、bleu、rouge、f1）
- [x] 17.2 测试注册表函数：get_evaluator、list_evaluators（带和不带 category 过滤器）、装饰器注册
- [x] 17.3 测试 JudgePromptTemplate 构建和验证（scoring_scale 类型、量规完整性）
- [x] 17.4 使用模拟的 LLMService 测试 LLM-judge 评估器（toxicity、safety、instruction_following、coherence、fluency）
- [x] 17.5 使用模拟的 LLMService 测试引用评估器（citation_relevance、source_attribution、groundedness、hallucination_detection）
- [x] 17.6 使用模拟的 LLMService 和 tool_calls 输入测试工具评估器
- [x] 17.7 使用 conversation_history 输入测试多轮评估器
- [x] 17.8 使用模拟的 LLMService 测试安全评估器（prompt_injection、pii_leakage、jailbreak）
- [x] 17.9 测试编程评估器（带自定义函数的 python_code_eval、custom_callable）

## 18. 测试——回归与 API

- [x] 18.1 测试数据集版本管理：使用版本创建、锁定/解锁、设置 baseline_run_id
- [x] 18.2 测试项断言：使用断言创建项，断言覆盖数据集默认值
- [x] 18.3 测试按标签过滤的评估运行（仅评估带标签的项）
- [x] 18.4 使用断言和阈值测试通过/失败计算
- [x] 18.5 使用回归检测（分数下降 > 阈值）测试运行比较 API
- [x] 18.6 测试回归触发 API 返回结构化的通过/失败报告
- [x] 18.7 测试分数下降时的评估回归告警创建
- [x] 18.8 使用和不使用 category 过滤器测试评估器列表 API

## 19. 验证

- [x] 19.1 运行 `ruff check src/hecate/ tests/`——零错误
- [x] 19.2 运行 `ruff format --check src/ tests/`——零更改
- [x] 19.3 运行 `mypy src/`——零错误
- [x] 19.4 运行 `python -m pytest tests/ -q`——所有测试通过
