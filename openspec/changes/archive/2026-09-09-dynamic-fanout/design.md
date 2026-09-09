# Design: 1.3.21③ Send-style Dynamic Fan-out

## Context

引擎现状(`src/hecate/runtime/`):静态 FAN_OUT 以编译期 `branches` 列表派发(`pregel.py::_dispatch_fan_out`,分支共享同一 snapshot,子通道 `_fanout__{src}__{branch}`);条件边经 `_route` 解析出**单个**目标(`_resolve_next_nodes`,尾部 `dict.fromkeys` 去重);MERGE 读固定子通道名聚合。日志侧:LogPolicy 排除 `_fanout__*`(ephemeral)与 `_` 前缀控制通道,`_route` 显式豁免(`logpolicy.py`);提交点为 `STEP_END`/`INTERRUPT`/`FORK`(`continuation.py::_COMMIT_POINT_TYPES`)。既有缺陷(T2b):扇出窗口内 log-only 恢复与 FORK 载荷均丢失分支子通道值。

动机与业界对比见 proposal.md;行为契约见各 delta spec。本文件记录机制选型。

## Goals / Non-Goals

**Goals**
- 运行时定 N 的单 superstep 图内扇出,计划与分支输出全程日志保真(replay/fork/崩溃恢复)。
- 静态 FAN_OUT/MERGE 行为零迁移;日志 schema 沿用 additive 契约(ADR-030 §1),不新增事件类型。
- ①(计划评审中断)与 ②(日志推导续跑)在扇出窗口上的语义闭环。

**Non-Goals**
- LLM 规划器产出派发包(留 5.11);`Command(goto=[...])` worker 自主派发(后置)。
- 跨 superstep / 子会话级动态编排(属 COORDINATOR 1.3.18)。
- 跨 worker 进程/分布式扇出(Temporal 式);单进程 asyncio.gather 语义保持。

## Decisions

### D1 派发计划承载:`_dispatch` 受日志控制的通道(而非新事件类型)

计划写入 planner 节点 result 的 `channel_updates["_dispatch"]`,随既有 `_append_write_batch` 入 WAL(`CHANNEL_WRITE` 事件)。LogPolicy 对 `_dispatch` 显式豁免(同 `_route` 待遇)。

- **备选 A:新增 `FANOUT` 事件类型并入 `_COMMIT_POINT_TYPES`**——拒绝:计划天然是"通道状态的一部分"(fold 后可读、update_state 可改、`_route` 先例完整),独立事件反而要回答"事件与通道状态谁是真相";且 ② 的 `derive_continuation` 已接收 `channel_state`,采纳规则可纯状态表达,零新提交点类别。
- **备选 B:`Command(goto=[Send...])`**——拒绝:命令式、绕开 DSL、v1 无消费者(见 Non-Goals)。
- **采纳规则(防陈旧计划)**:`derive_continuation` 仅当最后采纳的 `STEP_END` 锚点的 executed 列表含 `_dispatch` 写入者时,从折叠态 `_dispatch` 取续跑集;否则按既有静态出边规则。执行路径同构:`_resolve_next_nodes` 对带 `_dispatch` 的 result 取计划目标(优先级:`Command(goto)` > `_dispatch` > `_route`/静态边,与现行 goto 优先一致;goto 与 `_dispatch` 并存时以 goto 为准并告警)。
- 序列化天然成立(计划是纯 JSON dict)——显式规避 LangGraph issue #6789(Send 对象不可序列化 → 与 checkpointer 二选一)。

### D2 派发单元:调用列表(Invocation),静态路径内部统一包装

新增 `Invocation` 数据(目标节点 + 调用身份 `{fanout_source, branch_index}` + 子通道名)与 `DispatchPacket`(`{"node", "state"}`)。`_resolve_next_nodes` 返回 `list[Invocation]`:静态/`_route`/goto 路径解析为无载荷 invocation(行为逐字不变),`_dispatch` 路径每包一个 invocation。**不做节点 ID 去重**——map 场景同节点 N 次调用是核心需求(OpenJiuwen `target: list[str]` 同款先例)。扇出调用的 NODE_START/NODE_END payload 增量附加 `fanout_source`/`branch_index`(向后兼容字段)。

子通道命名:动态为 `_fanout__{src}__idx{i}`,静态保持 `_fanout__{src}__{branch_id}`——同前缀族,LogPolicy 的 `_is_fanout_subchannel` 前缀匹配无需分叉。

### D3 分支状态:seed 模式(而非 snapshot overlay)

每包 `state` 在派发前写入该调用专属子通道,分支 worker 收到「派发时全量 snapshot」+ 经子通道读自己的切片。

- **备选:overlay(分支 snapshot 为 `{**base, **slice}`,LangGraph 原味)**——拒绝:Hecate worker 契约是"读 snapshot 写 channel_updates",overlay 改变"snapshot"在并行调用间的含义,且切片键与全局通道名冲突时语义不明。业界五家(Dify/OpenJiuwen/ADK/Palantir/DeerFlow)全是"切片作为输入"形态,无自动命名空间先例。seed 对既有 worker 零破坏。

### D4 T2b 保真机制:扇出 superstep 的 STEP_END commit payload 携带分支子通道快照

既有缺陷:子通道不入日常日志(合理——分支对 loggable 通道的写入已随 `pending_writes` 记账,子通道是聚合视图的二次副本),但 MERGE/fork 需要它。

