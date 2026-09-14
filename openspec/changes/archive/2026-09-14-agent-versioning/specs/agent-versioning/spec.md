## Purpose

为 Agent 提供定义级别的不可变版本快照与完整生命周期（提交/发布/回滚/diff），使外部渠道可以绑定到配置冻结的版本，并为 Resource Versioning（14.x）建立引用清单与解析接缝这一机制底座。语义承诺为配置不可变（不承诺行为不可变——模型权重可能在同一模型 ID 后更新）。

## ADDED Requirements

### Requirement: 显式提交创建不可变版本快照

系统 SHALL 提供「提交版本」动作：将 Agent 活行（草稿）的自有配置字段（persona、model_config、mode、workflow_id、tools、skills、skill_ids、knowledge_base_ids、risk_level、opening_remarks、enable_suggestions、guardrail_config）冻结为一条不可变版本记录，版本号为该 Agent 内单调递增整数。提交后对草稿的任何编辑 SHALL NOT 影响已存在的版本快照。系统 SHALL 能从 Agent 与版本记录推导出"存在未提交变更"状态。

#### Scenario: 提交后的编辑不影响快照

- **WHEN** Agent A 提交版本 v3（persona="P1"）后，编辑草稿 persona="P2"
- **THEN** v3 快照中的 persona SHALL 仍为 "P1"，且 Agent A SHALL 展示"存在未提交变更"状态

#### Scenario: 版本号单调递增且唯一

- **WHEN** Agent A 依次提交 v3、v4 后再次提交
- **THEN** 新版本号 SHALL 为 5，且同一 Agent 内版本号 SHALL 唯一

### Requirement: 引用清单与漂移检测

版本快照 SHALL 包含引用清单：工作流引用 pin 到 `(workflow_id, workflow_version)`（提交时该工作流的 `published_version`，未发布过则取其最高版本）；tools、skills、knowledge_base_ids 等未版本化资源 SHALL 记录 `(resource_id, version=null, content_hash)`。运行时解析快照时，未 pin 的资源 SHALL 按当前内容实时解析；系统 SHALL 提供漂移查询，报告"某资源在快照提交后内容已变化"（按 content_hash 比对）。

#### Scenario: 工作流引用按发布版本 pin

- **WHEN** Agent A（mode=workflow，workflow_id=W）提交版本时 W 的 published_version=7、最高版本=9
- **THEN** 该快照 SHALL pin `(W, 7)`

#### Scenario: 未版本化资源漂移可检测

- **WHEN** Agent A 的 v3 快照记录了 skill S 的 content_hash="h1"，其后 S 的内容变更（hash 变为 "h2"）
- **THEN** v3 的漂移查询 SHALL 报告 S 已漂移，且运行时解析 v3 时 S 按变更后内容实时生效

### Requirement: 发布与发布门禁挂钩

系统 SHALL 提供「发布」动作：将指定已提交版本设为该 Agent 的 `published_version` 指针（初始为空）。发布可配置评估门禁（复用 7.3a 机制）：未配置时发布直接生效；`warn` 模式 SHALL 计算并报告门禁结果但不阻断；`require` 模式在任何启用信号失败时 SHALL 拒绝发布。门禁 v1 默认关闭。

#### Scenario: 发布移动指针

- **WHEN** Agent A 发布版本 v3
- **THEN** Agent A 的 published_version SHALL 变为 3，且不影响后续提交的新版本

#### Scenario: require 门禁阻断发布

- **WHEN** Agent A 配置 require 门禁且启用信号失败，尝试发布 v4
- **THEN** 发布 SHALL 被拒绝并返回门禁失败详情，published_version 保持不变

### Requirement: 回滚以新建版本实现

回滚 SHALL 以目标版本的内容新建一个版本（新版本号），SHALL NOT 直接改变 `published_version`；回滚产生的版本必须经显式发布才会对外生效。

#### Scenario: 回滚产生新草稿版本

- **WHEN** Agent A 当前 published_version=5，对其执行回滚到 v3
- **THEN** 系统 SHALL 新建版本 v6（内容等于 v3），published_version SHALL 仍为 5，且 v6 在发布前对外不可见

### Requirement: 版本 diff 与版本列表

系统 SHALL 提供同 Agent 任意两个版本间的 diff（覆盖自有字段与引用清单变化），以及版本列表（版本号、名称、发布说明、创建者、创建时间、是否为当前发布版）；版本名称与发布说明可编辑。

#### Scenario: diff 报告字段与引用变化

- **WHEN** 对 Agent A 的 v3 与 v4 执行 diff，v4 修改了 persona 且将 workflow W 从 v7 pin 到 v8
- **THEN** diff 结果 SHALL 同时包含 persona 的新旧值与工作流 pin 的新旧指向

### Requirement: 版本不可变与删除约束

已创建的版本快照内容 SHALL 不可修改。已发布版本（`published_version` 指向的版本）SHALL 禁止删除；被任一渠道以 pinned 模式绑定的版本 SHALL 禁止删除；其余历史版本可删除。

#### Scenario: 删除被 pin 的版本被拒绝

- **WHEN** 渠道 C 以 pinned 模式绑定 Agent A 的 v2，尝试删除 v2
- **THEN** 删除 SHALL 被拒绝并提示绑定该版本的渠道

### Requirement: 版本解析接缝

系统 SHALL 提供统一的生效配置解析：`resolve(agent_id, version=None)` 返回草稿（活行）配置；`resolve(agent_id, version=n)` 返回 v n 快照——自有字段取快照，工作流按 pin 的 `(workflow_id, workflow_version)` 加载，未 pin 资源按当前内容解析。studio 编辑器内的聊天与试跑 SHALL 使用草稿解析。指定不存在的版本 SHALL 返回明确错误。

#### Scenario: studio 试跑走草稿

- **WHEN** 在 studio 编辑器内对 Agent A 发起试跑，草稿与已发布版本配置不同
- **THEN** 试跑 SHALL 按草稿配置执行

#### Scenario: 指定版本解析使用 pin 的工作流版本

- **WHEN** `resolve(A, version=3)`，v3 pin `(W, 7)`，而 W 的最高版本已是 9
- **THEN** 解析结果 SHALL 加载 W 的版本 7 的图定义
