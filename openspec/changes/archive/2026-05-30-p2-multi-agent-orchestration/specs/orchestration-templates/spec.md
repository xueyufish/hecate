## 新增需求

### 需求：编排模板列表 API
系统须暴露 `GET /api/orchestration-templates` 端点，返回所有可用编排模板的名称、描述和图结构预览。

#### 场景：列出模板
- **当** 调用 `GET /api/orchestration-templates`
- **则** 响应包含模板列表，每个模板有 `id`, `name`, `description`, `category` 和 `preview` 字段

#### 场景：模板预览包含图摘要
- **当** 模板在列表中被返回
- **则** `preview` 字段包含模板中的 agent 节点数和边数

### 需求：编排模板详情 API
系统须暴露 `GET /api/orchestration-templates/{template_id}` 端点，返回模板的完整 Graph DSL JSON。

#### 场景：获取模板详情
- **当** 调用 `GET /api/orchestration-templates/customer-service-triage`
- **则** 响应包含可加载到画布编辑器的完整 Graph DSL JSON

#### 场景：模板未找到
- **当** 调用 `GET /api/orchestration-templates/nonexistent`
- **则** 响应为 404，错误码 `NOT_FOUND`

### 需求：客服分类模板
系统须包含预构建的"Customer Service Triage"编排模板，包含通过 handoff 边连接到账单、技术和通用专业 agent 的路由器 agent。

#### 场景：分类模板结构
- **当** 加载 Customer Service Triage 模板
- **则** 图包含 4 个 AGENT 节点（router, billing, technical, general）和从 router 到每个 specialist 的 3 条 handoff 边

#### 场景：分类模板路由器有 handoff 工具
- **当** 模板被编译且 router agent 执行
- **则** router agent 的工具列表包含针对 3 个 specialist 的 `handoff_to_agent` 工具

### 需求：内容管线模板
系统须包含预构建的"Content Pipeline"编排模板，包含以线性链连接的研究员、写手和评审 agent。

#### 场景：管线模板结构
- **当** 加载 Content Pipeline 模板
- **则** 图包含 3 个 AGENT 节点（researcher, writer, reviewer），通过标准边在线性链中连接，并有一个检查评审状态的 condition 节点

### 需求：层级监督者模板
系统须包含预构建的"Hierarchical Supervisor"编排模板，包含一个通过基于工具的调用委派任务给工作 agent 的监督者 agent。

#### 场景：监督者模板结构
- **当** 加载 Hierarchical Supervisor 模板
- **则** 图包含 1 个 `invocation_mode: "tool"` 的监督者 AGENT 节点，引用 N 个工作 agent，以及用于重新委派的条件循环
