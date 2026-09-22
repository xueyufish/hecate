## Purpose

为 skill 提供资产级不可变版本快照与完整生命周期（提交/回滚/diff/删除约束/状态），承接 agent 版本化（1.3.20）对 skill 的引用 pin，并为后续依赖声明（5.9e）提供版本语义底座。语义承诺为内容不可变（字节级快照），不承诺行为不可变（模型与加载器演进可能改变同字节内容的行为）。

## Requirements

### Requirement: 提交创建不可变内容快照

系统 SHALL 提供 skill 版本「提交」动作：将该 skill 活行的 `name`、`instructions`、`allowed_tools`、`scripts`、`references`、`description`、`max_tokens` 冻结为一条不可变版本记录，版本号为该 skill 内单调递增整数。提交后对活行内容的任何编辑 SHALL NOT 影响已存在的版本快照。治理字段（`auto_load`、`model_invocable`、`user_invocable`、`provider`、`trust_tier`、`metadata`）SHALL NOT 进入快照，始终按活行解析。"存在未提交变更"状态 SHALL 仅由冻结字段的哈希比对推导。存量 skill SHALL NOT 在迁移时自动产生版本；首个版本由首次显式提交创建。plugin 来源（`provider` 为 NULL）的 skill SHALL 被排除在版本化之外，提交请求 SHALL 被拒绝并说明原因。

#### Scenario: 提交后的编辑不影响快照

- **WHEN** skill S 提交版本 v1（instructions="I1"）后，编辑活行 instructions="I2"
- **THEN** v1 快照中的 instructions SHALL 仍为 "I1"，且 S SHALL 展示"存在未提交变更"状态

#### Scenario: 冻结边界外的字段不参与脏判定

- **WHEN** skill S 存在版本快照，随后仅修改 `auto_load` 或 `model_invocable`
- **THEN** S 的"存在未提交变更"状态 SHALL 为 false，且运行时对该 skill 的调用策略 SHALL 立即按新值生效

#### Scenario: 版本号单调递增

- **WHEN** skill S 依次提交 v1、v2 后再次提交
- **THEN** 新版本号 SHALL 为 3，且同一 skill 内版本号 SHALL 唯一

#### Scenario: plugin 来源 skill 拒绝提交

- **WHEN** 对 `provider` 为 NULL 的 plugin 来源 skill 请求提交版本
- **THEN** 请求 SHALL 被拒绝，错误信息说明 plugin skill 的生命周期归插件包、不支持手动版本化

### Requirement: 内容哈希与引用清单可比

版本记录 SHALL 存储 `content_hash`，其算法与字段集（`name`、`instructions`、`allowed_tools`、`scripts`、`references`）SHALL 与 skill 活行 `content_hash` 及 agent 版本引用清单完全一致。同一内容在活行与快照上 SHALL 产生相同哈希值。

#### Scenario: 提交时快照哈希与活行一致

- **WHEN** skill S 的活行 `content_hash` 为 "h1"，提交版本 v1
- **THEN** v1 记录的 `content_hash` SHALL 为 "h1"

### Requirement: 版本列表、详情与状态

系统 SHALL 提供 skill 的版本列表（版本号、名称、说明、创建者、创建时间）与单版本详情（含完整快照），以及版本名称与说明的编辑（仅元数据，SHALL NOT 触碰快照内容）。skill 读取面 SHALL 暴露 `latest_version`（可为空）与 `has_uncommitted_changes`。指定不存在的版本 SHALL 返回明确错误。

#### Scenario: 从未提交的 skill 状态

- **WHEN** skill S 从未提交过版本
- **THEN** S 的 `latest_version` SHALL 为空，`has_uncommitted_changes` SHALL 为 true

#### Scenario: 回滚或提交后脏标记归零

- **WHEN** skill S 提交 v2 后未再编辑内容
- **THEN** S 的 `has_uncommitted_changes` SHALL 为 false

### Requirement: 版本 diff

系统 SHALL 提供同 skill 任意两个版本间的 diff，覆盖全部冻结字段的变化。

#### Scenario: 字段级差异可见

- **WHEN** v1 的 instructions 与 v2 不同、其余冻结字段相同
- **THEN** diff 结果 SHALL 报告 instructions 的变化，且不报告未变化字段

### Requirement: 回滚以新版本实现并写回活行

回滚 SHALL 以目标版本的内容新建一个版本（新版本号），并将该内容写回 skill 活行；写回 SHALL 即时生效，语义与编辑一致。回滚 SHALL NOT 改变任何 agent 版本快照及其 pin 关系。

#### Scenario: 回滚后活行内容等于目标版本

- **WHEN** skill S 活行内容已前进，执行回滚到 v1
- **THEN** 系统 SHALL 新建版本（内容等于 v1），活行的冻结字段 SHALL 全部等于 v1 快照内容

#### Scenario: 回滚不破坏已有 pin

- **WHEN** agent 版本 A 已 pin skill S 的 v1，S 活行前进后执行回滚到 v1
- **THEN** A 的引用条目 SHALL 保持 pin v1，且运行时解析 A 时提供的 S 内容不变

### Requirement: 删除约束与快照存续

软删 skill 活行 SHALL NOT 级联删除其版本记录。被任一非删除 agent 版本的引用清单以 `version` 非 null 方式 pin 的 skill 版本 SHALL 禁止删除；其余版本可删除。被 pin 的 skill 版本内容 SHALL 在其源活行被软删后仍可被解析。

#### Scenario: 源删除后 pinned 内容仍可解析

- **WHEN** agent 版本 A pin skill S 的 v2，随后 S 活行被软删
- **THEN** 解析 A 时 SHALL 仍能从 v2 快照提供 S 的内容

#### Scenario: 被引用版本删除被拒

- **WHEN** 对被 agent 版本 A pin 的 skill S v2 请求删除
- **THEN** 删除 SHALL 被拒绝并说明引用来源；未被任何 agent 版本 pin 的版本 SHALL 可删除

### Requirement: 自演进发布自动成版

自演进管线发布 learned skill 写入活行时，系统 SHALL 自动提交一个版本：创建者记录为系统，版本记录 SHALL 关联该次演进的 `learned_run_id`。自动提交 SHALL 发生在演进门禁审查通过之后。

#### Scenario: 演进发布产生可审计版本

- **WHEN** 一次通过门禁审查的演进发布更新了 learned skill 的内容
- **THEN** 系统 SHALL 产生新版本，其 `created_by` 为系统、`learned_run_id` 为该次演进运行，无需人工提交

### Requirement: pinned 内容解析契约

当 agent 版本快照以 `version` 非 null pin 某 skill 时，运行时按该快照解析该 skill SHALL 从被 pin 的版本快照提供内容（含按快照 `max_tokens` 的截断语义），SHALL NOT 使用 skill 活行当前内容；pinned 加载 SHALL 精确指向被 pin 的版本记录，SHALL NOT 再做同名 precedence 仲裁。`version` 为 null 的 skill 条目 SHALL 维持活行实时解析。

#### Scenario: pinned 解析冻结于历史内容

- **WHEN** agent 版本 A pin skill S 的 v2，其后 S 活行内容前进
- **THEN** 运行时按 A 解析 S 时注入的内容 SHALL 来自 v2 快照，与活行无关
