# Design: 1.3.21① Declarative Interrupts

## Context

现状(源码核实):`PregelRuntime` 的 worker-authored interrupt 链路完整(`pregel.py:413-459`:apply writes → checkpoint → yield → TURN_END),resume 链路完整(`_restore_from_checkpoint` 缓存 + tail fold + `_resume_value` 注入,`_resolve_next_nodes_after_interrupt` 沿被中断节点出边),但存在三处断点:(a) 无生产代码触发 interrupt;(b) worker-interrupt 路径在 WAL batch-append(`pregel.py:465-499`)之前 `break`,中断节点的写入进了缓存却没进日志——与 execution-state-log 的提交点规则(`STEP_END`/`INTERRUPT` 为提交点,未达提交点的 `CHANNEL_WRITE` 对恢复视为未发生)相悖,resume 的 projection-equivalence 检查在写入可日志化通道时理论上发散;(c) `SessionModel.status = "interrupted"` 无写入者,而 1.3.19 spec(execution-state-log"SessionModel.status 仅由真实事件驱动变更")已声明该要求,resume 端点第二道门因此不可通过。

约束:恢复元数据 SHALL 由日志推导(1.3.19,不依赖缓存 metadata);T0.5 TURN_START/TURN_END 审计配对;task mode 无 checkpoint 且不允许交互;`CompiledGraph.to_json()` 被 `studio/workflows/service.py` 用于持久化图定义。

业界对标(2026-09-08 调研,详见下文各决策引用):LangGraph 静态 `interrupt_before/after`、Google ADK `RequestInput` 的 `rerunOnResume`(false=回复进后继/true=节点重跑,即 before/after 二元)、Dify `HumanInputNode`(按钮即路由、超时分支)、MS Agent Framework v1.13 入口 checkpoint、openclaw"审批等待不烧预算"。

## Goals / Non-Goals

**Goals:**

- DSL 顶层数组 → 引擎暂停点 → 日志 → resume 端点 → session status 的完整闭环,不写自定义 worker 即可构建 HITL 流程
- 声明式与 worker-authored 两条中断路径在日志与 resume 语义上同构(同一 `EventType.INTERRUPT`、同一恢复推导)
- worker-authored 路径的 WAL 提交序向已声明规则收敛(修 (b))

**Non-Goals**(proposal"明确不做"之外的设计级边界):

- 不改 `Command`/`WorkerResult` 类型(worker 不感知声明式机制)
- 不引入新的 `EventType`(复用 `INTERRUPT`/`RESUME`);payload 字段演进留给 11.18 stream modes
- 不做多中断并发(一个 superstep 至多一个暂停点;LangGraph `Command(resume=dict)` 多中断映射不在本期)
- 不改 resume 端点与 `CheckpointStore` ABC 契约

## Decisions

### D1: DSL 形态 = 顶层列表,非 per-node config

`interrupt_before` / `interrupt_after` 作为 DSL 文档顶层可选数组(`graph-dsl.schema.json` 顶层为 `additionalProperties: false`,新增两个可选键,存量文档零影响),经 `parse_graph()` → `GraphConfig` → `CompiledGraph` 传递。

理由:LangGraph `graph.compile(interrupt_before=...)` 的形状,roadmap 原文即"compile-time node lists";per-node config 会把同一关注点散到 N 个节点定义里。备选(拒绝):节点 config 内 `interrupt: "before"`——对单节点编辑友好但聚合视图(哪些节点会暂停)要全图扫描,校验错误也更分散。

### D2: 暂停点位置 = 整步语义,插在既有提交边界上

- **before**:superstep 计数递增、`scheduler.select_next()` 之后、dispatch 之前——`scheduled ∩ interrupt_before ≠ ∅` 则整步不执行。checkpoint 载荷 = 当前通道状态(初始输入或上一步结果),`node_id` 记触发集中的第一个节点。
- **after**:现有"WAL batch-append → apply writes"(`pregel.py:465-502`)之后、常规 checkpoint 处——`executed ∩ interrupt_after ≠ ∅` 则用带描述符的 INTERRUPT 事件替代(并包含)该步常规检查点语义。即:**顺序天然正确**,声明式 after 路径不需要新的排序逻辑。

理由:LangGraph 语义(任一节点命中则整步暂停);MS AF "checkpoint at end of each superstep" 同型。多节点并发时部分执行会让"暂停点"落在步中间,与 BSP 模型和提交点规则都冲突——整步是唯一自洽选择。备选(拒绝):仅暂停命中的节点、其余照跑——产生"半步"状态,checkpoint 与日志都无法干净表达。

