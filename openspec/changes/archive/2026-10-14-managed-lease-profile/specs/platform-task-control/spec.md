# Spec Delta

## MODIFIED Requirements

### Requirement: 治理事件采用版本化 envelope 并按 Run 游标可读

平台在 Task 提交、生命周期迁移、命令记录/迁移与对账标记时 SHALL 发射治理事件:版本化 envelope 携带 event_id、task/run 引用、actor、source、source_sequence、事件/接收时间、payload schema 引用与关联/因果 ID;治理事件 profile 下 actor 与 source MUST 非空(分别标识动作主体与权威写入方)。事件 SHALL 持久化并可按 Run 引用 + 游标分页读取;已读取位置之后的断线消费 SHALL 能以游标恢复,不依赖客户端时钟。执行流事件(引擎流映射后的 envelope)SHALL 在任务 API 派发路径持久追加,同步与流式接口为同一 Task/Run 上的等待/订阅视图。事件读取 SHALL 以 workspace 作用域隔离,跨 workspace 读取 MUST 与不存在不可区分。

`source_sequence` 在 (workspace, run) 维度 SHALL 由存储层唯一约束保证:并发的序号分配竞争 MUST 显式冲突失败,经 event_id 去重后重试收敛, MUST NOT 产生同位静默重排;迟到事件(序号低于已存最大序号)的落库 SHALL 显式处理(按 event_id 去重或重序追加),MUST NOT 静默丢弃或破坏游标分页读取语义。治理事件写入方保持单一且分列:平台发射路径与宿主事实投影路径各以自身 source 写入,任一写入方 MUST NOT 覆盖或重开另一方已写入的事实(终态投影不重开语义保持)。存储升级遇到历史同位重复数据时 SHALL 显式报告并修复后再施加唯一约束,MUST NOT 静默保留破坏唯一性。

#### Scenario: 关键迁移发射治理事件

- **WHEN** Task 从 queued 迁移到 running 再到 succeeded
- **THEN** 每次迁移产生一条含 actor 与 source 的版本化事件,按时间可追溯

#### Scenario: 游标断线恢复

- **WHEN** 消费者读取某 Run 事件到游标 C 后断线,重连后携带 C 继续读取
- **THEN** 返回 C 之后的事件,不重复已读事件也不跳过新事件

#### Scenario: 跨 workspace 事件读取隔离

- **WHEN** 以 workspace A 的上下文请求 workspace B 的 Run 事件
- **THEN** 响应与 Run 不存在时不可区分,不泄露事件内容

#### Scenario: 并发序号分配冲突显式且经去重收敛

- **WHEN** 两个写入方并发为同一 Run 分配下一个 source_sequence,双方均提交落库
- **THEN** 存储层唯一约束使后提交方显式冲突失败;该事件经 event_id 去重判定后或被识别为重复(幂等跳过)或重试获得新序号落库,事件流中不存在同位双写或静默丢失

#### Scenario: 迟到事件显式落库不破坏游标读取

- **WHEN** 投影器收到 event_id 未落库但序号低于该 Run 已存最大序号的迟到事件
- **THEN** 该事件按去重/重序语义显式落库,游标分页读取不因迟到追加而跳过或重复返回事件

#### Scenario: 历史同位重复数据升级显式修复

- **WHEN** 存储升级在施加唯一约束前发现同一 (workspace, run, source_sequence) 存在多条历史事件
- **THEN** 升级路径显式报告该数据并按确定性规则修复(保留权威写入方记录),修复完成前不宣称约束已生效
