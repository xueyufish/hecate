# 1.3.21① Declarative Interrupts

## Why

引擎的中断机制是**完整但孤儿**的:`Command(interrupt=...)` 的处理、checkpoint、resume、`_resume_value` 通道全链路存在且被测试覆盖,但全仓库没有任何生产代码触发它——没有 worker 返回过 interrupt,执行服务消费事件流时丢弃 `{"type": "interrupt"}`,`SessionModel.status = "interrupted"` 没有写入者(resume 端点的第二道门因此对真实会话必然 400)。用户今天想构建"审批后继续"的 HITL 流程,唯一途径是写自定义 Python worker。

Roadmap 1.3.21①(Engine Parity 首项,S)要求:compile-time `interrupt_before` / `interrupt_after` 节点列表 + `execution_context` 暴露 `remaining_steps`(graceful degradation before `MaxSuperstepsError`)。2026-09-08 的业界调研(19 个项目 + 框架扫描)确认:superstep/turn 边界是业界收敛的暂停与持久化单元;LangGraph(静态 `interrupt_before`/`interrupt_after`)、Dify(`HumanInputNode`)、IBM watsonx(User Activity 节点)、Google ADK 2.0(`RequestInput` + `rerunOnResume`)均有声明式图级 HITL 先例——本项做完是与领先者对齐,差异化在于 Hecate 的 log-as-truth fold resume。Sprint 9 消费者(6.26 E5 what-if 分支、8.20 可执行回放、11.18/5.11 fan-out)依赖 ① 先行。

## What Changes

- **DSL 声明式中断列表**:Graph DSL 顶层新增可选 `interrupt_before` / `interrupt_after` 节点 ID 数组(schema、parser、`GraphConfig`/`CompiledGraph` 字段、`to_json()` roundtrip——`studio/workflows/service.py` 持久化图定义依赖 roundtrip,漏掉会丢配置)
- **编译期校验**:列表引用的 node ID 必须存在(`GraphValidationError`);`execution_mode="task"` 拒绝携带中断列表的图(扩展现有 `_validate_execution_mode`——task mode 无 checkpoint、不允许交互)
- **引擎两个暂停点**:`interrupt_before` 在本步任何 scheduled 节点命中时**整步暂停于 dispatch 之前**(无节点执行);`interrupt_after` 在本步全部写入 WAL append + 应用之后暂停。暂停时 emit `INTERRUPT` 事件 + yield `{"type": "interrupt", ...}` + 保存物化缓存
- **phase-aware resume**:resume 时由日志推导 phase(遵循 1.3.19"恢复元数据由日志推导,SHALL NOT 依赖缓存 metadata")——`before` → 执行被暂停节点本身;`after` → 沿中断 superstep 全部执行节点的出边继续。声明式中断 payload 为结构化描述符(`kind`/`phase`/`node`/`interrupt_id`/`superstep`/`remaining_steps`),worker-authored 路径 payload 保持不变
- **`remaining_steps` 暴露**:`execution_context` 新增 `remaining_steps = max_supersteps - superstep`,worker 可读(本项只暴露不消费)
- **WAL 提交序修复**:worker-authored interrupt 的 channel writes SHALL 先批量 append `CHANNEL_WRITE` 再以 `INTERRUPT` 事件为提交点——消除现有"cache 领先于日志"的偏差(撕裂尾部规则下,未达提交点的写入对恢复视为未发生,但当前实现把这部分写入固化进了缓存)
- **Session status 事件驱动接线**(实现 1.3.19 已声明但未实现的要求):执行服务观察到 `INTERRUPT` 事件 → `SessionModel.status = "interrupted"`;resume → `active`(resume 端点已做)。HITL 链路由此第一次端到端可达

**明确不做**(防范围蔓延,proposal→tasks 全程携带):超时分支(Dify 模式——暂停无超时,等到 resume 或会话终结;记录为演进方向);结构化 `{value, route}` resume(Dify 按钮即路由——保持 `_resume_value` 单通道,路由交给 CONDITION 节点表达);`response_schema` 类型化回复(MCP elicitation 对齐,后续演进);工具级审批轴(属 tool_gate/guardrail 既有职责,Google ADK 两轴模型的印证);运行时调试断点注入(G7 / 1.1.23,届时加构造参数);worker 对 `remaining_steps` 的消费逻辑(如 prompt 注入)。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `pregel-runtime`:新增声明式中断需求(两个暂停点、整步语义、phase-aware resume、日志推导 phase、描述符 payload)+ 新增 `remaining_steps` 上下文暴露需求 + 修改既有"Interrupt/resume via checkpoint"需求(worker-authored 路径的 WAL 提交序场景)
- `graph-dsl`:新增声明式中断列表需求(顶层字段、schema、parser、`to_json` roundtrip)+ 修改既有编译校验需求(中断列表 node 存在性校验、task mode 拒绝;顺带修正该需求下引用了不存在的 INTERRUPT 节点类型的陈旧场景)

## Impact

- **修改**:`src/hecate/runtime/graph-dsl.schema.json`(顶层两个可选数组)、`src/hecate/studio/workflows/graph_dsl.py`(parse)、`src/hecate/runtime/types.py`(`GraphConfig`/`CompiledGraph` 字段 + `to_json`)、`src/hecate/runtime/compiler.py`(校验)、`src/hecate/runtime/pregel.py`(暂停点、phase-aware resume、`remaining_steps`、WAL 序)、`src/hecate/studio/workflows/execution_service.py`(interrupt 事件观察 + session status 接线)
- **不修改**:`studio/api/sessions.py`(resume 端点日志门天然兼容——声明式中断 emit 同一 `INTERRUPT` 事件);`eventstore.py`(复用既有 `EventType.INTERRUPT`/`RESUME`)
- **依赖**:无新第三方包、无 DB 迁移、无配置项
- **测试**:`tests/test_runtime/test_pregel.py`(before/after 暂停 + resume、remaining_steps、WAL 序)、graph DSL/compiler 测试(字段解析、校验、roundtrip、task mode 拒绝)、`test_log_as_truth_integration.py`(声明式中断的日志一致性与 projection 检查)、`test_api/test_resume_endpoint.py`(端到端:声明式中断 → resume 200)
- **风险**:worker-authored 路径 WAL 先行是行为变化(既有测试基线需更新,方向是向已声明的提交点规则收敛);schema 顶层 `additionalProperties: false` 下新增可选字段对存量 DSL 文档零影响;`_resolve_next_nodes_after_interrupt` 从单节点泛化到节点集合,需保持向后兼容(单节点场景行为不变)
