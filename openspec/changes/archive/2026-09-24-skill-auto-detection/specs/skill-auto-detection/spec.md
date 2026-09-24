# skill-auto-detection Delta

## Purpose

skill 基于任务上下文自动发现与按需加载：workspace 内符合条件的 skill 无需手动 agent-skill 绑定即可被模型发现（description 驱动、LLM 自选），配合分层开关、trust tier 下限与来源遥测构成可治理的发现机制。绑定 = 冻结面（ref_manifest pinning），发现 = 活面（运行时活解析）。

## ADDED Requirements

### Requirement: Discovery pool composition

Skill 自动发现的候选池 SHALL 由以下来源构成：当前 workspace 的全部 skill 与 bundled 域（零 UUID workspace）的全部 skill，经 provider-registry 优先级归并同名行后，满足全部条件的行：未删除、`model_invocable=true`、`provider != 'user'`、plugin 来源的 skill 其所属 plugin 处于启用状态。`provider='user'` 的 skill SHALL 被排除在发现池之外（该分类声称"工作区成员个人所有"，而平台数据层无成员级隔离；显式绑定与 `auto_load` 路径不受此排除影响）。显式绑定与 `auto_load` 的 skill 的既有加载语义 SHALL 完全不变。

#### Scenario: Workspace skill without binding becomes discoverable

- **WHEN** discovery 生效且 workspace 中存在一个 `model_invocable=true` 的 project skill，它不在任何 agent 的 `skills` 列表中
- **THEN** 该 skill 的 name 与 description SHALL 出现在该 agent 的 L1 catalog 中，且模型可通过按需加载获取其全文

#### Scenario: User-provider skill excluded from discovery

- **WHEN** discovery 生效且 workspace 中存在一个 `provider='user'` 的 skill，它未被任何 agent 绑定
- **THEN** 该 skill SHALL NOT 出现在任何 agent 的 L1 catalog 中，且针对它的按需加载请求 SHALL 被拒绝

#### Scenario: Model-invisible skill never discoverable

- **WHEN** 一个未绑定的 skill 的 `model_invocable=false`
- **THEN** 该 skill SHALL NOT 出现在发现池中，且按需加载 SHALL 被拒绝（hide-not-block，不出现在 catalog 中再拦截）

#### Scenario: Disabled plugin skill excluded from discovery

- **WHEN** 一个 plugin 来源的 skill 的所属 plugin 被禁用
- **THEN** 该 skill SHALL NOT 出现在发现池中；plugin 重新启用后 SHALL 重新可被发现

#### Scenario: Bundled skill discoverable across workspaces

- **WHEN** discovery 生效且 bundled 域存在一个 `model_invocable=true` 的 skill，同名无 workspace 级遮蔽行
- **THEN** 任意开启 discovery 的 workspace 中的 agent 的 L1 catalog SHALL 包含该 skill

### Requirement: Discovery activation governed by three layers

Skill 发现 SHALL 由三层开关逐级收窄：全局 settings（默认 `false`）→ workspace 级策略 → per-agent 覆盖。workspace 策略开启前，全局关闭 SHALL 使系统行为与本 capability 引入前完全一致（仅 绑定 ∪ auto_load）。per-agent 覆盖取值为三态：未设置（跟随 workspace 策略）、显式开启（仅当 workspace 已开启时生效）、显式退出（即使 workspace 开启也不参与发现）。全局开关关闭时，任何 workspace 或 agent 级设置 SHALL 无效。

#### Scenario: Global switch off preserves legacy behaviour

- **WHEN** 全局 settings 关闭且某 workspace 策略开启、某 agent 显式开启
- **THEN** 该 agent 的 L1 catalog SHALL 仅包含 绑定 ∪ auto_load，与本 capability 引入前一致

#### Scenario: Workspace opt-in enables discovery

- **WHEN** 全局 settings 开启、workspace 策略开启、agent 未显式设置覆盖
- **THEN** 该 agent 的 L1 catalog SHALL 包含发现池条目

#### Scenario: Agent opt-out wins over workspace policy

- **WHEN** 全局与 workspace 均开启，但某 agent 显式设置退出
- **THEN** 该 agent 的 L1 catalog SHALL 仅包含 绑定 ∪ auto_load，其按需加载 advertised 集合同样收窄

#### Scenario: Agent opt-in cannot exceed workspace policy

- **WHEN** 全局开启但 workspace 策略关闭，某 agent 显式设置开启
- **THEN** 该 agent SHALL NOT 获得发现池条目

### Requirement: Workspace-configurable trust-tier floor

Workspace 策略 SHALL 可配置发现池的最低 `trust_tier`，默认 `community`（即不过滤）。低于下限的 skill SHALL 被排除在发现池之外，但不影响其经显式绑定进入 agent。真实内容扫描门禁属于 5.13a，本 capability 的下限过滤 SHALL 仅基于现有 `trust_tier` 字段。

