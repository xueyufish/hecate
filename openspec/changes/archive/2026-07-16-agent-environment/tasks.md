## 1. Configuration — 配置

- [x] 1.1 向 `src/hecate/core/config.py` 添加设置：`AGENT_ENV_ENABLED: bool = True`，`AGENT_ENV_TTL: int = 86400`（24 小时，以秒为单位）

## 2. AgentEnvironment ABC + LocalEnvironment

- [x] 2.1 创建 `src/hecate/services/environment/__init__.py`
- [x] 2.2 创建 `src/hecate/services/environment/environment.py` — `FileInfo` 数据类（name、path、size、modified_at、is_dir），`AgentEnvironment` ABC（environment_id、root_path、read_file、write_file、list_files、delete_file、exists、ensure_dirs），`LocalEnvironment` 实现，使用 `WORKSPACE_ROOT/{agent_id}/` 及子目录：sessions/、files/、memory/、skills/

## 3. EnvironmentManager

- [x] 3.1 创建 `src/hecate/services/environment/manager.py` — `EnvironmentManager` 类，包含：`get_or_create(agent_id) -> AgentEnvironment`（懒创建 + 缓存），`close(agent_id)`，`close_all()`，TTL 驱逐（空闲环境自动清理），asyncio.Lock 线程安全

## 4. REST API

- [x] 4.1 创建 `src/hecate/api/management/environment.py` — 路由前缀 `/api/agents/{agent_id}/environment`：`GET /files`（列出），`GET /files/{path}`（读取），`POST /files`（上传），`DELETE /files/{path}`（删除），`GET /stats`（文件数量、总大小）
- [x] 4.2 在 `src/hecate/main.py` 中注册 `environment_router`

## 5. WorkflowExecutionService Integration — WorkflowExecutionService 集成

- [x] 5.1 更新 `src/hecate/services/workflow/execution_service.py` — 添加 `environment_manager` 参数，执行前调用 `get_or_create(agent_id)`，在 `execution_context` 中传递环境根路径

## 6. Tests — 测试

- [x] 6.1 测试 `LocalEnvironment` — 写入/读取/列出/删除文件，ensure_dirs 创建子目录，exists 返回正确布尔值
- [x] 6.2 测试 `EnvironmentManager` — 懒创建、缓存复用、TTL 驱逐、close_all、并发访问安全
- [x] 6.3 测试 REST API — 通过 httpx AsyncClient 列出/读取/写入/删除文件，统计端点

## 7. Verification — 验证

- [x] 7.1 运行 `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 错误
- [x] 7.2 运行 `mypy src/` — 0 错误
- [x] 7.3 运行 `python -m pytest tests/test_services/test_environment/ -q` — 全部通过
