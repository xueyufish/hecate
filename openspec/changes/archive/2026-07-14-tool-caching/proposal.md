## Why — 为什么

Hecate 每次从头开始执行工具调用，即使在同一会话中使用相同参数调用相同工具也是如此。对于只读工具如 `web_search`、`read_file` 或 MCP 的 `list_*` 操作，这浪费了延迟和外部 API 配额。对 14 个平台的研究表明，代码执行框架（CrewAI、AgentScope、LangGraph）普遍实现了工具结果缓存，而 LangGraph-Redis 中间件引入了一个复杂的 7 优先级可缓存性链，该链根据元数据、副作用前缀和可变参数确定工具调用是否应该被缓存。

## What Changes — 变更内容

- **ToolCache**：带 TTL 的内存缓存，集成到 `ToolRegistry.execute()` 中。缓存命中时，返回缓存结果而不执行工具。缓存未命中时，正常执行并存储结果。
- **可缓存性优先级链**（灵感来自 LangGraph-Redis）：`cacheable` 标志 → `source` 启发式 → 副作用名称前缀 → `read_only + idempotent` → 可变参数检测 → 默认拒绝
- **按工具缓存配置**：`ToolModel` 上新增 `cacheable: bool | None` 和 `cache_ttl: int | None` 字段。`None` 表示通过优先级链自动检测。
- **会话作用域缓存**：默认情况下缓存条目作用域为 `session_id`。跨会话缓存需要每个工具选择加入。
- **规范键生成**：`hash(tool_name + canonical_json(args))`，附带可选的 `ignored_args` 剥离（request_id、trace_id 等）
- **缓存失效**：TTL 过期 + 手动 `DELETE /api/tools/cache` 端点 + 工具配置变更时自动失效
- **缓存指标**：命中率、未命中率、条目数——通过 `GET /api/tools/cache/stats` 暴露

## Capabilities — 能力

### 新能力

- `tool-caching`：带 TTL 的工具结果缓存、可缓存性优先级链、会话作用域条目、规范键生成、缓存失效和指标

### 变更的能力

- _（无——缓存对现有工具执行是透明的；ToolRegistry 获得一个可选的 cache 参数）_

## Impact — 影响

- **新文件**：
  - `src/hecate/services/tool/cache.py` — ToolCache 类（键生成、TTL、优先级链、会话作用域）
  - `src/hecate/api/management/tool_cache.py` — 用于缓存管理（统计、清除）的 REST API
  - `tests/test_services/test_tool_cache.py` — 缓存单元测试
- **修改的文件**：
  - `src/hecate/services/tool/registry.py` — ToolRegistry 获得可选的 `cache: ToolCache` 参数；`execute()` 在执行前检查缓存
  - `src/hecate/models/tool.py` — ToolModel 获得 `cacheable: bool | None` 和 `cache_ttl: int | None` 列
  - `src/hecate/core/config.py` — 新设置：`TOOL_CACHE_ENABLED`、`TOOL_CACHE_DEFAULT_TTL`、`TOOL_CACHE_MAX_ENTRIES`、`TOOL_CACHE_SESSION_SCOPED`
  - `src/hecate/main.py` — 注册 tool_cache 路由
  - `alembic/versions/` — ToolModel 新列的迁移
- **依赖**：无新增（使用现有的 stdlib `hashlib`、`json`、`time`）
