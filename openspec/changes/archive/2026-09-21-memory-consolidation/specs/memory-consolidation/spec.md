# Spec Delta: memory-consolidation

## Purpose

提供定时与事件驱动的后台记忆整合(sleep-time consolidation):在 agent 空闲期审查对话转录,抽取持久事实,按重要性与新颖性筛选,以可审计、可取代(而非删除)的方式更新 L3/L4 记忆与 L1 `learned_context` block,改善后续会话的起点。

## ADDED Requirements

### Requirement: 整合触发总线

系统 SHALL 提供三种整合触发策略,且全部作用于同一"待处理整合"判定:某个整合单元 `(workspace, agent, user_id | null)` 自上次成功整合水位以来存在新的对话转录。三种策略为:(a) `fixed_interval` 定时调度(默认每日 02:00);(b) idle 静默触发(整合单元安静超过配置时长且有新转录);(c) 压力标记优先(被 memory-pressure-alert 标记的整合单元在下一轮调度中获得优先)。整合能力 SHALL 默认关闭,经显式配置启用。

#### Scenario: cron 定时触发

- **WHEN** 到达配置的调度时刻(默认每日 02:00)且存在有新转录的整合单元
- **THEN** 系统对这些整合单元各执行一轮整合,并在 `consolidation_runs` 记录触发来源为定时调度

#### Scenario: idle 静默触发

- **WHEN** 某整合单元最后一次活动距今超过配置的静默时长,且其审查窗口存在新转录
- **THEN** 该整合单元在随后的轮询中被调度整合,无需等待下一次定时时刻

#### Scenario: 压力标记优先

- **WHEN** 某整合单元存在来自 memory-pressure-alert 的未消费压力标记
- **THEN** 下一轮调度中该单元先于无标记单元被处理

#### Scenario: 默认关闭

- **WHEN** 未启用整合配置
- **THEN** 系统不执行任何整合调度,不产生任何后台记忆写入

### Requirement: 多实例互斥与断点续跑

多实例部署下,同一整合单元的同一次调度 SHALL 仅由一个实例执行(经 PostgreSQL advisory lock 互斥)。水位推进 SHALL 遵循先晋升后记账:仅当某单元的整合产物全部成功应用后,该单元的审查水位才前进;应用失败的事务单元 SHALL 在下一轮被重试(at-least-once)。

#### Scenario: 双实例不重复执行

- **WHEN** 两个应用实例同时到达调度时刻并尝试整合同一单元
- **THEN** 仅获得锁的实例执行整合,另一实例跳过该单元且不记录运行

#### Scenario: 失败单元重试

- **WHEN** 某单元整合在应用阶段失败
- **THEN** 该单元审查水位不变,失败原因记录于 `consolidation_runs`,下一轮调度重新处理该单元

### Requirement: 整合引擎三段管线

整合 SHALL 按"抽取 → 打分与去重 → 计划应用"三段执行:(a) 从审查窗口的对话转录中抽取候选持久事实;(b) 对候选做重要性/新颖性打分,并与同整合单元内现有记忆做相似度比较;(c) 由 LLM 产出类型化操作计划(ADD / UPDATE / SUPERSEDE / NO-OP),由确定性代码校验后应用。LLM SHALL NOT 直接执行记忆写操作;所有应用动作 SHALL 经与在线 memory tools 相同的服务层写路径,遵守 revision 乐观并发。每个整合单元的每次运行 SHALL 受 LLM 调用次数与记忆变更数量的配置上限约束,超限时终止并记录。

#### Scenario: 常规整合产出

- **WHEN** 某单元的审查窗口包含关于用户偏好的新对话,且现有记忆中无相似条目
- **THEN** 计划含对该事实的 ADD 操作,应用后新记忆可被既有记忆检索路径查到,且 `memory_edit_log` 记录该变更

#### Scenario: revision 冲突跳过

- **WHEN** 计划中某操作的目标记忆在运行期间被在线写入修改(revision 不匹配)
- **THEN** 该操作被跳过并在运行记录中标记,同轮其余操作不受影响

#### Scenario: 运行超限终止

- **WHEN** 单次运行的 LLM 调用或变更数达到配置上限
- **THEN** 运行以超限状态结束,已应用部分保持一致,剩余候选留待下一轮

