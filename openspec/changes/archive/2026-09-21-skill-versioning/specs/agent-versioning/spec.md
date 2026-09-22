# Spec Delta

## MODIFIED Requirements

### Requirement: 引用清单与漂移检测

版本快照 SHALL 包含引用清单：工作流引用 pin 到 `(workflow_id, workflow_version)`（提交时该工作流的 `published_version`，未发布过则取其最高版本）。skills 为可版本化资源：提交时每个引用的 skill SHALL 经统一 precedence 仲裁（project > user > bundled）确定唯一活行，并在条目中记录 `(name, skill_id, provider, version, content_hash)` 五元组——`version` 为该 skill 提交时的最新快照版本，从未提交过版本的 skill 记 `version=null`；plugin 来源（provider 为 NULL）的 skill 条目记录 `version=null` 且不参与 pin。tools、knowledge_base_ids 等仍未版本化的资源 SHALL 继续记录 `(resource_id, version=null, content_hash)`。运行时解析快照时，`version` 非 null 的 skill 条目 SHALL 从对应 skill 版本快照提供内容，未 pin 条目（含 `version=null` 的 skill 条目与其他未版本化资源）SHALL 按当前内容实时解析。系统 SHALL 提供漂移查询：未 pin 条目按 content_hash 比对报告"快照提交后内容已变化"，`version` 非 null 的 pinned 条目 SHALL 不参与漂移报告。存量快照中的旧格式 skill 条目（无 `skill_id`/`version` 字段）SHALL 按未 pin 处理，继续实时解析。同名 skill 的仲裁结果 MUST NOT 依赖查询顺序或存储顺序。

#### Scenario: 工作流引用按发布版本 pin

- **WHEN** Agent A（mode=workflow，workflow_id=W）提交版本时 W 的 published_version=7、最高版本=9
- **THEN** 该快照 SHALL pin `(W, 7)`

#### Scenario: 提交时 skill pin 到最新版本

- **WHEN** Agent A 提交版本时，其引用的 skill S 最新版本为 v2
- **THEN** 该快照的 S 条目 SHALL 记录 `(name, S 的 skill_id, S 的 provider, 2, S v2 的 content_hash)`

#### Scenario: 从未提交版本的 skill 维持未 pin

- **WHEN** Agent A 提交版本时，其引用的 skill T 从未提交过版本
- **THEN** T 条目的 `version` SHALL 为 null，`content_hash` SHALL 取 T 活行现值

#### Scenario: pinned skill 内容冻结且退出漂移

- **WHEN** Agent A 的快照 pin skill S 的 v2，其后 S 活行内容变更
- **THEN** 运行时解析 A 时 S 的内容 SHALL 来自 v2 快照，且 A 的漂移查询 SHALL NOT 报告 S

#### Scenario: 未 pin 条目漂移仍可检测

- **WHEN** Agent A 的快照中 skill T 条目 `version=null`、`content_hash="h1"`，其后 T 活行内容变更（hash 变为 "h2"）
- **THEN** A 的漂移查询 SHALL 报告 T 已漂移，且运行时解析 A 时 T 按变更后内容实时生效

#### Scenario: 未版本化资源漂移可检测

- **WHEN** Agent A 的 v3 快照记录了未 pin 条目（version=null）的 content_hash="h1"，其后该资源内容变更（hash 变为 "h2"）
- **THEN** v3 的漂移查询 SHALL 报告该条目已漂移，且运行时解析 v3 时该条目按变更后内容实时生效

#### Scenario: 存量快照旧格式条目兼容

- **WHEN** 解析一个在本变更前提交的 agent 版本快照，其 skill 条目仅有 `(resource_id=name, version=null, content_hash)`
- **THEN** 该条目 SHALL 按未 pin 处理，运行时按活行实时解析，漂移查询照常比对

#### Scenario: 同名跨 provider 仲裁确定 pin 对象

- **WHEN** Agent A 提交版本时，workspace 内存在同名 `project` 与 `user` 两行 skill
- **THEN** 引用条目 SHALL 记录 `project` 行（precedence 胜者）的 `skill_id`、`provider`、`version` 与 `content_hash`，与加载时服务的行为一致
