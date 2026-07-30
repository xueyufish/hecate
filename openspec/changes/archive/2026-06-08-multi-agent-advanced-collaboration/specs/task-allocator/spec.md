## 新增的需求

### 需求：TaskAllocator ABC 定义智能体选择接口
引擎应在 `engine/task_allocator.py` 中定义 `TaskAllocator` ABC，包含抽象方法 `allocate`，该方法接受任务描述、候选智能体列表和可选配置，返回最匹配的智能体或 None。

#### 场景：分配找到匹配候选
- **当** 调用 `allocate(task="Analyze financial report", candidates=[agent_a, agent_b])` 且 agent_a 是金融专家
- **则** 分配器应返回 agent_a

#### 场景：无合适候选
- **当** 调用 `allocate(task="Translate to Japanese", candidates=[finance_agent, legal_agent])` 且两者都不匹配
- **则** 分配器应返回 None

#### 场景：create_if_not_found 为 P3 预留
- **当** 调用 `allocate(task="...", candidates=[], create_if_not_found=True)`
- **则** P2 实现应抛出 `NotImplementedError`，附带指示此功能为 P3 动态智能体创建预留的消息

### 需求：SemanticTaskAllocator 使用 LLM 进行匹配
`SemanticTaskAllocator` 应通过调用 `port.llm_invoke()` 来实现 TaskAllocator，分析任务描述与候选智能体描述的匹配度，生成评分排名。

#### 场景：语义匹配
- **当** 调用 `allocate(task="Review legal contract", candidates=[billing_agent, legal_agent, tech_agent])`
- **则** 分配器应调用 `port.llm_invoke()`，使用包含任务描述和所有候选描述的提示词，解析 LLM 响应以提取最佳匹配，并返回 legal_agent

#### 场景：LLM 响应解析
- **当** LLM 返回包含智能体排名的结构化响应
- **则** 分配器应解析响应并返回排名最高的智能体

#### 场景：LLM 失败回退
- **当** `port.llm_invoke()` 在分配期间抛出异常
- **则** 分配器应记录错误并返回 None（而非抛出异常）

### 需求：用于简单场景的 RoundRobinTaskAllocator
`RoundRobinTaskAllocator` 应通过按顺序轮询候选来实现 TaskAllocator，适用于负载均衡场景。

#### 场景：轮询选择
- **当** 调用 `allocate(task="task_1", candidates=[a, b, c])` 三次
- **则** 它应按顺序返回 a、b、c，然后循环回到 a

#### 场景：空候选
- **当** 调用 `allocate(task="...", candidates=[])`
- **则** 它应返回 None
