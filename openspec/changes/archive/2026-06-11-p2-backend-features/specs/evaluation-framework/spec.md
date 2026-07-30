## ADDED Requirements — 新增需求

### 需求：带生成答案的评估数据集项
系统应使用 `generated_answer` 字段（TEXT，可空，默认 NULL）扩展 `EvaluationItemModel`。当提供时，此字段应包含系统生成的答案（来自 RAG 管道或代理执行）以用于评估。`EvaluationItemCreateSchema` 应接受可选的 `generated_answer` 字段。`EvaluationItemReadSchema` 应包含 `generated_answer`

#### 场景：使用预生成答案创建项
- **当** 发送 POST 请求创建评估项，携带 `{"query": "...", "generated_answer": "...", "expected_answer": "..."}`
- **则** 系统应存储 `generated_answer` 并在评估运行期间使用它

#### 场景：无生成答案创建项
- **当** 创建评估项时没有 `generated_answer`
- **则** 系统应存储 NULL 并允许评估引擎通过管道生成答案

### 需求：评估引擎答案源模式
系统应在评估运行创建中支持 `answer_source` 参数：`"manual"`（使用项的 `generated_answer` 字段）、`"pipeline"`（调用 RAG 管道或代理生成答案）、`"auto"`（默认——如果 `generated_answer` 存在则使用，否则调用管道）。`EvaluationRunCreateSchema` 应接受可选的 `answer_source` 字段

#### 场景：手动模式评估
- **当** 创建评估运行使用 `answer_source="manual"` 且项已填充 `generated_answer`
- **则** 引擎应使用提供的 `generated_answer` 评估每个项，无需调用任何管道

#### 场景：RAG 的管道模式评估
- **当** 创建评估运行使用 `answer_source="pipeline"` 且数据集与知识库关联
- **则** 引擎应为每个项调用 RAG 管道生成答案，然后评估它

#### 场景：自动模式回退
- **当** 创建评估运行使用 `answer_source="auto"`（或省略）且项的 `generated_answer=NULL`
- **则** 引擎应在评估前调用管道生成答案
