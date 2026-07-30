## ADDED Requirements — 新增需求

### Requirement: FAN_OUT 节点分发并行分支 — FAN_OUT 节点分发并行分支
FAN_OUT 节点类型 SHALL 表示一个并行分发点，将执行拆分为多个并发分支。节点配置 SHALL 包含一个 `branches` 字段，列出所有并行分支目标的节点 ID。

#### Scenario: FAN_OUT 节点配置
- **WHEN** 一个节点的类型为 FAN_OUT，配置为 `{"branches": ["analyst_a", "analyst_b", "analyst_c"]}`
- **THEN** 运行时 SHALL 同时分发所有 3 个分支节点

#### Scenario: FAN_OUT 节点不执行 worker
- **WHEN** 运行时遇到 FAN_OUT 节点
- **THEN** FAN_OUT 节点本身 SHALL NOT 调用 worker——它仅触发其分支节点的分发

### Requirement: MERGE 节点收集并行分支结果 — MERGE 节点收集并行分支结果
MERGE 节点类型 SHALL 表示一个聚合点，收集前面 FAN_OUT 所有分支的结果。节点配置 SHALL 包含一个 `fan_out_source` 字段引用 FAN_OUT 节点 ID 和一个 `output_channel` 字段指定聚合结果的写入位置。

#### Scenario: MERGE 收集所有分支输出
- **WHEN** MERGE 节点在具有 3 个分支的 FAN_OUT 之后执行
- **THEN** MERGE 节点 SHALL 读取所有分支子通道，并将字典 `{branch_id: result}` 写入输出通道

#### Scenario: MERGE 等待所有分支
- **WHEN** MERGE 节点已到达但并非所有分支都已完成
- **THEN** MERGE 节点 SHALL 等待所有分支结果可用后再生成输出

### Requirement: FAN_OUT 分支子通道隔离 — FAN_OUT 分支子通道隔离
FAN_OUT 的每个分支 SHALL 写入隔离的子通道，命名为 `_fanout__{fan_out_node_id}__{branch_node_id}`，以防止并行分支覆盖彼此的状态。

#### Scenario: 分支写入子通道
- **WHEN** 分支节点 "analyst_a" 作为 FAN_OUT 节点 "fanout_1" 的一部分执行
- **THEN** 分支结果 SHALL 写入子通道 `_fanout__fanout_1__analyst_a`

#### Scenario: 主通道状态被保留
- **WHEN** 并行分支并发执行
- **THEN** 主 "messages" 通道 SHALL NOT 被单个分支修改——只有 MERGE 节点写入聚合结果