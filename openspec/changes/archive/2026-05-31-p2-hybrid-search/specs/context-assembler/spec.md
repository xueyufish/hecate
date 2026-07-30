## 现有场景已修改

### ~~场景：知识库查询调用 knowledge_query~~ → 场景：知识库查询使用混合搜索模式调用 knowledge_query
- **当** 图执行知识检索（Knowledge Retrieval）节点，配置为 knowledge base "KB-1" 且 search_mode="hybrid"
- **则** EnginePort 调用 `knowledge_query(kb_id="KB-1", query="user input", top_k=5, search_mode="hybrid")`，同时从向量和关键词存储返回结果

### 知识检索节点的场景已更新
- **当** 图执行知识检索节点
- **则** 节点可配置 search_mode（vector|keyword|hybrid），混合搜索为默认值
