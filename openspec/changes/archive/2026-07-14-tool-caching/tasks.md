## 1. Configuration — 配置

- [x] 1.1 向 `src/hecate/core/config.py` 添加工具缓存设置：`TOOL_CACHE_ENABLED: bool = True`、`TOOL_CACHE_DEFAULT_TTL: int = 300`、`TOOL_CACHE_MAX_ENTRIES: int = 10000`、`TOOL_CACHE_SESSION_SCOPED: bool = True`

## 2. ToolModel Fields — ToolModel 字段

- [x] 2.1 向 `src/hecate/models/tool.py` 添加字段：`cacheable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)`、`cache_ttl: Mapped[int | None] = mapped_column(Integer, nullable=True)`。更新 CreateSchema 和 ReadSchema。
- [x] 2.2 为新列创建 Alembic 迁移。

## 3. ToolCache Implementation — ToolCache 实现

- [x] 3.1 创建 `src/hecate/services/tool/cache.py` — `CacheEntry` dataclass（result、created_at、ttl、last_accessed），`ToolCache` 类包含：`get(key) -> CacheEntry | None`（更新 last_accessed）、`set(key, result, ttl)`、`invalidate(tool_name)`、`invalidate_all()`、`stats() -> dict`（hits、misses、entries、hit_rate）、`_canonical_json(args, ignored_args) -> str`、`_make_key(tool_name, args, session_id, ignored_args) -> str`、`_sweep_expired()`、使用 `collections.OrderedDict` 在 max_entries 时进行 LRU 淘汰。
- [x] 3.2 实现 `is_cacheable(tool_meta) -> bool` 优先级链：P1 显式 cacheable 标志、P2 副作用名称前缀、P3 危险内置集合、P4 risk_level + sandbox_enabled 默认启发式。

## 4. ToolRegistry Integration — ToolRegistry 集成

- [x] 4.1 更新 `src/hecate/services/tool/registry.py` — `ToolRegistry.__init__()` 获得可选的 `cache: ToolCache | None`。在 `execute()` 中：执行前，检查 `is_cacheable()` → 生成键 → `cache.get(key)` → 命中时返回。执行后，`cache.set(key, result, ttl)`。

## 5. REST API — REST API

- [x] 5.1 创建 `src/hecate/api/management/tool_cache.py` — 路由前缀 `/api/tools/cache`：`GET /stats`（hit/miss/entries/hit_rate）、`DELETE /`（清除所有，可选 `?tool_name=` 过滤）。
- [x] 5.2 在 `src/hecate/main.py` 中注册 `tool_cache_router`。

## 6. Tests — 测试

- [x] 6.1 测试 `ToolCache` — set/get、TTL 过期、LRU 淘汰、规范键（字典键排序）、ignored_args 剥离、按工具名称失效、全部失效、统计准确性。
- [x] 6.2 测试 `is_cacheable()` 优先级链 — 显式 True/False 覆盖、副作用前缀跳过、危险内置跳过、risk_level 启发式（LOW 缓存、HIGH 跳过）、sandbox_enabled 跳过。
- [x] 6.3 测试 ToolRegistry 集成 — 缓存未命中执行、缓存命中返回缓存、不可缓存工具绕过缓存、会话作用域（不同会话不共享）。
- [x] 6.4 测试 REST API — GET 统计、DELETE 全部清除、DELETE 按工具清除。

## 7. Verification — 验证

- [x] 7.1 运行 `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 错误
- [x] 7.2 运行 `mypy src/` — 0 错误
- [x] 7.3 运行 `python -m pytest tests/test_services/test_tool_cache.py -q` — 全部通过
