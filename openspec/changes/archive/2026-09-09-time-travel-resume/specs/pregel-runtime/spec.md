## ADDED Requirements

### Requirement: 续跑节点集的日志推导

恢复路径 SHALL 从会话日志的**最后一个提交点描述符**推导续跑节点集,规则按提交点类别:

1. `INTERRUPT`(未闭合):沿用既有 phase-aware 推导——`before` 执行被暂停节点本身,`after` 求出边并集,worker-authored 求中断节点出边(① 语义逐字保持);
2. `FORK`:取 payload 的 `next_nodes`;
3. `STEP_END`(尾部崩溃恢复):取自上一提交点以来的 `NODE_END` 节点集,求出边并集,条件边经恢复后的 `_route` 通道裁决;
4. 无任何提交点(空日志):取图入口。

携带 `source="update_state"` 的 `STEP_END`(状态修改批次收尾)SHALL NOT 作为续跑锚点——修改不是执行进度,推导时跳过并向前找上一个提交点。推导 SHALL NOT 依赖 checkpoint 缓存 metadata(缓存仅作折叠加速);多来源混存时按日志顺序取最后者。

#### Scenario: FORK 描述符续跑
- **WHEN** 子会话日志最后提交点为 `FORK`,payload `next_nodes=["plan_review"]`
- **THEN** 恢复后首个 superstep SHALL 调度 `plan_review` 而非图入口

#### Scenario: 尾部崩溃恢复推导
- **WHEN** 会话日志最后提交点为 `STEP_END`,其前一个提交点之后存在 `NODE_END(A)`、`NODE_END(B)`
- **THEN** 恢复 SHALL 从 A 与 B 的出边并集(条件边按恢复的 `_route` 值)调度

#### Scenario: 描述符优先级按日志顺序
- **WHEN** 日志中 `FORK` 之后又出现未闭合 `INTERRUPT`
- **THEN** 续跑节点集 SHALL 按 `INTERRUPT` 的 phase-aware 规则推导

### Requirement: resume_from 仅限日志尾部

引擎执行入口接受 `resume_from`(日志版本)参数时,SHALL 仅在该版本等于会话当前日志尾部版本时执行恢复(崩溃恢复语义);版本小于尾部时 SHALL 抛出错误并明示"历史点恢复必须经 fork 创建子会话"。历史点 fork 的编排属服务层职责,引擎 SHALL NOT 在同一会话日志内产生双时间线。

#### Scenario: 尾部版本放行
- **WHEN** `resume_from` 等于当前日志尾部版本
- **THEN** 引擎 SHALL 折叠至该版本并按续跑推导继续执行

#### Scenario: 历史版本拒绝
- **WHEN** `resume_from` 小于当前日志尾部版本
- **THEN** 引擎 SHALL 抛出带指引信息的错误,且 SHALL NOT 向日志追加任何事件