### Requirement: SUPERSEDE 取代语义

当新信息与现有记忆冲突时,系统 SHALL 以取代代替删除:被取代记忆置 `superseded_by` 指针并软删除,记录保留且可查询;取代动作 SHALL 记录于 `memory_edit_log`(含 before/after 摘要)。系统 SHALL NOT 因整合而物理删除任何记忆行。

#### Scenario: 冲突事实取代

- **WHEN** 审查窗口中的新信息与某条现有 L3 记忆语义冲突且被判定为更新
- **THEN** 旧记忆被置 `superseded_by` 指向新记忆并软删除,新旧两条记录均可查询,编辑日志含取代记录

### Requirement: 运行审计

系统 SHALL 为每次整合运行维护一条运行级审计记录(`consolidation_runs`):触发来源、整合单元、审查窗口边界、统计(候选数/采纳数/拒绝数/失败数)、逐条操作的类型与结果、运行的最终状态。审计记录 SHALL 只追加,不被后续运行改写。

#### Scenario: 运行可追溯

- **WHEN** 管理员查询某 agent 的整合历史
- **THEN** 每次运行可见其触发来源、窗口、逐条操作结果与状态

### Requirement: 候选安全扫描

候选事实在应用前 SHALL 经过 injection/guardrail 检测;命中的候选 SHALL 被拒绝并记录拒绝原因。系统 SHALL NOT 将凭证类内容(密钥、令牌、口令及其等价物)写入任何记忆层。

#### Scenario: 注入载荷被拒绝

- **WHEN** 对话转录中包含试图操控后续会话的指令文本,且被抽取为候选事实
- **THEN** 安全扫描拒绝该候选,运行记录标记拒绝原因,该内容不进入任何记忆层

### Requirement: 整合单元隔离

整合 SHALL 以 `(workspace, agent, user_id | null)` 为隔离单元:用户级持久事实仅写入携带该 `user_id` scope 的 L3 记忆;L1 `learned_context` block 为 agent 级(workspace + agent),不含用户专属事实。任何整合产物 SHALL NOT 跨工作空间可见。

#### Scenario: 用户事实不跨范围

- **WHEN** 整合用户 A 的对话抽取到个人偏好事实
- **THEN** 该事实仅落入 A 的 user-scoped L3 记忆,同一 agent 服务用户 B 时检索不到该事实

### Requirement: L1 重写允许清单

对 L1 memory block 的整合写入(`UPDATE_BLOCK`)SHALL 受 per-agent 允许清单约束;默认清单 SHALL 仅包含专用 `learned_context` block。清单之外的 block(含 persona 等在线工具可编辑 block)SHALL 拒绝整合写入并记录。允许清单为空的 agent SHALL 不被整合触碰任何 L1 block。

#### Scenario: persona 默认不被重写

- **WHEN** 默认配置下整合计划包含对 `persona` block 的 UPDATE_BLOCK 操作
- **THEN** 该操作被拒绝并记录,`persona` 内容不变

#### Scenario: learned_context 正常更新

- **WHEN** 允许清单为默认值且计划包含对 `learned_context` block 的更新
- **THEN** block 内容经既有 block 写路径更新,revision 递增,编辑日志记录变更

### Requirement: 真实相似度计算

整合的相似度比较(去重/新颖性)SHALL 使用真实 embedding 计算,SHALL NOT 依赖 mock 向量。被整合触碰的记忆行 SHALL 将真实向量回写至其 embedding 字段,替换既有 mock 值。

#### Scenario: 相似事实合并

- **WHEN** 候选事实与同单元现有记忆的相似度超过配置阈值
- **THEN** 计划生成 UPDATE 或 SUPERSEDE 而非 ADD,不产生近重复记忆

### Requirement: 单条失败隔离与在线无感

单条候选的处理失败 SHALL NOT 中断同轮其余候选。整合引擎的任何故障 SHALL NOT 影响在线会话:引擎不可用时,在线对话、memory tools 与既有检索路径行为不变。

#### Scenario: 引擎故障无感

- **WHEN** 整合引擎在调度期间持续抛错
- **THEN** 在线会话与 memory tools 行为不变,失败被记录并按重试策略退避
