## MODIFIED Requirements — 修改的需求

### Requirement: Fan-out and merge in node palette — 节点面板中的 Fan-out 和 Merge
NodePalette 组件应将 fan-out 和 merge 作为可拖拽项包含在内。除了从现有 DSL 加载时出现外，这些节点类型也可通过从面板拖拽以交互方式创建。

#### Scenario: Node palette contents — 节点面板内容
- **当** 工作流编辑器加载
- **则** 节点面板显示 8 项：Conversation、Condition、Tool Call、Agent、Knowledge Retrieval、Variable Set、Fan Out、Merge
