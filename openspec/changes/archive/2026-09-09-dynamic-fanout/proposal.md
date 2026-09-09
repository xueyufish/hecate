# Proposal: 1.3.21③ Send-style Dynamic Fan-out

## Why

Roadmap 1.3.21③(Engine Parity 收官项,M/L)要求:条件边能在运行时返回 N 个派发包(每包携带自己的状态切片),经子通道 + ConflictResolver 合并,泛化今日编译期冻结的静态 FAN_OUT 分支表。静态扇出的分支集合写死在图配置里,"规划器算出 N 个子任务 → 并行收集 → 聚合"的 map-reduce 模式无法表达;Sprint 9 消费方 5.11 Deep Research 并行收集与 map-reduce 聚合模板被直接阻塞。

2026-09 业界调研(17 项系统,框架/企业平台/CLI-SDK 三层,来源已核实)确认:运行时定 N 的动态扇出已是主流能力(LangGraph `Send`、ADK dynamic workflows、Palantir AIP Loop、OpenJiuwen `BranchComponent`、Dify Iteration、AgentScope `AgentCreate` 等),合并策略清一色 reducer、分支状态全部以"切片作为输入"传递、成熟实现均带显式扇出上限。同时 LangGraph issue #6789(Send 对象不可序列化导致与 checkpointer 二选一)是公开记录的反面教材——Hecate 以受日志控制的 channel 承载派发计划,可序列化、可入 WAL、可重放三者兼得,天然避开该坑,且与 ①(声明式中断的计划评审)和 ②(日志推导 continuation)形成闭环:planner 节点上 `interrupt_after` 即 HITL 计划评审,fork/崩溃恢复经 `derive_continuation` 从折叠态重建 N 分支计划。

## What Changes

- **引擎**:派发单元从节点 ID 集合(`_resolve_next_nodes` 去重)升级为**调用列表**(node_id + 分支索引 + 载荷),支持同一节点在同一 superstep 内并行 N 次调用;NODE_START/NODE_END 事件携带分支身份。
- **派发计划通道**:`_dispatch` 作为受日志控制的控制通道(logpolicy 显式豁免,同 `_route` 待遇),承载 `[{"node": ..., "state": ...}, ...]` 派发包;计划入 WAL,崩溃恢复与 fork 经 `derive_continuation` 从折叠态读取(仅当 planner 节点属于最近 STEP_END 锚点的 executed 列表时采纳,防陈旧计划)。
- **分支状态 seed 模式**:每包载荷写入该分支的子通道,分支 worker 读完整 snapshot + 自己那片;延续 `_fanout__` 命名族。
- **动态 MERGE**:MERGE 不再依赖编译期 `branches` 配置,从折叠态中的派发计划发现分支集合;聚合写入支持可插拔 ACCUMULATOR reducer(ride-along,超越内建 `add`)。
- **per-branch 容错**:分支失败处理策略可配置(默认保持 fail-fast,新增 collect 模式——失败分支记录在案,不中断整批)。
- **护栏**:分层扇出上限(节点级 `max_fanout` + superstep 级总量)+ `_dispatch` 目标合法性校验(target 必须是图内节点)。
- **T2b 不变式修复**:「扇出分支输出必须活过 replay/fork」立为 log-as-truth 推论不变式——静态 FAN_OUT 路径的 fork 缝隙(扇出窗口内 FORK 载荷丢失分支输出)一并闭合。
- **显式排除**(调研结论,防范围蔓延):LLM 规划器动态产出 N(方案 B,留 5.11 Deep Research 接线时做);`Command(goto=[packet, ...])` worker 自主派发(方案 C,后置);与 COORDINATOR(1.3.18)的边界在 spec 中显式划线——后者是跨 superstep、隔离子会话的重型编排,③ 限单 superstep 图内并行。

## Capabilities

### New Capabilities

(无——本变更为既有能力的需求变更与扩展。)

### Modified Capabilities

- `fan-out-merge`:新增动态扇出需求——运行时派发包、per-branch 容错策略、分层扇出上限、动态 MERGE 计划发现;与 COORDINATOR 的边界划线。
- `pregel-runtime`:派发单元升级为调用列表;`_dispatch` 计划的日志推导续跑规则(锚点采纳条件);分支状态 seed 语义;NODE 事件分支身份。
- `execution-state-log`:`_dispatch` 控制通道入日志;扇出分支输出可折叠不变式(T2b——含静态路径修复)。
- `graph-dsl`:map-over-channel 声明式编写面(`fanout` 配置:over/target/state_keys);编译期校验(目标节点存在、over 通道存在、上限合法性)。
- `channel-registry`:可插拔 ACCUMULATOR reducer 注册机制(超越内建 `add`)。
- `time-travel-resume`:fork 与 continuation 推导跨越动态扇出窗口——FORK 载荷与续跑集正确携带 N 分支计划与分支输出。

## Impact

- **代码**:`src/hecate/runtime/pregel.py`(派发循环、`_resolve_next_nodes`、`_dispatch_fan_out`/`_execute_merge`、`_append_write_batch`)、`src/hecate/runtime/replay/logpolicy.py`(`_dispatch` 豁免 + 子通道折叠策略)、`src/hecate/runtime/replay/continuation.py`(计划感知锚点规则)、`src/hecate/runtime/channel.py`(reducer 注册)、`src/hecate/runtime/compiler.py`(DSL 校验)、`src/hecate/runtime/graph-dsl.schema.json` + 解析器(`fanout` 配置面)、`src/hecate/runtime/types.py`(派发包/调用列表类型)。
- **API**:无新增公开端点;执行事件流(NODE_START/NODE_END payload 增分支身份)与 UPDATES 流对下游消费者可见,属增量字段。
- **兼容**:静态 FAN_OUT/MERGE 行为保持不变(既有图零迁移);`_dispatch` 为新增保留通道名;日志 schema 沿用 additive 契约(ADR-030 §1),无事件类型新增。
- **测试**:延续 ①② 惯例——引擎单测 + logfold/logpolicy 折叠测试 + fork/time-travel 跨扇出窗口的集成测试;全量套件须保持绿。
