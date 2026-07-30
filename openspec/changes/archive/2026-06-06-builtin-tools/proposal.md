## Why — 为什么

`_ProductionEnginePort.tool_execute()` 和 `ConversationService._execute_tools_with_evidence()` 都返回 mock 字符串（`"Executed {name} with args {args}"`）。整个工具执行链是断开的——LLM 决定调用工具，ToolWorker 解析它们，但没有真实的工具逻辑运行。Agent 无法执行任何实际工作（搜索网络、读取文件、执行代码）。这是 P1 完成的最后一个关键缺口。

## What Changes — 变更内容

- 添加一个 `ToolRegistry` 服务，按源类型（builtin/custom/mcp）路由 `tool_execute()` 调用
- 添加一个 `BuiltInToolExecutor`，注册并执行 5 个内置工具：`web_search`、`read_file`、`write_file`、`list_files`、`execute_code`
- 将 `ToolRegistry` 接入 `_ProductionEnginePort.tool_execute()`，替换 mock 存根
- 通过环境变量添加可配置的搜索提供商支持（Tavily/Serper/DuckDuckGo）
- 在启动时将内置工具定义种子到 `tools` 数据库表（`source="builtin"`、`workspace_id=00000000`）
- `execute_code` 使用现有的 `SandboxExecutor` + `SandboxPool` 基础设施
- ToolRegistry 接口支持所有三种源类型；本变更仅实现 `builtin` 路径
- `custom` 和 `mcp` 路由路径被保留（抛出 `NotImplementedError`）供未来的 P2/P3 实现
- `ConversationService._execute_tools_with_evidence()` 存根保持不变——它将被统一引擎迁移替代

## Capabilities — 能力

### 新能力
- `tool-registry`：中央工具路由服务，按源类型（builtin/custom/mcp）将工具名称映射到执行器
- `builtin-tools`：五个内置工具（web_search、read_file、write_file、list_files、execute_code），带可配置的搜索提供商和基于沙箱的代码执行

### 修改的能力
- `engine-ports`：`tool_execute()` 通过 ToolRegistry 获得具体实现，而非 mock 存根

## Impact — 影响

- **新文件**：`src/hecate/services/tool/registry.py`、`src/hecate/services/tool/builtin.py`、`src/hecate/services/tool/search/`（搜索提供商适配器）
- **修改的文件**：`src/hecate/services/orchestration/engine_port_adapter.py`（接入 ToolRegistry）、`src/hecate/core/config.py`（添加搜索提供商配置）
- **新测试**：`tests/test_services/test_tool/`（注册表 + 内置工具 + 搜索提供商）
- **新依赖**：`tavily-python` 或 `duckduckgo-search`（可选，基于提供商选择）在 `[dev]` 或 `[tools]` 组中
- **数据库迁移**：通过启动事件将内置工具定义种子到 `tools` 表（非 Alembic 迁移——数据可从代码重现）
- **无破坏性变更**：现有 API 行为保留；之前返回 mock 字符串的工具现在返回真实结果
