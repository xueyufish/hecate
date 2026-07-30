## 1. Search Provider Abstraction — 搜索提供商抽象

- [x] 1.1 创建 `src/hecate/services/tool/search/__init__.py`，包含 `SearchProvider` ABC：`search(query: str, max_results: int) -> list[dict]`
- [x] 1.2 创建 `src/hecate/services/tool/search/duckduckgo.py`——DuckDuckGo 提供商（默认，无需 API 密钥），使用 `duckduckgo-search` 包
- [x] 1.3 创建 `src/hecate/services/tool/search/tavily.py`——Tavily 提供商，使用 `tavily-python` 包
- [x] 1.4 创建 `src/hecate/services/tool/search/serper.py`——Serper 提供商，使用 HTTP API
- [x] 1.5 创建 `src/hecate/services/tool/search/factory.py`——`create_search_provider()` 读取 `SEARCH_PROVIDER` + `SEARCH_API_KEY` 环境变量，返回正确的提供商实例
- [x] 1.6 在 `pyproject.toml` 的可选 `[tools]` 依赖组中添加 `duckduckgo-search`；将 `tavily-python` 添加到 `[tools]`

## 2. BuiltInToolExecutor — 内置工具执行器

- [x] 2.1 创建 `src/hecate/services/tool/__init__.py`（空，包标记）
- [x] 2.2 创建 `src/hecate/services/tool/builtin.py`，包含 `BuiltInToolExecutor` 类
- [x] 2.3 实现 `web_search` 工具：接受 `query` + `max_results`，委派给 `SearchProvider`，返回 `{title, url, snippet}` 列表
- [x] 2.4 实现 `read_file` 工具：接受 `path`，针对 `WORKSPACE_ROOT` 解析，净化路径，读取并返回文件内容
- [x] 2.5 实现 `write_file` 工具：接受 `path` + `content`，针对 `WORKSPACE_ROOT` 解析，净化路径，创建父目录，写入文件
- [x] 2.6 实现 `list_files` 工具：接受可选的 `path`，针对 `WORKSPACE_ROOT` 解析，返回目录列表
- [x] 2.7 实现 `execute_code` 工具：接受 `code`，委派给 `SandboxExecutor.execute()`，返回 `{stdout, stderr, exit_code, timed_out}`；优雅处理 Docker 不可用
- [x] 2.8 定义 `BUILTIN_TOOL_DEFINITIONS` 字典，将工具名称映射到 `{description, parameters (JSON Schema)}`（全部 5 个工具）

## 3. ToolRegistry — 工具注册表

- [x] 3.1 创建 `src/hecate/services/tool/registry.py`，包含 `ToolRegistry` 类
- [x] 3.2 实现 `execute(name, args, context)` 方法：先检查内置名称集合，然后对非内置工具查询数据库，按源类型路由
- [x] 3.3 实现 builtin 路由：委派给 `BuiltInToolExecutor`
- [x] 3.4 实现 custom/mcp 路由：抛出 `NotImplementedError`
- [x] 3.5 实现 `seed_builtin_tools(db)` 函数：将内置工具定义更新插入到 `tools` 表，`source="builtin"`、`workspace_id=00000000`
- [x] 3.6 将 `WORKSPACE_ROOT` 和 `SEARCH_PROVIDER` / `SEARCH_API_KEY` 添加到 `src/hecate/core/config.py`（pydantic-settings）

## 4. Wire into EnginePort — 接入 EnginePort

- [x] 4.1 修改 `src/hecate/services/orchestration/engine_port_adapter.py`：将 `ToolRegistry` 注入 `_ProductionEnginePort.__init__`，用 `self._tool_registry.execute(name, args, context)` 替换存根 `tool_execute()`
- [x] 4.2 更新 `create_engine_port()` 工厂以接受并传递 `ToolRegistry`
- [x] 4.3 查找并更新所有 `create_engine_port()` 调用点以传递注册表实例

## 5. Startup Seed — 启动种子

- [x] 5.1 在 `src/hecate/main.py`（FastAPI `lifespan` 或 `@app.on_event("startup")`）中添加启动事件，调用 `seed_builtin_tools(db)`
- [x] 5.2 验证启动后 `GET /api/tools?source=builtin` 返回全部 5 个内置工具

## 6. Tests — 测试

- [x] 6.1 创建 `tests/test_services/test_tool/__init__.py`
- [x] 6.2 创建 `tests/test_services/test_tool/test_search_providers.py`——测试 DuckDuckGo 提供商（实况或 mock）、工厂解析
- [x] 6.3 创建 `tests/test_services/test_tool/test_builtin_executor.py`——使用 mock 文件系统和 mock 沙箱测试 5 个工具中的每一个
- [x] 6.4 创建 `tests/test_services/test_tool/test_registry.py`——测试按源类型路由、内置查找、未知工具错误、custom/mcp 的 NotImplementedError
- [x] 6.5 创建 `tests/test_services/test_tool/test_seed.py`——测试种子函数插入内置工具、处理重复（更新插入）
- [x] 6.6 测试 read_file、write_file、list_files 的路径遍历防护
- [x] 6.7 运行 `python -m pytest tests/test_services/test_tool/ -v`——全部通过

## 7. Verification — 验证

- [x] 7.1 运行 `ruff check src/hecate/services/tool/ tests/test_services/test_tool/`
- [x] 7.2 运行 `ruff format --check src/hecate/services/tool/ tests/test_services/test_tool/`
- [x] 7.3 运行 `mypy src/hecate/services/tool/ src/hecate/services/orchestration/engine_port_adapter.py`
- [x] 7.4 运行 `python -m pytest tests/ -q`——无回归