### D3: phase 与 resume 节点集由日志推导,payload 即推导源

`INTERRUPT` 事件 payload(声明式)为结构化描述符:

```
{
  "kind": "declarative",
  "phase": "before" | "after",
  "nodes": [...],          # before=被暂停步的 scheduled 集;after=中断步的 executed 集
  "interrupt_id": "<session_id>:<uuid4>",
  "superstep": N,
  "remaining_steps": M
}
```

resume 时从日志**最后一个未闭合 `INTERRUPT` 事件**读 `phase`/`nodes`:`before` → 直接执行 `nodes`(被暂停节点本身);`after` → 对 `nodes` 求出边并集(复用现有 `_resolve_conditional_target` + 各节点 `CHANNEL_WRITE` 中的 `_route`)。缓存 metadata 不作为推导源(1.3.19 约束),但缓存照常保存(快速恢复路径)。

理由:ADK `rerunOnResume` 证实 before/after 二元是业界收敛解;`interrupt_id`(稳定前缀+UUID,ADK 模式)为 11.18 调试流与未来多中断预留,本期仅生成与透传。备选(拒绝):phase 存 checkpoint metadata——违反"SHALL NOT 依赖缓存 metadata",且缓存可丢弃。

`_resolve_next_nodes_after_interrupt` 的实现形态:按 payload `kind` 分支——`kind="declarative"` 走上述推导;否则(缺省=worker-authored)保持现有单节点出边逻辑,单节点场景行为不变(向后兼容)。多节点 worker-interrupt 现状(`break` 丢弃同步其余节点的 pending writes)由 D5 的提交序修复一并消除:修复后 worker-interrupt 也先 append 全步 writes 再以 INTERRUPT 收尾,其余节点的写入不再丢失。

**实现期补充(2026-09-08)**:(a) **resume 一次性豁免**——before 暂停的 resume 会立刻再次命中同节点的 before 检查(断点自咬死锁);引入 `_resume_skip_before` 一次性集合:被暂停 superstep 的节点在恢复后的首个 superstep 不再触发自身暂停,之后再经过(环路回访)照常触发;after-resume 指向的新 before 节点不受豁免影响。(b) **控制通道需在 state 声明**——`ChannelManager.write` 对未注册通道静默跳过,因此 after-resume 的条件路由要求 `_route`(及 `_resume_value` 的消费)在图 state 中声明为通道;这与 log-as-truth 方向一致(`_route` 本就设计为可日志化、参与 fold 正确性)。(c) after 描述符的 `nodes` 为产生 WorkerResult 的节点集(不含 FAN_OUT 结构节点),避免 resume 沿 fan-out 出边重复派发分支。

### D4: remaining_steps 只暴露、不消费

`_execution_context()`(`pregel.py:152`)增加 `"remaining_steps": self._max_supersteps - self._superstep`。本期无 in-repo 消费者(coordinator_worker 有自己的 budgets);验收用 stub worker 断言。checkpoint 恢复后 superstep 计数器来自缓存/日志,人类等待时间不消耗步数(openclaw"审批等待不烧预算"语义,由现有恢复机制天然满足,写测试钉住)。

### D5: worker-authored 路径 WAL 提交序修复(纳入本期)

改动:results 循环收集到 `Command(interrupt)` 时不再立即 `break`,而是把**本步全部 results** 的 pending writes 走既有 WAL batch-append 路径(以 `INTERRUPT` 事件本身作为本步提交点收尾,替代 `STEP_END`),再 apply writes、存缓存、yield。效果:缓存与日志严格一致,projection-equivalence 在 interrupt/resume 往返后成立。

理由:这是把实现对齐到已声明的提交点规则(修复 (b)),不是新行为;与 D2 的 after 路径共享同一套"append→apply→checkpoint"顺序,两条路径收敛为一个实现。备选(拒绝):只修声明式路径、worker 路径另行 fix——同文件同函数的两条分支留一条不一致,审查与测试都更贵。

### D6: task mode 拒绝放在编译期,复用 `_validate_execution_mode`

`_validate_execution_mode`(`compiler.py:312`)的 forbidden 集合从 `{SUGGESTION}` 扩展为:非空 `interrupt_before`/`interrupt_after` 列表亦拒绝。顺带修正 graph-dsl spec 中引用不存在的 INTERRUPT 节点类型的陈旧场景(delta 已将其改写为 interrupt 列表场景)。运行时不重复校验(task mode 本就无 checkpoint,暂停点无从生效;fail-fast 在编译期)。

