## Context

①②③ 交付后引擎现状:调度单元为 `Invocation`(③,同子节点同 superstep 并行 N 次),superstep 结果收集 → 冲突裁决 → 单事务批量 `append_batch` + `STEP_END` → 通道 apply(ADR-030 WAL 序);声明式中断在 superstep 边界整步暂停(before = dispatch 前,after = 提交后,①);`fold_session` 从日志重建任意历史点(②)。`ChannelManager` 是内存投影,日志是唯一权威;`logpolicy.py` 排除 ephemeral 通道。

5.7 的 `ToolCache`(`tools/tool/cache.py`)提供了模式参照:内存 LRU + 每条目 TTL(monotonic 时钟)、canonical JSON 键、会话作用域。但 runtime 自足性不变式(`runtime/AGENTS.md`,AST 扫描 + subprocess probe 守卫)禁止 `runtime/` 模块级 import `tools/`。

2026-09 行业调研(19 项目,详见 proposal)锚定了三个事实:节点级结果缓存业界仅 LangGraph 有第一-class 实现(`CachePolicy(ttl, key_func)` + `compile(cache=后端)`,命中在流输出挂 `__metadata__: {cached: true}`);没有任何平台为缓存命中伪造模型事件(Bedrock 把缓存用量放 `usage` 块,provider 在既有 usage 结构加计数器);single-flight 在全部图谱平台缺席(仅 durable-execution 系统靠 journal by construction 获得)。

## Goals / Non-Goals

**Goals:**

- 单一 dispatch 缝实现节点结果缓存:命中跳过 worker 执行,日志轨迹与 miss 逐字一致
- 默认关、作者显式 opt-in;不限节点类型;编译期 fail-loud 校验(同 reducer 先例)
- 键构成把"最贵的陈旧输出"类失效风险消掉(CONVERSATION 掺 model 配置哈希)
- `session` / `tenant` 两级作用域,让只读 KB 检索的跨会话收益可兑现
- 缓存可任意丢弃且不影响 fold / replay / fork / T2b(ADR-030 哲学的直接延续)

**Non-Goals:**

- 不做 single-flight / in-flight 合并(v1 接受惊群,决策 D7)
- 不做 provider 前缀/KV 缓存命中(`cache_read_input_tokens`)的逐节点 usage 透传——独立观测层特性,见 Open Questions
- 不做语义缓存(embedding 漂移危害,调研结论不采纳)
- 不做分布式缓存后端(v1 进程内存 per-replica,决策 D9)
- 不做跨 fork 缓存继承(子会话冷启动,安全默认)
- 不改外部 API surface(无新端点;缓存策略经既有图定义承载)

## Decisions

### D1: `runtime/node_cache.py` 独立实现,模式复刻不代码复用

自足性不变式拒绝模块级跨域 import。从 `ToolCache` 复刻:OrderedDict LRU + `CacheEntry(ttl, created_at)`(monotonic 时钟)+ canonical JSON。Alternative:函数级 lazy import `tools.tool.cache`(被守卫容忍但污染分层语义,拒绝);抽出共享 `core/` 缓存库(为两个 ~150 行类引抽象,过度设计,拒绝)。

### D2: 引擎级单一 dispatch 缝,类型无关

缓存查询放在 `pregel.py` 派发 Invocation 处,位于中断处理之后(interrupt_before 的整步暂停在 dispatch 前已发生,故中断决策不受缓存影响;resume 后照常查缓存,interrupt_after 在 WAL 提交后与缓存无交集):

```
 schedule(Invocation)
   → interrupt_before 整步检查(①,先于一切执行)
   → dispatch:
       node 有 cache 策略?
         ├─ 否 → 正常派发 worker
         └─ 是 → key = f(policy, node, input slice)
                  ├─ hit  → 伪造 WorkerResult(cached 值)
                  └─ miss → 派发 worker → 结果回填 NodeCache
       → 结果走既有统一路径:NODE_END + CHANNEL_WRITE + STEP_END(WAL commit)
```