**选定**:扇出 superstep 的 `STEP_END` commit_payload 增加 `fanout` 段(源节点 + 调用身份 + 各子通道值);fold 遇到该段时重建子通道。无扇出的 superstep 零开销。先例:② 的 FORK 载荷 fold、D11 的 initial_input 修复——"结构中间态对 fold 保真,经提交点载荷而非日常通道记账"。静态与动态路径统一走此机制。

- **备选 A:LogPolicy 对 `_fanout__*` 全面解禁**——拒绝:日志膨胀(与 pending_writes 记账重复),且"worker 误写 `_fanout__` 前缀"这类边界语义变浊。
- **备选 B:MERGE 改从分支 channel_updates 的日志记录聚合**——拒绝:破坏 `{branch_id: result}` 既有契约,且子通道 dict 含 non-loggable 通道键,日志中本就不全。
- 效果:`snapshot_at_version` 经 fold 自然含子通道值 → FORK 载荷保真;缓存缺失的 log-only 恢复后 MERGE 正确。投影等价断言范围不变(子通道仍非 `should_log_channel`,断言不比对它们——保真由 fold 重建路径保证,以 T2b 专项测试锁定)。

### D5 per-branch 容错:`on_branch_error: fail_fast | collect`

默认 `fail_fast` 保持现状(`gather` 后遇 error 即 raise)。`collect`:分支异常被子通道捕获,子通道值记为 `{"__branch_error__": {"type", "message"}}`,其余分支照常;MERGE 聚合保留错误条目,下游自行裁决。配置入口:`fanout.on_branch_error`(动态)与 FAN_OUT 节点 config(静态,增量字段)。Pi 的 per-task 记录是先例;不引入部分重试(留后续)。

### D6 上限:引擎默认 + 图/节点覆盖 + 绝对钳制

引擎默认 `max_fanout_per_dispatch=64`、`max_invocations_per_superstep=256`;图 state 配置与节点 `fanout.max_fanout` 可覆盖;超平台绝对边界(1024)一律钳到边界。超限抛 `FanoutLimitError`(沿用 `MaxSuperstepsError` 风格,fail closed 不截断)。分层上限取 OpenClaw/DeerFlow 阵营,不取 LangGraph 的"无上限"。与平台配额层的联动留 Open Questions。

### D7 可插拔 reducer:模块级具名注册表 + 编译期校验

`channel.py` 增 `_REDUCERS` 注册表与 `register_reducer(name, fn)`;内建 `"add"`(行为不变)与 `"append"`(列表追加)。`AccumulatorBehavior.write` 按 `defn.reduce_fn` 查表;**未注册的具名** reduce_fn 在编译期(compiler 校验 state 声明)与 `ChannelManager.register` 双点报错,移除"未知具名 reducer 静默 overwrite"回退;`reduce_fn=None` 保持既有 overwrite 语义(既有测试锁定,不受影响)。兼容风险:仅当既有图使用了未注册的具名 reduce_fn 才受影响——已全库核对(`grep reduce_fn`):生产代码仅 `"add"`/`None`,`studio/workflows/patterns.py:252` 为模式识别启发式字符串判断、不依赖注册,预计零存量。

### D8 中断叠加语义

planner ∈ `interrupt_after`:STEP_END(计划已记账)提交后、派发前暂停——`after_hits` 判定先于 `_resolve_next_nodes` 派发消费,描述符 `nodes=[planner]`;恢复路径天然走 D1 采纳规则(最后锚点 executed 含 planner → 取计划)。扇出目标 ∈ `interrupt_before`:该批调用派发前整批暂停一次(① 的一次性豁免沿用),描述符 `nodes` 列目标节点。

## Risks / Trade-offs

- [计划/切片大载荷入日志(JSONB 物理上限)] → 上限封顶单计划规模;文档声明大切片应走 offloader(既有 `offloader.py`);② 对 FORK 载荷的同款已知状况,不新增敞口。
- [同 superstep 多个 planner 并存] → 派发各自生效;恢复推导按 executed 序取计划写入者。编译期不禁止、文档标注"多 planner 恢复语义按日志序",v1 不做跨 planner 合并。
- [collect 模式错误条目污染下游聚合] → 错误条目带 `__branch_error__` 显式标记,MERGE 原样保留;模板层(map-reduce 模板)负责示范过滤。
- [移除未知 reduce_fn 静默回退属行为变更] → tasks 首项全库核对;若有存量用法,先迁移再收紧(预计为零)。
- [动态调用身份进入 NODE 事件,流式消费者需感知新字段] → 纯增量字段,静态路径不带;11.18 调试流与 G7 消费侧后续自然受益。

## Migration Plan

纯 additive:无 schema 迁移、无事件类型变更、静态图零改动。部署即生效;回滚 = revert(日志中已存在的 `fanout` commit payload 段被旧版 fold 忽略——fold 对未知 payload 键宽容,天然向前兼容)。日志 schema version 不变(payload 形状增量,marker 机制不受影响)。

## Open Questions

- 平台配额层与引擎 `FanoutLimitError` 的联动口径(② 遗留的 fork 配额同款问题,留平台配额统一设计)。
- 大切片的 offloader 集成是否 v1 做(倾向:v1 仅文档声明 + 上限封顶,offloader 接入待真实载荷分布)。
- `fanout` 配置 v1 限 CONDITION;FAN_OUT 节点动态模式(`mode: dynamic`)是否开放,待 5.11 接线时按需评估。
