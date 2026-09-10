## 1. DSL surface

- [x] 1.1 `graph-dsl.schema.json`:节点 `config` 新增 `cache` 对象——`ttl`(必填,正整数)、`key_func`(可选 string)、`scope`(enum `session|tenant`,默认 `session`)
- [x] 1.2 parser:`parse_graph()` 接受并传播 `cache` 块(ttl 非法即 `GraphValidationError`),`to_json()` roundtrip 保持不变;补 parse 层单测

## 2. Cache machinery(runtime)

- [x] 2.1 新增 `runtime/node_cache.py`:`InMemoryNodeCache`(OrderedDict LRU + 每条目 TTL,monotonic 时钟,条目上限 4096)+ `register_node_key_func` / `get_node_key_func` / `list_node_key_funcs` 注册表(镜像 `channel.py` reducer 先例);禁止 import `tools/`(自足性守卫)
- [x] 2.2 默认键推导:node id + 节点类型身份(CONVERSATION 掺 model 配置哈希)+ 可读通道切片 canonical JSON;键命名空间 `(scope, session_id|tenant_id, node_id, identity_hash, slice_hash)`
- [x] 2.3 `compiler.py`:节点 cache 策略校验(`key_func` 未注册名 → `GraphValidationError`,同 reducer 未知名 fail loud),策略随编译产物下发到节点表

## 3. Engine dispatch seam

- [x] 3.1 `pregel.py` 派发处接缓存:有策略节点先查键,命中伪造 `WorkerResult` 跳过 worker,未命中正常执行并回填;无策略节点零查询
- [x] 3.2 NODE_END 双路统一带 `cached`(bool)与 `cache_key`(命中为键哈希,未命中 null);命中路径不产生 LLM_REQUEST / LLM_RESPONSE
- [x] 3.3 作用域接线:session id 从会话取;tenant 上下文来源在实现时确认最小承载方式(执行入口显式传入优先)

## 4. Tests

- [x] 4.1 `tests/test_runtime/test_node_cache.py`:TTL 过期、LRU 驱逐、注册表(list/register)、默认键推导(model 配置变化 → 键变化;切片变化 → 键变化;session/tenant 命名空间隔离)
- [x] 4.2 DSL/compiler 测试:schema 接受与拒绝(ttl 缺失/非正/非整数)、roundtrip、未知 `key_func` 编译报错、任意节点类型可配
- [x] 4.3 引擎测试(`tests/test_runtime/test_pregel.py` 或新文件):命中跳过 worker(worker 调用计数断言)、未命中存储、**日志轨迹相等不变式**(同节点同输入 miss 与 hit 的事件序列 + 通道写入逐字一致,仅 NODE_END cached 标记不同)、命中无合成 LLM 事件、fold 等价(缓存命中后 restore 状态与执行路径一致)、缓存清空不影响重建、与 interrupt_before/after 共存、与动态扇出共存(分支切片相同键的重复执行不破坏 MERGE)

## 5. Cleanup and catalog

- [x] 5.1 修复 `types.py:190` 过时 docstring(`reduce_fn` "currently only 'add' is supported" → 反映 add/append + 注册表)
- [x] 5.2 feature-catalog / roadmap 1.3.21 行标注:reducer 半项随 ③ 交付;本变更为 1.3.21④ 节点级 CachePolicy(交付内容与调研定位摘要);**延后跟进项随行落 catalog 行防归档失联**(provider usage 透传=消费者拉动见 design、分布式后端、single_flight、大载荷 offloader、fork 缓存继承)

## 6. Verification

- [x] 6.1 四项检查全绿:`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`python -m pytest tests/ -q`
