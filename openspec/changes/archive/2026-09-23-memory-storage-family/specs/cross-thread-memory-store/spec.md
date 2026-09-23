# cross-thread-memory-store Specification(Delta)

## Purpose

把工作记忆区中的事实记忆(L3 用户记忆 + L4 知识记忆)的 namespace 模型从两层(`workspace_id + user_id`)扩展为四层(`workspace_id + team_id + actor_id + session_id`),使工作空间内不同用户/团队间的记忆可受控共享与检索,采用主流的多租户 namespace 隔离模式。

## ADDED Requirements

### Requirement: 四层 namespace 模型

系统 SHALL 把事实记忆的隔离与共享模型从两层 `workspace_id + user_id` 扩展为四层 `workspace_id + team_id + actor_id + session_id`:

- `workspace_id`:第一层隔离,所有事实记忆必须携带;跨 workspace 不可达。
- `team_id`(可空):第二层共享分组;同一 team 内的 actor 可共享 team-scope 记忆;`null` 表示 actor-only 不共享。
- `actor_id`(可空):第三层用户/智能体标识;`null` 表示 workspace-level 事实(无用户属性)。
- `session_id`(可空):第四层会话标识;`null` 表示跨 session 持久事实。

存储层 SHALL 在 `memories` / `knowledge_memories` 表新增 `team_id` 与 `actor_id` 列(可空);既有数据 SHALL 通过回填 `actor_id` 默认为既有 `user_id`、`team_id = null`。

#### Scenario: 四层 namespace 查询

- **WHEN** 一个跨 thread 检索请求携带 `workspace_id=X / team_id=Y / actor_id=Z / session_id=null`
- **THEN** 存储层返回所有 `workspace_id=X` 且 `(team_id=Y 或 null) 且 (actor_id=Z 或 null) 且 (session_id=null 或包含)` 的事实记忆

#### Scenario: 既有数据回填

- **WHEN** 平台升级到本变更
- **THEN** 所有既有 `memories` / `knowledge_memories` 行的 `actor_id` 默认填既有 `user_id` 值,`team_id = null`
- **AND** 回填不影响在线检索行为

### Requirement: Cross-Thread 共享语义

系统 SHALL 支持 workspace 级共享事实记忆(cross-thread),即:任何标记为 `actor_id=null / session_id=null` 的事实记忆 SHALL 对该 workspace 内所有用户/team 可检索。

- workspace 级事实 SHALL 通过显式 API 写入(不通过 `memory_add` 默认行为);`memory_add` 默认走 actor-scope,需显式 `scope='workspace_overwrite'` 升级为 workspace 级。
- workspace 级事实 SHALL 仅 workspace admin 角色可写;editor 角色 SHALL 收到结构化错误。
- workspace 级事实 SHALL 在检索结果中明确标注来源 scope(`workspace_shared`),与 actor-scope 记忆(`actor_scoped`)区分。

#### Scenario: workspace admin 写 workspace 级事实

- **WHEN** workspace admin 角色调用 `memory_add(content='报销政策 v2', scope='workspace_overwrite')`
- **THEN** 该事实以 `team_id=null`, `actor_id=null`, `session_id=null` 写入,所有用户检索可见
- **AND** 检索结果标注 `scope='workspace_shared'`

#### Scenario: 非 admin 写 workspace 级失败

- **WHEN** editor 角色调用 `memory_add(content='...', scope='workspace_overwrite')`
- **THEN** 返回结构化错误(无权限升级 scope),actor-scope 写入不发生

#### Scenario: 检索结果标注 scope

- **WHEN** Agent 调用 `memory_search(query='报销政策')`
- **THEN** 结果集包含 actor-scope 记忆与 workspace_shared 记忆,每条结果标注 `scope` 字段

### Requirement: 跨 Team 共享与隔离

系统 SHALL 支持同一 workspace 内不同 team 的受控共享:

- `team_id='T1'` 的事实 SHALL 对 `T1` 内所有 actor 可检索;对其他 team actor 不可达。
- `team_id=null` 的事实 SHALL 对 workspace 内所有 actor 可检索(即 workspace 级)。
- actor-level 事实(`actor_id=A`)SHALL 仅 actor A 可检索。

#### Scenario: 同 team 内可检索

- **WHEN** team T1 的 actor A1 写入 `team_id='T1'` 记忆
- **WHEN** team T1 的 actor A2 调用 `memory_search(query=...)`
- **THEN** A2 的检索结果包含 A1 写入的 team-scope 记忆

#### Scenario: 跨 team 不可达

- **WHEN** team T1 的 actor A1 写入 `team_id='T1'` 记忆
- **WHEN** team T2 的 actor B1 调用 `memory_search(query=...)`
- **THEN** B1 的检索结果不包含 A1 的记忆,行为与现状一致

### Requirement: namespace 维度校验

系统 SHALL 在每次 fact CRUD 时校验 namespace 维度的合法性:

- `workspace_id` 必须存在;缺失 SHALL 抛错。
- `team_id` 与 `actor_id` 至少有一个为 null;`team_id != null 且 actor_id != null` SHALL 抛错(避免歧义 scope)。
- `session_id` 可空,但若非空 SHALL 关联到该 workspace 内的有效 session。

#### Scenario: 非法 namespace 拒绝写入

- **WHEN** 调用 `memory_add(content='...', team_id='T1', actor_id='A1')`
- **THEN** 返回结构化错误(歧义 scope),写入不发生

#### Scenario: 缺失 workspace_id 拒绝

- **WHEN** 调用方不传 `workspace_id`
- **THEN** 返回结构化错误(workspace_id required),写入不发生

### Requirement: Cross-Thread 检索融合排序

系统 SHALL 把跨 thread / 跨 team / 跨 workspace 共享的检索结果与 actor-scope 检索结果按既有 4.14 / 4.15 的多信号融合排序统一处理:

- 共享记忆 SHALL NOT 自动获得 priority boost;按既有 score 公式参与排序。
- workspace_shared 记忆 SHALL 携带 `source_scope` 元数据(便于 Agent 解读)。
- 既有融合公式不变,新增 scope 维度不影响 score 计算(只是 metadata 字段)。

#### Scenario: 共享记忆不抢前排

- **WHEN** 检索 query 在 workspace_shared 与 actor_scoped 各命中一条,actor_scoped 相关性 0.9,workspace_shared 相关性 0.5
- **THEN** 融合排序后 actor_scoped 排在 workspace_shared 之前(共享记忆无优先级加成)