#### Scenario: Default floor admits community skills

- **WHEN** workspace 未配置 trust 下限且存在 `trust_tier='community'` 的未绑定 skill
- **THEN** 该 skill SHALL 进入发现池

#### Scenario: Configured floor excludes community tier

- **WHEN** workspace 配置下限为 `trusted` 且存在 `trust_tier='community'` 的未绑定 skill
- **THEN** 该 skill SHALL NOT 进入发现池，但显式绑定它的 agent 的 L1 catalog 仍 SHALL 包含它

### Requirement: Catalog budget with deterministic selection policy

发现池条目进入 L1 catalog 时 SHALL 受 catalog 预算约束（默认 2000 tokens）。预算溢出时，条目按以下优先级保留，同级内按下级规则排序：绑定 skill 与 auto_load skill 恒优先于发现条目；发现条目之间先按 `trust_tier`（official > trusted > community），再按 provider rank（project > user > bundled，plugin 行不参与 rank 比较），再按历史使用次数降序，最后按名称字典序（确定性兜底）。被挤出 catalog 的发现条目 SHALL 仅从 L1 catalog 省略；若其名被模型显式请求加载，按需加载路径 SHALL 独立判定（不受 catalog 省略影响）。

#### Scenario: Overflow drops lowest-priority discovery entries

- **WHEN** 绑定与 auto_load 条目之外，发现条目总量超出 catalog 预算
- **THEN** 发现条目 SHALL 按上述优先级保留至预算耗尽，其余从 L1 catalog 省略并记录日志

#### Scenario: Trust tier outranks usage count

- **WHEN** 一个 `official` 发现条目与一个使用次数更高的 `community` 发现条目竞争剩余预算的最后一个位置
- **THEN** SHALL 保留 `official` 条目

#### Scenario: Equal priority resolved deterministically by name

- **WHEN** 两个发现条目的 trust tier、provider rank 与使用次数完全相同
- **THEN** SHALL 按名称字典序保留，保证同一 agent 在相同数据下的 catalog 稳定

#### Scenario: Catalog-omitted skill remains loadable by name

- **WHEN** 一个发现条目因预算被挤出 L1 catalog，模型在后续轮次显式请求加载该名称
- **THEN** 按需加载 SHALL 按 L2 advertised 规则独立判定并允许成功

### Requirement: Run-scoped activation unchanged

自动发现的 skill 的激活作用域 SHALL 与绑定 skill 一致：按需加载的 L2 内容为 run-scoped context，SHALL NOT 永久追加进 system prompt。发现机制 SHALL NOT 引入跨 run 或跨 turn 的内容驻留。

#### Scenario: Loaded discovery content is run-scoped

- **WHEN** 模型在某个 run 中加载了一个自动发现的 skill 的全文
- **THEN** 该内容 SHALL 仅在该 run 内有效，后续 run 需重新加载

### Requirement: Usage telemetry carries detection provenance

skill 使用事件（catalog 服务、按需加载）SHALL 为每条事件记录 `detected_via` 来源标记：经显式绑定或 `auto_load` 路径服务的记 `bound`，经发现池服务的记 `auto_detected`。遥测写入失败 SHALL 按既有 best-effort 契约处理（仅回滚 savepoint，不影响主事务）。

#### Scenario: Discovered load marked auto_detected

- **WHEN** 模型加载了一个仅经发现池可达的 skill 的全文
- **THEN** 对应 usage 事件的 `detected_via` SHALL 为 `auto_detected`

#### Scenario: Bound load marked bound

- **WHEN** 模型加载了一个在 `agent.skills` 中的 skill 的全文
- **THEN** 对应 usage 事件的 `detected_via` SHALL 为 `bound`

### Requirement: Unmet requires on discovered load warns but serves

自动发现的 skill 携带 5.9e 的 `requires` 声明且存在不可解析的依赖时，按需加载 SHALL 照常服务 skill 内容，同时 SHALL 记录一条依赖告警事件（不阻断、不静默）。`requires` 的绑定期闭包校验语义 SHALL 不变（5.9e），发现路径不引入运行时依赖求解器。

#### Scenario: Discovered skill with missing dependency loads with warning

- **WHEN** 一个自动发现的 skill 声明 `requires` 了 workspace 中不存在的 skill，模型请求加载其全文
- **THEN** 系统 SHALL 服务该 skill 内容，并记录一条包含缺失依赖名的依赖告警事件

#### Scenario: Discovered skill with satisfied dependency loads silently

- **WHEN** 一个自动发现的 skill 的 `requires` 全部可解析，模型请求加载其全文
- **THEN** 系统 SHALL 服务该 skill 内容且不记录依赖告警