### D7: session status 接线 = 执行服务事件观察,不新增日志 watcher

`execution_service` 的 `_non_stream_execute` / `_stream_execute` 消费循环中,观察到 `{"type": "interrupt"}` → 将 `SessionModel.status` 置 `"interrupted"`(按 1.3.19"仅由真实事件驱动变更":以 yield 的 interrupt 事件为触发,与日志中 `INTERRUPT` 事件一一对应);resume → `active` 由 resume 端点既有逻辑完成。引擎日志不可用(task mode / event_store 未接线)时 status 不变更(task mode 不允许中断列表,实际不会发生)。

理由:最小接线面——执行服务是 session 生命周期的既有 owner,事件观察是它已有的消费形态(今天只是丢弃 interrupt 事件)。备选(拒绝):独立 log-watcher / materializer 挂钩——引入新组件与轮询/订阅复杂度,收益仅在"无执行服务参与的会话"场景,当前不存在该场景。

### D8: to_json roundtrip 为一等要求

`CompiledGraph.to_json()` 增加两个键;`parse_graph()` 接受它们。缺一个方向,`studio/workflows/service.py:84/194` 持久化的图定义就会在重新加载后丢失中断配置——roundtrip 测试钉住。

### D9: 暂停无超时(显式决定)

声明式暂停持续到 resume 或会话终结,不设默认超时。Dify 的"超时分支"模式(到期走显式连接的 fallback 边)记为演进方向——它要求 DSL 支持"超时出边"这一新边语义,超出 S 范围。会话级 GC/retention(既有机制)是兜底。

### D10: worker-authored 中断事件同样自描述 kind/nodes(原 Open Question ①,已关闭)

新产生的 worker-authored `INTERRUPT` 事件在既有 `interrupt_value_type` 键旁**并列**追加 `kind="worker"` 与 `nodes=[interrupted_node]`——不包装业务 payload、不改既有键。收益:日志流自描述,resume 推导与 11.18 UI 渲染统一按 `kind` 分流,无 payload 形状嗅探。resume 推导的缺省分支(无 `kind` 视为 worker-authored)**保留**以兼容存量会话的旧日志事件——该分支无论本决定如何都不可删,故本决定零额外风险。

### D11: 描述符字段形状 = 平铺、无前缀、只增不改(原 Open Question ②,已关闭)

`kind`/`phase`/`nodes`/`interrupt_id`/`superstep`/`remaining_steps` 即为规范形状:平铺键,不加 `interrupt_` 前缀;后续演进(11.18 stream modes 等)只允许新增键,禁止重命名或复用既有键承载新语义。11.18 因此无需任何迁移。

## Risks / Trade-offs

- [worker-interrupt 提交序变化改变既有测试基线] → 既有测试断言的是旧行为(写入不进日志);更新为提交点语义断言,并新增 projection-equivalence 往返测试。方向是向已声明 spec 收敛,非破坏性产品行为。
- [`_resolve_next_nodes_after_interrupt` 泛化引入回归] → 保留缺省分支(worker-authored 单节点出边逻辑逐字保留),声明式分支独立测试;多节点 after 求并集有专测。
- [before 暂停的 superstep 计数偏移] → 暂停发生在计数递增后,resume 恢复计数再递增,一步"空转"仅影响 `max_supersteps` 的感知预算一位,无正确性影响;测试钉住 remaining_steps 语义即可。
- [描述符 payload 与 worker-authored payload 混存于日志] → `kind` 字段缺省视为 worker-authored;resume 推导只认 `kind="declarative"`。日志消费者(resume 端点)只看事件类型,不解析 payload,零影响。
- [执行服务接线遗漏 channel/IM 会话路径] → 本期只接 studio 执行路径(会话主链路);IM 路径若跑中断图,status 翻转缺失导致 resume 被拒——记录为已知限制,接线点收敛在执行服务后,IM 复用同一服务时自动获得。

## Migration Plan

纯增量,无迁移:DSL 新字段可选且缺省空;日志 payload 新键(声明式描述符、worker 事件的 `kind`/`nodes`)只被新 resume 推导读取,旧日志事件走缺省分支;无 DB schema 变更、无配置项。回滚 = revert(旧代码忽略未知 DSL 键,persisted 图定义中的列表对旧代码不可见但无害)。

## Open Questions

(无——原两条已在评审时关闭,决议记为 D10/D11。)
