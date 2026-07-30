## MODIFIED Requirements — 修改的需求

### Requirement: 工具执行 — Tool execution
- **当** 调用 `tool_execute(name, args, context)`
- **则** 它应通过 ToolRegistry 路由调用，由 ToolRegistry 按名称和源类型解析工具，通过相应的执行器执行它，并返回工具的结果

#### Scenario: 通过注册表的工具执行 — Tool execution via registry
- **当** 调用 `tool_execute("web_search", {"query": "test"}, context)`
- **则** 适配器应委派给 `ToolRegistry.execute("web_search", {"query": "test"}, context)` 并返回注册表的结果

#### Scenario: 未找到工具 — Tool not found
- **当** 调用 `tool_execute("nonexistent", args, context)` 且工具不存在
- **则** 它应抛出 `ValueError`，消息指示未找到该工具
