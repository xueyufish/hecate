## ADDED Requirements — 新增需求

### 需求：工具调用准确性评估器
系统应提供 `ToolCallAccuracyEvaluator`，衡量代理是否选择了正确的工具并带有正确的参数。它应使用 LLM-as-Judge 来比较实际工具调用与评估项中提供的预期工具调用。该指标应对工具选择正确性和参数准确性进行评分

#### 场景：正确的工具选择和参数
- **当** 代理评估项包含 `tool_calls`，内容为 `[{"name": "search_web", "parameters": {"query": "weather"}}]` 且预期工具调用匹配
- **则** 评估器应返回 `metric_name="tool_call_accuracy"` 且 `value` 接近 1.0 的 Score

#### 场景：选择了错误的工具
- **当** 代理选择了 `send_email` 而非预期的 `search_web`
- **则** 评估器应返回 `value` 接近 0.0 的 Score，并附带解释不匹配的 `reasoning`

#### 场景：未提供工具调用
- **当** 请求工具调用准确性评估时，评估项中没有 `tool_calls`
- **则** 评估器应返回 `value=-1.0` 和 `reasoning="No tool_calls provided"` 的 Score

### 需求：任务完成评估器
系统应提供 `TaskCompletionEvaluator`，衡量代理是否成功完成分配的任务。它应使用 LLM-as-Judge 评估最终响应是否展示了任务完成，考虑原始查询、任何中间步骤和最终答案

#### 场景：任务完全完成
- **当** 代理响应表明分配的任务已完全完成
- **则** 评估器应返回 `metric_name="task_completion"` 且 `value=1.0` 的 Score

#### 场景：任务部分完成
- **当** 代理响应涉及任务的某些方面但遗漏了其他方面
- **则** 评估器应返回 `metric_name="task_completion"` 且 `value` 与完成比例成比例的 Score

#### 场景：任务未尝试
- **当** 代理响应与分配的任务无关
- **则** 评估器应返回 `metric_name="task_completion"` 且 `value` 接近 0.0 的 Score

## MODIFIED Requirements — 修改的需求

### 需求：正确性评估器
系统应提供 `CorrectnessEvaluator`，使用 LLM-as-Judge 比较生成的答案与期望答案。它应评估事实准确性和完整性。评估器应在可用时使用评估项中的 `generated_answer`

#### 场景：使用预生成答案与真实答案比较
- **当** 评估项中提供了生成的答案和期望的答案
- **则** 评估器应使用 LLM-as-Judge 返回反映事实准确性的 Score

#### 场景：未提供期望答案
- **当** 请求正确性评估时没有期望答案
- **则** 评估器应返回 `value=-1.0` 和 `reasoning="No expected_answer provided"` 的 Score
