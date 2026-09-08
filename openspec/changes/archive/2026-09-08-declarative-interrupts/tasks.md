# Tasks: 1.3.21① Declarative Interrupts

## 1. DSL 与类型层（D1/D8）

- [x] 1.1 `src/hecate/runtime/graph-dsl.schema.json` 顶层新增可选 `interrupt_before` / `interrupt_after`（string 数组，缺省空）
- [x] 1.2 `runtime/types.py`：`GraphConfig` 与 `CompiledGraph` 新增 `interrupt_before` / `interrupt_after` 字段（`list[str]`，缺省空列表）；`studio/workflows/graph_dsl.py` `parse_graph()` 解析并传播两列表到编译产物
- [x] 1.3 `CompiledGraph.to_json()` 序列化两列表；单测：parse → compile → to_json → re-parse roundtrip 不丢失，缺省文档两列表为空

## 2. 编译期校验（D6）

- [x] 2.1 `runtime/compiler.py` 新增中断列表校验：列表引用的 node ID 不存在 → `GraphValidationError`（field 指向 `interrupt_before`/`interrupt_after` 路径）
- [x] 2.2 `_validate_execution_mode` 扩展：`execution_mode="task"` 且任一列表非空 → `GraphValidationError`（消息表明 declarative interrupts are forbidden in task mode）
- [x] 2.3 单测矩阵：不存在 node 拒绝 / task mode 非空列表拒绝 / task mode 空列表放行 / conversational 携带列表编译成功

## 3. execution_context 暴露 remaining_steps（D4）

- [x] 3.1 `runtime/pregel.py` `_execution_context()` 新增 `remaining_steps = self._max_supersteps - self._superstep`
- [x] 3.2 单测：stub worker 断言 superstep N 时 `remaining_steps == max_supersteps - N`；interrupt → resume 后首轮的 `remaining_steps` 基于恢复的计数器（人类等待不消耗步数）

## 4. worker-authored 中断 WAL 提交序修复（D5）

- [x] 4.1 重构 `pregel.py` results 处理：收集到 `Command(interrupt)` 时不再立即 break——本步全部 pending writes 先走既有 WAL batch-append（`should_log_channel` 过滤），以 `INTERRUPT` 事件作为本步提交点收尾（替代 `STEP_END`），再 apply writes、保存物化缓存、yield interrupt、TURN_END
- [x] 4.2 更新受影响的既有测试基线（`test_pregel.py` / `test_log_as_truth_integration.py` / `test_execution_identity.py`：中断步写入现应出现在日志中）；新增往返一致性测试：worker interrupt 携带可日志化 channel writes → resume → projection-equivalence 检查通过（缓存与日志 fold 一致）

## 5. 引擎声明式暂停点（D2/D3）

- [x] 5.1 before 检查：superstep 递增 + `scheduler.select_next()` 之后、dispatch 之前，`scheduled ∩ interrupt_before ≠ ∅` → 整步不执行，构造描述符（`phase="before"`、`nodes`=scheduled 集）走与 worker-interrupt 相同的 append/apply-less/checkpoint/yield/TURN_END 收尾路径（before 暂停无本步写入，无需 WAL batch）
- [x] 5.2 after 检查：既有"WAL batch-append → apply writes"之后，`executed ∩ interrupt_after ≠ ∅` → 构造描述符（`phase="after"`、`nodes`=executed 集）append `INTERRUPT` 事件、保存带描述符的检查点、yield、TURN_END（替代该步常规 SUPERSTEP_END 收尾）
- [x] 5.3 描述符构造辅助：声明式路径产出 `kind="declarative"`、`phase`、`nodes`、`interrupt_id=f"{session_id}:{uuid4}"`、`superstep`、`remaining_steps`；worker-authored 路径 payload 业务值不变，并列追加 `kind="worker"` + `nodes=[interrupted_node]`（D10，与既有 `interrupt_value_type` 键共存）
- [x] 5.4 phase-aware resume：恢复时从日志最后一个未闭合 `INTERRUPT` 事件 payload 读 `kind`/`phase`/`nodes`——`kind="declarative"` 且 `phase="before"` → 执行 `nodes` 本身；`phase="after"` → 对 `nodes` 求出边并集（复用 `_resolve_conditional_target` + `_route`）；payload 无 `kind`（worker-authored）→ 保持现有单节点出边逻辑逐字不变
- [x] 5.5 单测（spec 场景逐条）：before 整步暂停且 resume 执行被暂停节点并读 `_resume_value`；after 在写入落盘后暂停；多节点 superstep 单命中整步暂停、resume 出边并集；entry 节点 before 暂停锚定初始状态；含 FAN_OUT/MERGE 图的中断往返；TURN_END 配对（`reason="interrupt"`）；声明式与 worker 中断在同一日志流中混存时 resume 推导正确分流

## 6. Session status 事件接线（D7）

- [x] 6.1 `studio/workflows/execution_service.py` `_non_stream_execute` / `_stream_execute` 消费循环：观察到 `{"type": "interrupt"}` → `SessionModel.status = "interrupted"`（仅此一处新增写路径，resume → active 已由 resume 端点承担）
- [x] 6.2 端到端测试：声明式中断的会话 → session 行 `interrupted` → `POST /api/sessions/{id}/resume` 通过日志门与状态门（扩展 `test_api/test_resume_endpoint.py`）

## 7. 全量验证

- [x] 7.1 四项检查全绿：`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`python -m pytest tests/ -q`；确认无既有测试因提交序变化而未更新
