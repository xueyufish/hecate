## ADDED Requirements — 新增需求

### Requirement: ToolRegistry 按源类型路由工具执行 — ToolRegistry routes tool execution by source type
系统应在 `services/tool/registry.py` 中提供一个 `ToolRegistry` 服务，接受工具名称、参数和可选上下文，按源类型查找工具定义，并将执行路由到相应的执行器。

#### Scenario: 内置工具执行 — Built-in tool execution
- **当** 调用 `registry.execute("web_search", {"query": "test"}, context)` 且存在名为 "web_search" 的内置工具
- **则** 注册表应路由到 `BuiltInToolExecutor` 并返回工具的结果

#### Scenario: 自定义工具执行（尚未实现）— Custom tool execution (not yet implemented)
- **当** 调用 `registry.execute("my_tool", args, context)` 且工具具有 `source="custom"`
- **则** 注册表应抛出 `NotImplementedError`，消息指示自定义工具执行尚不可用

#### Scenario: MCP 工具执行（尚未实现）— MCP tool execution (not yet implemented)
- **当** 调用 `registry.execute("mcp_tool", args, context)` 且工具具有 `source="mcp"`
- **则** 注册表应抛出 `NotImplementedError`，消息指示 MCP 工具执行尚不可用

#### Scenario: 未知工具名称 — Unknown tool name
- **当** 调用 `registry.execute("nonexistent", args, context)` 且不存在该名称的工具
- **则** 注册表应抛出 `ValueError`，消息指示未找到该工具

### Requirement: ToolRegistry 使用内存内置查找和数据库回退 — ToolRegistry uses in-memory builtin lookup with DB fallback
注册表应维护一个内置工具名称的内存集合以进行快速路由。对于非内置工具，它应按名称和 workspace_id 查询 `ToolModel` 表。

#### Scenario: 内置工具无需数据库查询即可解析 — Builtin tool resolves without DB query
- **当** 查找内置工具名称
- **则** 注册表应直接路由到内置执行器，无需查询数据库

#### Scenario: 非内置工具查询数据库 — Non-builtin tool queries database
- **当** 查找非内置工具名称
- **则** 注册表应查询 `ToolModel` 表以确定源类型

### Requirement: 内置工具定义在启动时种子到数据库 — Built-in tool definitions are seeded to DB on startup
系统应在应用程序启动期间将内置工具定义（名称、描述、参数模式、source="builtin"）种子到 `tools` 数据库表。

#### Scenario: 空数据库获得所有内置工具 — Fresh database gets all builtin tools
- **当** 应用程序以空的 tools 表启动
- **则** 所有 5 个内置工具（web_search、read_file、write_file、list_files、execute_code）应以 `source="builtin"` 和 `workspace_id=00000000` 被插入

#### Scenario: 已有的内置工具被更新，不重复 — Existing builtin tools are updated, not duplicated
- **当** 应用程序启动且内置工具已存在于数据库中
- **则** 种子函数应更新工具定义（描述、参数），如果它们与代码不同，而不创建重复项
