## ADDED Requirements

### Requirement: fork 跨越扇出窗口的计划与输出保真

fork 的锚点选择 SHALL 覆盖扇出提交点:`commit-points` 列举 SHALL 含扇出 superstep 的 `STEP_END` 锚点(无需新增事件类型);在扇出提交点(计划已提交、MERGE 未执行)fork 时,子会话的 `FORK` 载荷 SHALL 携带派发计划与全部分支输出,推导续跑集(`next_nodes`)SHALL 为计划的调用列表而非静态出边——子会话从 fork 点继续 SHALL 重现父会话的扇出执行(分支已在父会话完成的,子会话 SHALL NOT 重复派发)。

#### Scenario: 从扇出提交点 fork 后继续
- **WHEN** 在动态扇出 superstep 已提交、分支未派发的日志版本 fork
- **THEN** 子会话 SHALL 以计划中的 5 个调用为续跑集,SHALL NOT 回退到 planner 的静态出边

#### Scenario: 分支完成后 fork 不重复派发
- **WHEN** 在分支全部执行完毕、MERGE 未执行的日志版本 fork
- **THEN** 子会话续跑集 SHALL 为 MERGE(分支已在其折叠态中),SHALL NOT 再次派发分支调用

### Requirement: commit-points 列举呈现扇出信息

`commit-points` 列举对扇出 superstep 的锚点 SHALL 附带可辨识元数据(如计划包数 / 扇出源节点),使 8.20 回放与 what-if 消费方能定位扇出窗口,SHALL NOT 要求消费方自行 fold 全日志推断。

#### Scenario: 扇出锚点可定位
- **WHEN** 会话在某 superstep 执行了 5 包动态扇出
- **THEN** 该 superstep 的 `STEP_END` 锚点 SHALL 携带扇出元数据(源节点与包数)
