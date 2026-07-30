## 新增需求

### 需求：引用 Schema
系统须为引用元数据定义 schema，包含 source_type（knowledge_base, tool_call 或 message）、source_id、excerpt、relevance_score、title 和 url。

#### 场景：创建引用对象
- **当** 系统从知识库查询结果创建引用
- **则** 引用对象包含 source_type="knowledge_base"、source_id（段落 ID）、excerpt（匹配文本）、relevance_score（分值）、title（文档标题）

#### 场景：从工具调用结果创建引用
- **当** 系统从工具执行结果创建引用
- **则** 引用对象包含 source_type="tool_call"、source_id（工具调用 ID）、excerpt（结果摘要）、title（工具名称）

### 需求：消息响应中的引用
系统须在消息响应中包含引用数据。`MessageReadSchema` 须包含可选的 `citations` 字段。

#### 场景：消息响应包含引用
- **当** LLM 回复伴有一个知识库查询和两个工具调用
- **则** `MessageReadSchema` 响应包含 3 个引用对象，包含 source_type、excerpt 和相关性分值

#### 场景：无引用时的消息响应
- **当** LLM 回复不涉及知识库查询或工具调用
- **则** `MessageReadSchema` 响应包含 `citations: []`

### 需求：引用提取 —— 知识库
系统须从向量搜索结果中提取引用。每个带有元数据的知识库查询结果须转换为 `CitationSchema` 对象。

#### 场景：从知识库结果提取引用
- **当** `knowledge_query()` 返回带有 `source_id`, `excerpt`, `relevance_score`, `document_title` 的结果
- **则** 每个结果转换为包含这些字段的 `CitationSchema` 对象

### 需求：引用提取 —— 工具调用
系统须从工具执行结果中提取引用。当工具结果 JSON 包含标准引用字段时，它们须转换为 `CitationSchema` 对象。

#### 场景：从工具结果提取引用（有引用字段）
- **当** 工具返回 `{"result": "success", "source": "database://orders/123", "reference": "Order #123"}`
- **则** 创建引用对象，source_type="tool_call"、source_id="database://orders/123"、title="Order #123"

#### 场景：从工具结果提取引用（无引用字段）
- **当** 工具返回 `{"result": "42", "unit": "celsius"}`
- **则** 无引用创建

### 需求：内联引用 UI
系统须在消息气泡内渲染引用。引用编号标记（如 [1]、[2]）须作为可交互元素内联出现，可点击展开显示引用细节。

#### 场景：内联引用标签
- **当** Agent 回复包含文本 "根据政策 [1]，年假为 20 天 [2]"
- **则** "[1]" 和 "[2]" 渲染为可交互标签，不可点击时为带下划线的灰色数字，可展开

#### 场景：展开引用卡片
- **当** 用户点击引用标签 "[1]"
- **则** 展开的引用卡片显示在消息气泡内，包含标题、摘录文本和相关性分值

#### 场景：引用卡片可折叠
- **当** 用户点击展开的引用卡片
- **则** 卡片折叠回内联标签状态
