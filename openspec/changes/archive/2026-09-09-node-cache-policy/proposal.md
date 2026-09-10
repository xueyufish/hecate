## Why

1.3.21 ride-along 收尾:三个主项(①声明式中断、②时间旅行、③动态扇出)已交付,ride-along 的 reducer 半项已随 ③ 交付(`channel.py` 的 `register_reducer` 注册表 + 内建 `add`/`append` + 编译期校验),仅剩节点级 CachePolicy 未落地。CONVERSATION(上下文组装 + 记忆加载 + 压缩 + LLM 调用)与 KNOWLEDGE_RETRIEVAL(KB 查询)是运行时最贵的两类节点;KB 查询的输入天然与会话无关,跨会话重复提问的成本浪费真实存在。2026-09 行业调研(19 项目)确认:节点级结果缓存业界仅 LangGraph 有第一-class 支持(`add_node(cache_policy=...)`),Dify/deer-flow/AgentScope/openjiuwen/Bedrock AgentCore/Palantir AIP 等均无——落地后 Hecate 与唯一有此能力的引擎持平,领先其余全部。

## What Changes

- 图 DSL 节点新增 `cache` 配置块(`nodes.<id>.config.cache`: `ttl` 必填正整数秒 + `key_func` 可选具名函数),schema / parse / `to_json` roundtrip / 编译期校验(未注册的 `key_func` 名 fail loud,同 reducer 先例;不限节点类型,默认关、作者显式开启)
- 新增 `runtime/node_cache.py`:内存 LRU + 每条目 TTL 的节点结果缓存(复用 5.7 ToolCache 模式,按 runtime 自足性不变式独立实现,不 import `tools/`),`register_node_key_func(name, fn)` 具名注册表
- 默认缓存键 = node id + 节点类型相关要素(CONVERSATION 掺 model 配置哈希,模型升级自动失效)+ 可读通道切片的 canonical JSON;作用域两级:`session`(默认,键含 session_id)+ `tenant`(opt-in,面向只读 KB 检索,键含 tenant_id);`global` 明确排除(v1)
- 引擎 dispatch 缝(`pregel.py`):派发 Invocation 前查缓存,命中即伪造 WorkerResult 跳过 worker 执行;命中与 miss 的日志轨迹逐字一致(NODE_END 富化 `cached: true` + key 哈希,通道写入照常),fold / replay / fork / T2b 零影响(ADR-030:缓存是优化不是权威,可弃)
- 并发惊群:v1 接受重复执行(与全行业一致),取舍写入 design;`single_flight` 留作未来 opt-in 标志
- 顺手修复 `types.py:190` 过时 docstring(`reduce_fn` "currently only 'add' is supported" → 反映 add/append + 注册表现状)
- feature-catalog / roadmap 的 1.3.21 行标注:reducer 半项随 ③ 交付;本变更登记为 1.3.21④

不在范围(v1):provider 前缀/KV 缓存命中的 usage 透传(`cache_read_input_tokens`,独立观测层关注点,挂 design Open Questions);语义缓存(embedding 漂移危害,调研结论不采纳);single-flight 合并;跨 fork 缓存继承(子会话缓存冷启动,安全默认)。

## Capabilities

### New Capabilities

(无——遵循 ① declarative-interrupts 先例:引擎级 DSL 特性落既有 capability 的 delta,不另立新 capability)

### Modified Capabilities

- `graph-dsl`: 节点配置新增 `cache` 块——schema 增加节点级 `cache` 对象(`ttl` 必填正整数、`key_func` 可选字符串名),parse 与 `to_json` roundtrip 支持,编译期校验(未知 `key_func` 名编译报错,类型不限)
- `pregel-runtime`: dispatch 前的节点缓存查询语义——命中伪造 WorkerResult 且日志轨迹与 miss 逐字一致(NODE_END 携带 `cached` 标记),默认缓存键构成(model 配置哈希 + 可读通道切片 canonical JSON),`session`/`tenant` 两级键作用域,内存 LRU + TTL 淘汰,`register_node_key_func` 注册表与编译期校验

## Impact

- `src/hecate/runtime/`:`types.py`(docstring 修复;`NodeConfig.config` 携带 cache 策略)、`graph-dsl.schema.json`、parser(`to_json` roundtrip)、`compiler.py`(校验 + 编译产物携带策略)、`pregel.py`(dispatch 缝)、新增 `node_cache.py`
- 不改外部 API surface(无新端点)、不改 eventstore / fold / `logpolicy.py`(缓存永不入日志)、不改任何 worker
- 测试:runtime 层新增测试文件;受 runtime 自足性守卫约束(`node_cache.py` 禁止 import `tools/`,AST 扫描 + subprocess probe 既有)
- 缓存为进程内存级(per-replica),v1 不引入 Redis/Postgres 后端依赖
