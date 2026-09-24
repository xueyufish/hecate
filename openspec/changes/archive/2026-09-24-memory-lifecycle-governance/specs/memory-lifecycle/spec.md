# memory-lifecycle Specification(Delta)

## Purpose

定义记忆产物(L3 用户事实、L4 知识记忆)的生命周期语义:分层 TTL 过期、容量上限与淘汰、archive 软删与恢复、跨 namespace 晋升门,以及周期性清扫调度与审计。记忆由"只进不出"变为有界、可治理、可恢复。

## ADDED Requirements

### Requirement: 分层 TTL 过期

系统 SHALL 为 L3 与 L4 记忆产物提供按层配置的 TTL(存活上限):L3 episodic 与 L3 semantic 各自独立配置,L4 默认永不过期。TTL 的锚点为记忆的 `last_confirmed_at`(与既有时间衰减同锚);TTL 到期的记忆 SHALL 被 archive 软删,SHALL NOT 被物理删除。TTL 与检索排序中的时间衰减相互独立:衰减影响排序,TTL 决定存活。TTL 取值来源遵循 `memory-policy` capability 的生效链(平台环境变量给出各层默认值,策略对象可覆盖);recall 层的保留沿用既有 `RECALL_TTL_DAYS`,不在本 capability 范围内。

#### Scenario: TTL 到期 archive

- **WHEN** 一条 L3 episodic 记忆的 `last_confirmed_at` 距今超过该层生效 TTL
- **THEN** 下一轮生命周清扫将该记忆标记为 archived(软删),不物理删除,并记录生命周期审计

#### Scenario: L4 默认永不过期

- **WHEN** 策略未对 L4 配置 TTL
- **THEN** L4 记忆不因时间过期,仅可被容量淘汰或显式 archive

#### Scenario: TTL 缺省退化平台默认

- **WHEN** 生效链上无任何策略覆盖
- **THEN** 各层使用平台环境变量默认值,行为与未部署策略对象时一致

### Requirement: 容量上限与淘汰

系统 SHALL 支持对 L3(按生效 namespace scope 计)与 L4(默认不启用,可选开启)配置容量上限。容量超限时,系统 SHALL 按淘汰评分选择被淘汰记忆:评分 SHALL 复用既有 fusion 排序的信号族(以 `last_confirmed_at` 为锚的时间衰减 × 重要度,叠加访问热度),评分为该信号族在检索排序中的既有规范化分数,不引入新的第二套评分。被淘汰记忆 SHALL archive 软删;每轮清扫的淘汰条数 SHALL 受预算限制以防风暴。近期确认(在淘汰保护窗口内)的记忆 SHALL 不被淘汰。

#### Scenario: 超限淘汰最低分

- **WHEN** 某 scope 的 L3 记忆数超过生效容量上限
- **THEN** 清扫按淘汰评分从低到高 archive 记忆直至回落到上限内,单轮不超过淘汰预算

#### Scenario: 淘汰是软删

- **WHEN** 一条记忆被容量淘汰
- **THEN** 该记忆保留全部数据与审计谱系(含 `superseded_by` 链一致性),仅从检索路径排除,可被恢复

#### Scenario: 保护窗口内不淘汰

- **WHEN** 一条低分记忆的 `last_confirmed_at` 在配置的保护窗口内(如 7 天内被确认或命中)
- **THEN** 该记忆跳过本轮淘汰

### Requirement: archive 与恢复

系统 SHALL 提供 archive(软删)与恢复操作,经治理 REST API 与 Studio UI 暴露(见 `memory-api` / `memory-governance-ui` capability)。archived 记忆 SHALL 从全部检索路径排除(记忆工具 `memory_search`、fusion 检索、prefetch、REST 搜索);恢复后 SHALL 重新参与检索。archive/恢复 SHALL 记录 `memory_edit_log` 审计(生命周期操作来源)。物理删除 SHALL NOT 由本 capability 提供。

#### Scenario: archived 记忆退出检索

- **WHEN** 一条 L3 记忆被 archive
- **THEN** 后续 `memory_search`、fusion 检索、prefetch 与治理 REST 搜索均不返回该记忆

#### Scenario: 恢复重新参与检索

- **WHEN** 管理者对一条 archived 记忆执行恢复
- **THEN** 该记忆重新出现在检索路径中,恢复操作记入 `memory_edit_log`

### Requirement: 跨 namespace 晋升门

系统 SHALL 支持将低层级 namespace 的 L3 记忆晋升为更宽共享范围(team 或 workspace 级),晋升 SHALL 满足全部门槛:淘汰评分(同上定义)达到阈值、观察期内检索命中次数达到下限、记忆存在时长达到下限。晋升 SHALL 复用 `cross-thread-memory-store` 的 namespace 写路径与隔离校验,SHALL 记录 `memory_edit_log` 审计。晋升能力 SHALL 默认关闭(策略对象按 scope 显式启用)。

#### Scenario: 满足门槛晋升

- **WHEN** 某 actor 级 L3 记忆的评分、命中次数与存在时长均达到策略配置门槛,且该 scope 启用了晋升
- **THEN** 清扫将该记忆按 namespace 写路径复制/提升为 team(或 workspace)级共享记忆,原 actor 级记忆保留,晋升记入审计

#### Scenario: 默认关闭

- **WHEN** 策略未启用晋升
- **THEN** 任何记忆不发生跨 namespace 晋升

### Requirement: 生命周清扫调度

TTL 过期检查、容量淘汰与晋升评估 SHALL 在整合触发总线的定期清扫中执行,复用其多实例 advisory lock 互斥。清扫 SHALL 独立于"新转录/新 episode"的待处理判定(过期与超限不依赖新内容);每轮清扫 SHALL 受预算约束(扫描与淘汰条数上限)。清扫能力 SHALL 受 `MEMORY_LIFECYCLE_ENABLED` 控制,默认关闭;关闭时任何记忆不因 TTL 或容量被 archive。

#### Scenario: 清扫与整合互斥

- **WHEN** 同一整合单元的生命周清扫与整合 run 同时被调度
- **THEN** 二者经同一 advisory lock 串行执行

#### Scenario: 预算限制

- **WHEN** 某轮清扫中待淘汰记忆数超过单轮淘汰预算
- **THEN** 本轮仅淘汰预算内数量,余量留待下一轮

#### Scenario: 默认关闭

- **WHEN** `MEMORY_LIFECYCLE_ENABLED=false`(默认)
- **THEN** 不执行任何 TTL 过期、容量淘汰或晋升评估,存量记忆行为与本变更合入前一致

### Requirement: 生命周期审计

全部生命周期操作(TTL 过期 archive、容量淘汰、恢复、跨 namespace 晋升)SHALL 记入 `memory_edit_log`,操作来源标识为生命周期(与 agent 工具操作、consolidation 操作的来源标识并列),并记录操作原因(TTL 过期 / 容量淘汰 / 人工恢复 / 晋升)。

#### Scenario: 审计可追溯

- **WHEN** 管理者查看一条被淘汰记忆的编辑日志
- **THEN** 可见该记忆因容量淘汰被 archive 的记录,含时间与原因,与 agent 工具编辑记录格式一致
