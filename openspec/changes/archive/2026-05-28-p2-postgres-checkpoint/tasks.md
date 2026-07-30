## 1. 实现

- [x] 1.1 在 `engine/checkpoint.py` 中实现 `PostgresCheckpointStore` 类——继承 `CheckpointStore` ABC
- [x] 1.2 实现 `save()` 方法——创建 CheckpointModel，flush，返回 ID
- [x] 1.3 实现 `load()` 方法——按 session_id（最新）或 checkpoint_id 查询
- [x] 1.4 实现 `list_checkpoints()` 方法——带 limit 参数，按 superstep 降序排列
- [x] 1.5 为近期 checkpoint 实现 LRU 缓存——缓存键 = session_id
- [x] 1.6 实现保存时缓存失效——在成功写入 DB 后更新缓存

## 2. 测试

- [x] 2.1 save 单元测试——验证 checkpoint 持久化到 DB
- [x] 2.2 load 单元测试——验证最新和特定 checkpoint 的检索
- [x] 2.3 list_checkpoints 单元测试——验证排序和 limit
- [x] 2.4 缓存单元测试——验证缓存命中、未命中、失效
- [ ] 2.5 与 PregelRuntime 的集成测试——验证 checkpoint/resume 周期
