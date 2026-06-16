## 1. Implementation

- [x] 1.1 Implement `PostgresCheckpointStore` class in `engine/checkpoint.py` — inherits `CheckpointStore` ABC
- [x] 1.2 Implement `save()` method — create CheckpointModel, flush, return ID
- [x] 1.3 Implement `load()` method — query by session_id (latest) or checkpoint_id
- [x] 1.4 Implement `list_checkpoints()` method — query with limit, order by superstep desc
- [x] 1.5 Implement LRU cache for recent checkpoints — cache key = session_id
- [x] 1.6 Implement cache invalidation on save — update cache after successful DB write

## 2. Testing

- [x] 2.1 Unit tests for save — verify checkpoint persisted to DB
- [x] 2.2 Unit tests for load — verify latest and specific checkpoint retrieval
- [x] 2.3 Unit tests for list_checkpoints — verify ordering and limit
- [x] 2.4 Unit tests for cache — verify cache hit, miss, invalidation
- [ ] 2.5 Integration test with PregelRuntime — verify checkpoint/resume cycle
