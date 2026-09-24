# memory-consolidation Specification(Delta)

## MODIFIED Requirements

### Requirement: 整合触发总线

系统 SHALL 提供五种触发来源,且全部作用于同一"待处理整合"判定:某个整合单元 `(workspace_id, agent_id, actor_id | null, team_id | null)` 自上次成功整合水位以来存在新的对话转录或新的 closed episode。五种来源为:

(a) `fixed_interval` 定时调度(默认每日 02:00);
(b) idle 静默触发(整合单元安静超过配置时长且有新转录);
(c) 压力标记优先(被 memory-pressure-alert 标记的整合单元在下一轮调度中获得优先);
(d) `reflection_trigger`:当 `task-memory` capability 的 ReflectionEngine 对一个 episode 提取反思时,反思 run 受同一调度总线驱动,与 consolidation run 共享调度优先级与多实例互斥机制;
(e) `pre_compaction` flush 交接(`MEMORY_FLUSH_ENABLED=true` 时):L2 上下文压缩(surface replacement)确定将删窗口时,压缩路径 SHALL 向该会话所属整合单元同步登记一条待提取 flush 窗口。登记 SHALL 为轻量同步写,登记失败仅记录告警且压缩照常执行(best-effort);登记窗口由调度总线异步提取,遵循与既有整合产物相同的 at-least-once 与水位幂等语义;登记窗口在调度中复用压力标记的优先机制。

flush 交接 SHALL 满足:登记与后续提取不阻塞压缩、不阻塞在线会话;提取重试耗尽后仅告警(指标 + 日志),不升级为失败事务。该机制的正确性依赖一条平台配置约束:事件保留期 SHALL 显著大于提取管线延迟与重试周期之和,确保任何已登记窗口在事件被保留期清理前存在足够补提机会;整合内容之外的记忆清扫(见 `memory-lifecycle` capability)亦复用本调度总线。

整合能力 SHALL 默认关闭(`CONSOLIDATION_ENABLED`),经显式配置启用;`reflection_trigger` 单独受 `REFLECTION_ENABLED` flag 控制,默认关闭;`pre_compaction` 单独受 `MEMORY_FLUSH_ENABLED` flag 控制,默认关闭。

#### Scenario: cron 定时触发

- **WHEN** 到达配置的调度时刻(默认每日 02:00)且存在新转录的整合单元
- **THEN** 系统对这些整合单元各执行一轮整合,并在 `consolidation_runs` 记录触发来源为定时调度

#### Scenario: idle 静默触发

- **WHEN** 某整合单元最后一次活动距今超过配置的静默时长,且其审查窗口存在新转录
- **THEN** 该整合单元在随后的轮询中被调度整合,无需等待下一次定时时刻

#### Scenario: 压力标记优先

- **WHEN** 某整合单元存在来自 memory-pressure-alert 的未消费压力标记
- **THEN** 下一轮调度中该单元先于无标记单元被处理

#### Scenario: reflection_trigger 触发反思 run

- **WHEN** `REFLECTION_ENABLED=true` 且一个新 episode 被 `episode_close` 打戳
- **THEN** ReflectionEngine 在调度总线中记录一条 `reflection_runs`,trigger='reflection_trigger'
- **AND** 该 run 与同单元的 consolidation run 共享优先级,但独立审计(平行表)

#### Scenario: pre_compaction 登记与异步提取

- **WHEN** `MEMORY_FLUSH_ENABLED=true` 且一次 L2 压缩确定了将删窗口
- **THEN** 压缩路径同步向所属整合单元登记该窗口后立即返回,压缩不被阻塞
- **AND** 调度总线以优先级拾取该窗口,经整合管线提取并写入记忆产物,水位按既有先晋升后记账语义推进

#### Scenario: flush 登记失败不阻塞压缩

- **WHEN** flush 登记的同步写失败(如存储抖动)
- **THEN** 压缩照常完成,仅记录一条告警;该窗口因仍高于整合水位,将由后续 idle/cron 调度照常覆盖

#### Scenario: flush 提取重试耗尽只告警

- **WHEN** 某已登记 flush 窗口的提取反复失败并耗尽重试
- **THEN** 系统记录指标与日志告警,不阻塞任何在线路径;窗口内容仍高于水位,待下一轮调度继续尝试

#### Scenario: 重复登记幂等

- **WHEN** 同一窗口被登记多次(如连续两轮压缩边界相邻)
- **THEN** 调度提取以水位判定,已提取内容不重复写入记忆产物

#### Scenario: 默认关闭

- **WHEN** 未启用整合配置,或 `REFLECTION_ENABLED=false`,或 `MEMORY_FLUSH_ENABLED=false`(三者均为默认)
- **THEN** 系统不执行任何整合、反思或 flush 交接调度,不产生任何后台记忆写入
