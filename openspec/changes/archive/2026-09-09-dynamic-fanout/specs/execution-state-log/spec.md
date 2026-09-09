## ADDED Requirements

### Requirement: `_dispatch` 计划通道入日志

`_dispatch` SHALL 同 `_route` 待遇:作为 fold 正确性的一部分显式豁免于控制通道黑名单,其写入 SHALL 产生 `CHANNEL_WRITE` 事件,携带完整派发计划(调用目标 + 载荷 + 分支上限判定所需元数据)。折叠机器 SHALL 将 `_dispatch` 写入投影至通道状态,使崩溃恢复、fork 与 continuation 推导可从纯日志重建派发计划。

#### Scenario: 计划写入产生日志事件
- **WHEN** planner 节点向 `_dispatch` 写入含 5 个包的计划
- **THEN** 该 superstep 的 WAL 批次 SHALL 含携带完整计划的 `CHANNEL_WRITE` 事件

#### Scenario: 折叠后计划可读
- **WHEN** 对含上述事件的日志做 log-only fold
- **THEN** 折叠态中 `_dispatch` SHALL 等于计划全文,恢复的续跑推导可据此重建调用列表

### Requirement: 扇出分支输出折叠不变式(T2b)

扇出分支输出 SHALL 对 replay 与 fork 保真:对包含扇出 superstep 的日志做 log-only fold(无缓存路径)或构造 `FORK` 载荷时,各分支子通道的值 SHALL 可重建,使得恢复后的 MERGE 聚合结果与活体执行路径一致。该不变式 SHALL 同时覆盖静态 FAN_OUT 与动态扇出(顺带闭合既有缺陷:扇出窗口内的 fork 载荷与缓存缺失的 log-only 恢复丢失分支输出)。实现机制(logpolicy 对子通道的排除条件修订,或 fold 自分支输出重建)由 design 定夺,spec 只约束可观察行为。

#### Scenario: 扇出窗口内 fork 不丢分支输出
- **WHEN** 在静态 FAN_OUT 已提交、MERGE 未执行的日志版本上执行 fork
- **THEN** 子会话 FORK 载荷 SHALL 携带全部分支子通道值,子会话中 MERGE SHALL 聚合出与父会话活体路径一致的结果

#### Scenario: 缓存缺失的 log-only 恢复后 MERGE 正确
- **WHEN** 会话在扇出 superstep 提交后崩溃,恢复时 checkpoint 缓存不可用,仅靠日志折叠
- **THEN** 恢复后的 MERGE SHALL 读到全部分支输出,SHALL NOT 出现 `{branch: None}` 空聚合