Alternative: per-worker 检查(侵入 llm_worker 等每个 worker,重复实现且遗漏新 worker,拒绝);middleware 层(中间件语义是请求级拦截,不是节点结果 memoization,拒绝)。类型无关 + 作者 opt-in 与 LangGraph 一致;编译期不做类型门控(调研:Temporal 教训是按 effect 划界而非按节点类型,而作者显式 opt-in 本身就是边界)。

### D3: 默认键 = node id + 节点类型身份 + 可读通道切片 canonical JSON

- `session` 域:`(scope, session_id, node_id, identity_hash, slice_hash)`
- `tenant` 域:`(scope, tenant_id, node_id, identity_hash, slice_hash)`(不含 session_id)
- `identity_hash`:CONVERSATION 节点掺 model 配置(model 名 + 采样参数)的哈希——模型升级自动失效,消掉最大的陈旧输出类(LangGraph 默认键不含此,是我们的改进);其他节点类型 v1 为空串,后续按需扩展
- `slice_hash`:节点可读通道切片的 canonical JSON(sort_keys,复用 ToolCache 经验)
- `key_func` 具名注册覆盖默认推导(`register_node_key_func(name, fn)`;DSL 是 JSON 无法内联函数,具名注册是唯一解,与 reducer 注册表同构,未知名编译期报错)

Alternative:全通道状态做键(键宽、命中率低,拒绝——节点本就显式声明可读切片);租户内 key_func 自由表达式(JSON DSL 表达不了,拒绝)。

### D4: 作用域两级 session(默认)/ tenant(opt-in),global 排除

调研收敛结论(AgentCore Memory namespace、Bedrock account/region 级前缀缓存为先例):KB 查询键天然与会话无关,tenant 域是本特性价值主战场(一份缓存服务 N 个会话);global 在多租户语境有跨租户泄露面,v1 排除。作用域是 `cache` 块的 `scope` 字段(`"session" | "tenant"`,默认 `"session"`),仅是键前缀选择,无额外基建。

### D5: 命中不合成模型事件,NODE_END 富标记

命中时 NODE_END 载荷带 `cached: true` + `cache_key`(键哈希);miss 时同样带 `cached: false` + 相同的 `cache_key`(实现取"miss 也带哈希"而非先前的 null 设想——两路都带才能跨 run 关联同一键);结构一致,消费者单分支。键哈希是全键的截断 SHA-256,不内嵌 session/tenant id(它们已在事件自有列上)。**不合成** `LLM_RESPONSE(cached=true)`:审计日志记录实际发生的事,虚构模型调用在多租户审计语境是合规异味,且污染 token 计量。调研证据:LangGraph / Bedrock / Anthropic / OpenAI / Gemini 全部在既有事件或 usage 结构上加标记,无一家伪造模型事件(此前探索倾向 B 方案,被调研证据反转)。fold 只看 `CHANNEL_WRITE`,标记字段对 fold 透明。

### D6: 日志轨迹逐字一致作为验收不变式

命中路径复用 miss 的全部提交路径(仅结果来源不同),测试以 log-trajectory 相等断言钉住:同节点同输入,一次 miss 一次 hit,事件类型序列与通道写入完全一致,仅 NODE_END 的 cached 标记不同。T2b 不变式(fork 载荷保真)与 ② 的投影等价校验由此自动成立,不需新机制。

### D7: v1 接受惊群,`single_flight` 留标志位

全行业图谱平台无一做 single-flight(证据见 Context);引擎内同键合并会改变 ③ 的 Invocation 语义(调度数 ≠ 包数,触及 fanout 上限口径与 per-branch 容错),复杂度真实。v1 文档声明取舍;`CachePolicy` 结构预留 `single_flight` 扩展位(加法式演进,不堵路)。Alternative:v1 即做 per-key 合并(收益真实但语义变更越出 ride-along 体量,拒绝;同键并发重复的成本在 fan-out 场景才有量级,留 5.11 接线时评估)。

