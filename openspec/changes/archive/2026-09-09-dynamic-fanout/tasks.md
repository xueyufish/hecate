# Tasks: 1.3.21③ Send-style Dynamic Fan-out

## 1. 前置核对与类型基础

- [x] 1.1 复核 `reduce_fn` 存量用法:确认生产代码仅 `"add"`/`None`(预检已过:`studio/workflows/patterns.py:252` 为启发式字符串判断,不依赖注册;实现期最终确认),结论记入 PR 描述
- [x] 1.2 `runtime/types.py`:新增 `DispatchPacket`(`node`/`state`)与 `Invocation`(目标节点 + 调用身份 `{fanout_source, branch_index}` + 子通道名)dataclass;`WorkerResult`/`Command` 不变

## 2. 通道层:可插拔 reducer(design D7 / specs channel-registry)

- [x] 2.1 `channel.py`:模块级 `_REDUCERS` 注册表 + `register_reducer(name, fn)`;内建 `"add"`(行为不变)与 `"append"`;`AccumulatorBehavior.write` 按 `defn.reduce_fn` 查表合并
- [x] 2.2 移除未知 `reduce_fn` 静默 overwrite 回退:`ChannelManager.register` 与 compiler 校验双点显式报错;补/改 `tests/test_runtime/test_channel_registry.py` 与受影响测试
- [x] 2.3 reducer 冲突解析(`resolve_conflict`)与注册表对齐:未知 reducer 的 conflict 委托行为显式定义并测试

## 3. DSL 声明面(specs graph-dsl)

- [x] 3.1 `graph-dsl.schema.json` + 解析器:CONDITION 节点 `fanout` 配置(`over`/`target`/`state_key`/可选 `max_fanout`);`to_json` roundtrip
- [x] 3.2 compiler 编译期校验:`over` 通道存在于 state、`target` 节点存在、`state_key` 非空、`max_fanout` 正整数、非 CONDITION 类型拒绝;错误指明字段;`tests/test_runtime/test_graph_dsl.py` 场景

## 4. 引擎核心:调用列表与动态派发(design D2/D3/D5/D6 / specs pregel-runtime, fan-out-merge)

- [x] 4.1 `_resolve_next_nodes` 返回 `list[Invocation]`:静态/`_route`/`goto` 路径包装为无载荷 invocation(行为逐字不变,既有测试全绿);`goto` > `_dispatch` > `_route` 优先级与并存告警
- [x] 4.2 ConditionWorker fanout 求值:读 over 通道逐元素产出 `_dispatch` 包;空列表零派发;单元测试
- [x] 4.3 动态派发器:计划 → invocation 列表;派发前 seed 各包 `state` 至 `_fanout__{src}__idx{i}`;asyncio.gather 并行;NODE_START/NODE_END payload 增 `fanout_source`/`branch_index`
- [x] 4.4 上限强制:`FanoutLimitError`(节点级/superstep 级/绝对钳制,fail closed);图与节点覆盖;测试超限路径
- [x] 4.5 `on_branch_error`:`fail_fast` 默认(现状保持)+ `collect`(子通道记 `__branch_error__` 条目,整批继续);测试两模式
- [x] 4.6 分支隔离测试:同目标 N 调用各有独立子通道、互不可见对方切片

## 5. 日志与 replay(design D1/D4 / specs execution-state-log)

- [x] 5.1 `logpolicy.py`:`_dispatch` 显式豁免(同 `_route`);`tests/test_runtime/test_logpolicy_fold.py` 用例
- [x] 5.2 扇出 superstep 的 `STEP_END` commit payload 增 `fanout` 段(源节点 + 调用身份 + 子通道值);`logfold` 遇段重建子通道;静态与动态路径统一;T2b 两场景测试(扇出窗口 fork 不丢分支输出、缓存缺失 log-only 恢复后 MERGE 正确)
- [x] 5.3 `continuation.py`:`_dispatch` 采纳规则(最后 `STEP_END` 锚点 executed 含写入者才采纳,取计划调用列表;陈旧计划不采纳);纯日志推导;单测覆盖含 `_dispatch` 的四类锚点组合

## 6. 中断叠加(design D8 / specs pregel-runtime)

- [x] 6.1 planner ∈ `interrupt_after`:计划提交后、派发前暂停;恢复经采纳规则按计划派发;测试(计划评审中断场景)
- [x] 6.2 扇出目标 ∈ `interrupt_before`:整批调用派发前暂停一次(① 一次性豁免沿用),描述符 nodes 列目标节点;恢复后正常派发;测试

## 7. MERGE 动态聚合(specs fan-out-merge)

- [x] 7.1 `_execute_merge` 计划发现:`fan_out_source` 为动态源时从折叠态计划取调用集合,按调用身份聚合;collect 模式错误条目保留;聚合写 ACCUMULATOR 通道经注册 reducer
- [x] 7.2 静态路径回归:`{branch_id: result}` 语义逐字不变的既有测试全绿

## 8. time-travel 接线(specs time-travel-resume)

- [x] 8.1 `commit-points` 扇出元数据:扇出 superstep 锚点附带源节点与包数;服务层/API 测试
- [x] 8.2 fork 跨扇出窗口:计划已提交分支未派发 → 子会话续跑集为计划调用;分支已完成 → 续跑集为 MERGE 不重复派发;`snapshot_at_version`/FORK 载荷保真(依赖 5.2);集成测试

## 9. 全量验证

- [x] 9.1 四件套零错误:`ruff check`、`ruff format --check`、`mypy src/`、受影响层 pytest
- [x] 9.2 全量 `pytest tests/ -q` 绿;逐条核对六个 delta spec 的 Scenario 均有对应测试映射,缺口补齐
