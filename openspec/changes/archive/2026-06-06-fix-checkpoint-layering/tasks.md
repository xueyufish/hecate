## 1. 升级 services/checkpoint_store.py

- [x] 1.1 添加 `OrderedDict` 导入，将 `_cache` 从普通 `dict` 改为 `OrderedDict`，并添加可配置的 `cache_size` 参数（默认 128）
- [x] 1.2 添加 `_update_cache()` LRU 驱逐方法 — 命中时 move_to_end，溢出时 popitem(last=False)
- [x] 1.3 更新 `save()` 调用 `_update_cache()` 替代直接字典赋值
- [x] 1.4 更新 `load()` 缓存未命中路径：当 `checkpoint_id is None` 且缓存未命中时，查询数据库获取最新 checkpoint 并缓存结果（当前在缓存未命中时返回 None）
- [x] 1.5 添加 `_checkpoint_to_dict()` 静态方法，用于一致的 model 到 dict 转换
- [x] 1.6 将 `load()` 和 `list_checkpoints()` 中的内联字典构造替换为 `_checkpoint_to_dict()`
- [x] 1.7 添加 `logging` 导入，为保存、缓存命中、缓存未命中事件添加调试级别的日志消息
- [x] 1.8 为类、`__init__` 和所有公开方法添加完整的文档字符串（与 engine 版本质量匹配）

## 2. 清理 engine/checkpoint.py

- [x] 2.1 删除整个 `PostgresCheckpointStore` 类（第 137-301 行）
- [x] 2.2 移除 `sqlalchemy` 导入（`select`、`AsyncSession`）
- [x] 2.3 移除 `from hecate.models.checkpoint import CheckpointModel` 导入
- [x] 2.4 移除 `from collections import OrderedDict` 导入（不再需要）
- [x] 2.5 更新模块文档字符串，移除 `PostgresCheckpointStore` 引用
- [x] 2.6 验证 `engine/checkpoint.py` 没有从 `models/`、`services/`、`sqlalchemy` 导入

## 3. 迁移测试

- [x] 3.1 创建 `tests/test_services/test_checkpoint_store.py`
- [x] 3.2 添加一个 `session_factory` fixture，将 conftest 的 `db_session` 包装到 `async_sessionmaker` 中
- [x] 3.3 从 `tests/test_engine/test_postgres_checkpoint.py` 复制所有测试用例，更新导入为 `from hecate.services.checkpoint_store import PostgresCheckpointStore`
- [x] 3.4 将所有测试实例化从 `PostgresCheckpointStore(db_session)` 更新为 `PostgresCheckpointStore(session_factory)`
- [x] 3.5 更新 `test_cache_eviction` 以引用 `_cache` 为 `OrderedDict`（断言驱逐仍然有效）
- [x] 3.6 删除 `tests/test_engine/test_postgres_checkpoint.py`

## 4. 验证

- [x] 4.1 运行 `ruff check src/hecate/ tests/` — 零错误
- [x] 4.2 运行 `ruff format --check src/ tests/` — 零错误
- [x] 4.3 运行 `mypy src/` — 零错误
- [x] 4.4 运行 `python -m pytest tests/ -q` — 所有测试通过
- [x] 4.5 验证 `src/hecate/engine/` 中没有文件从 `models/` 导入（grep 检查）