### D8: `ttl` 必填,无静默默认

LangGraph 默认 `ttl=None`(永不过期)与 ToolCache 默认 300s 都被拒:前者是 stale 陷阱,后者是隐式行为。fail-closed 文化(同 ③ 的 `FanoutLimitError`、reducer 未知名报错):作者必须显式选择新鲜度边界。schema 层校验正整数。

### D9: 进程内存 per-replica,不引后端抽象

v1 具体类(条目上限取 4096,节点结果普遍大于工具结果,较 ToolCache 的 10000 保守),无 Redis/Postgres 后端、无 `Cache` 协议抽象(YAGNI;多后端等真实需求出现再提取,LangGraph 的 `compile(cache=后端)` 是可追溯的先例路径)。多副本部署下命中率受限于副本亲和——诚实写进 Risks,不以抽象层掩盖。

## Risks / Trade-offs

- [CONVERSATION 节点内部副作用(上下文工程中的工具调用)在命中时被一并跳过] → 文档明确"命中 = 跳过节点全部执行";tool-call 等有副作用节点由作者自判(ttl 取短或不用);v1 不做纯度分析
- [多副本部署缓存命中率低(per-replica)] → 诚实声明;tenant 域在单副本内已兑现主要收益;Redis 后端留 Open Questions,等真实命中率数据
- [可读切片含非确定内容(时间戳等)导致永不命中] → `key_func` 是逃生门;文档提示;TTL 有界使浪费有上界
- [伪造 WorkerResult 与真实结构漂移] → 命中路径构造同一 `WorkerResult` 类型;D6 的轨迹相等测试从结构上钉住漂移
- [节点结果大载荷的内存压力] → LRU 条目上限封顶;不做 offloader(与 ③ 的大切片 offloader 同款延后项,见 Open Questions)
- [tenant 域缓存了日后被个性化修改的节点] → 文档约束 tenant 域面向只读/无个性化解;作用域是作者显式声明,误配可追因到键里的 tenant_id

## Migration Plan

纯增量特性,默认关,无数据迁移、无 API 变更、无 schema 演进(eventstore 不动)。发布后未声明 `cache` 的图行为逐字节不变;回滚 = revert dispatch 缝调用点(默认关已保证风险下限)。feature-catalog / roadmap 的 1.3.21 行标注(reducer 半项随 ③ 交付、本变更登记为 1.3.21④)随本变更落地,`/opsx-archive` 时按惯例同步 `docs/design/positioning.md`。

## Open Questions

- provider 前缀/KV 缓存命中的逐节点 usage 透传(`cache_read_input_tokens`,Anthropic 命名):**已定(2026-09-09)——不独立立项,消费者拉动**。方案本身成立(真实 usage 盲区是真的),但今天无计费级消费方(核实:`RuntimePort.llm_invoke` 仅 yield token 字符串,`LLM_RESPONSE` 载荷仅 `{model, response_length}`,全库无 `cache_read_input_tokens`/`cached_tokens` 消费;估算数字仅用于 OTel span 与 context 选择,后者用估算是合理的)。触发器:P4 成本类能力(cost-dashboard / model-cost-management)立项,或首个租户计费/成本复盘需求,以先到者为准;届时透传作为该变更的**第一个里程碑**(先通 port→事件管道,契约形状由该消费场景定义),不单独立项。触发器不出现则不立,无损失(盲区无消费方,不造成可测量伤害)
- 分布式缓存后端(Redis/Postgres,跨副本共享):等 per-replica 命中率真实数据再评估
- `single_flight` opt-in 的引擎语义(与 fanout 上限、per-branch 容错的口径):与 5.11 Deep Research 接线时一并评估
- 大载荷 offloader 集成(同 ③ 延后项):待真实载荷分布
- 子会话 / fork 的缓存继承策略(当前冷启动):等 6.26 E5 what-if 分支的真实使用模式
