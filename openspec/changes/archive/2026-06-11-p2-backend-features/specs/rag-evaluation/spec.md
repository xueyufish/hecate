## MODIFIED Requirements — 修改的需求

### 需求：Context Precision 评估器
系统应提供 `ContextPrecisionEvaluator`，衡量检索上下文中相关项是否排名更高。当安装了 `ragas` 时，它应使用 Ragas 的 `ContextPrecision` 指标。评估器应在可用时接受评估项中的 `generated_answer`，或在 `generated_answer` 为空时自动调用 RAG 管道

#### 场景：使用预生成答案评估
- **当** 评估项具有非空的 `generated_answer` 字段
- **则** 评估器应使用该答案进行评估，而不是调用 RAG 管道

#### 场景：使用 RAG 管道自动生成评估
- **当** 评估项的 `generated_answer` 字段为空且评估运行指定 `answer_source="pipeline"`
- **则** 系统应使用项的查询和检索到的上下文调用 RAG 管道生成答案，然后评估生成的答案

#### 场景：未安装 Ragas
- **当** 用户尝试使用 `ContextPrecisionEvaluator` 但未安装 `ragas`
- **则** 系统应抛出 `ImportError`，附带说明如何安装的消息：`pip install hecate[rag]`

### 需求：Context Recall 评估器
系统应提供 `ContextRecallEvaluator`，衡量检索到的上下文是否与期望答案一致。它应使用 Ragas 的 `ContextRecall` 指标。评估器应支持预生成和管道生成的答案

#### 场景：使用管道生成答案评估上下文覆盖率
- **当** RAG 评估运行时使用 `answer_source="pipeline"` 且项没有 `generated_answer`
- **则** 评估器应调用 RAG 管道生成答案，然后根据期望答案衡量上下文召回率

### 需求：Faithfulness 评估器
系统应提供 `FaithfulnessEvaluator`，衡量生成的答案是否与检索到的上下文在事实上一致（幻觉检测）。评估器应支持预生成和管道生成的答案

#### 场景：检测带管道生成答案的幻觉主张
- **当** RAG 管道生成的答案包含检索上下文不支持的主张
- **则** 评估器应返回 `metric_name="faithfulness"` 的 Score，每个不支持的主张会惩罚 `value`

### 需求：Answer Relevancy 评估器
系统应提供 `AnswerRelevancyEvaluator`，衡量生成的答案与用户问题的相关程度。评估器应支持预生成和管道生成的答案

#### 场景：使用管道生成答案评估答案相关性
- **当** RAG 管道为查询生成答案
- **则** 评估器应返回 `metric_name="answer_relevancy"` 的 Score，指示答案与问题意图之间的语义相似性
