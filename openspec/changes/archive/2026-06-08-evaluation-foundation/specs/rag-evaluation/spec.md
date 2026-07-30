## ADDED Requirements — 新增的需求

### Requirement: Context Precision evaluator — 需求：Context Precision 评估器
系统应提供一个 `ContextPrecisionEvaluator`，衡量检索上下文中相关项是否排名更高。当安装了 `ragas` 时，应使用 Ragas 的 `ContextPrecision` 指标。

#### Scenario: Evaluate ranked retrieval results — 场景：评估排名的检索结果
- **WHEN — 当** RAG 查询返回按相关性排序的上下文，并提供了基本事实答案
- **THEN — 则** 评估器应返回带 `metric_name="context_precision"` 和 0.0 到 1.0 之间的 `value` 的 Score，指示相关上下文是否出现在顶部

#### Scenario: Ragas not installed — 场景：Ragas 未安装
- **WHEN — 当** 用户尝试使用 `ContextPrecisionEvaluator` 但未安装 `ragas`
- **THEN — 则** 系统应引发 `ImportError` 并附带解释如何安装的消息：`pip install hecate[rag]`

### Requirement: Context Recall evaluator — 需求：Context Recall 评估器
系统应提供一个 `ContextRecallEvaluator`，衡量检索上下文是否与预期答案一致。应使用 Ragas 的 `ContextRecall` 指标。

#### Scenario: Evaluate context coverage — 场景：评估上下文覆盖
- **WHEN — 当** RAG 查询返回上下文并提供了预期答案
- **THEN — 则** 评估器应返回带 `metric_name="context_recall"` 的 Score，指示检索上下文覆盖预期答案的程度

### Requirement: Faithfulness evaluator — 需求：Faithfulness 评估器
系统应提供一个 `FaithfulnessEvaluator`，衡量生成答案是否与检索上下文在事实上一致（幻觉检测）。

#### Scenario: Detect hallucinated claims — 场景：检测幻觉性声明
- **WHEN — 当** 生成答案包含检索上下文不支持的声明
- **THEN — 则** 评估器应返回带 `metric_name="faithfulness"` 的 Score，每个不支持声明的 `value` 受罚

#### Scenario: Fully faithful answer — 场景：完全忠实的答案
- **WHEN — 当** 生成答案中的每个声明都可以追溯到检索上下文
- **THEN — 则** 评估器应返回 `value=1.0`

### Requirement: Answer Relevancy evaluator — 需求：Answer Relevancy 评估器
系统应提供一个 `AnswerRelevancyEvaluator`，衡量生成答案与用户问题的相关性。

#### Scenario: Evaluate answer relevance — 场景：评估答案相关性
- **WHEN — 当** 生成答案针对原始查询进行评估
- **THEN — 则** 评估器应返回带 `metric_name="answer_relevancy"` 的 Score，指示答案和问题意图之间的语义相似度
