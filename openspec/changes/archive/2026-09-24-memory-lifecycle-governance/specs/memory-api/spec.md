# memory-api Specification(Delta)

## ADDED Requirements

### Requirement: 记忆编辑审计查询

系统 SHALL 提供记忆编辑审计的查询端点:按 workspace 过滤,支持按 agent、记忆类型(L1/L3/L4)、操作来源(agent 工具 / consolidation / 生命周期)、时间范围过滤与分页,返回条目含目标记忆标识、操作类型、来源、revision 与时间戳。

#### Scenario: 查询某 agent 的编辑历史

- **WHEN** 管理者以 `agent_id` 与时间范围查询编辑审计
- **THEN** 返回该 agent 在范围内全部记忆编辑记录(含 agent 工具、consolidation、生命周期来源),按时间倒序分页

### Requirement: 整合与反思运行查询

系统 SHALL 提供整合与反思运行的查询端点:列出 `consolidation_runs`(及 `reflection_runs`)支持按单元、trigger 类型、状态与时间范围过滤,返回含触发来源、窗口水位、状态与统计(候选数 / 应用数 / 失败数)。

#### Scenario: 查看最近整合运行

- **WHEN** 管理者查询某整合单元的最近运行
- **THEN** 返回该单元按时间倒序的运行列表,含 trigger、窗口与统计,失败运行可见失败原因

### Requirement: 记忆策略管理端点

系统 SHALL 提供记忆策略的 CRUD 端点:workspace 级策略与 agent 级覆盖分别寻址;写入经 `memory-policy` capability 定义的校验(未知工具名、超上限数值、越权权限面配置拒绝);端点 SHALL 同时返回某 scope 的解析生效值(resolved view)。

#### Scenario: 创建并查看生效值

- **WHEN** 管理者为 workspace 创建策略后查询某 agent 的 resolved view
- **THEN** 返回该 agent 生效链(platform → workspace → agent)收敛后的全部字段值,并标明每字段来源层级

#### Scenario: 越权配置被拒

- **WHEN** 提交的 agent 级覆盖试图将共享上限扩至超过 workspace 级
- **THEN** 请求被拒绝,错误指明收敛规则违反

### Requirement: 记忆 archive 与恢复端点

系统 SHALL 提供记忆 archive(软删)与恢复端点,覆盖 L3 与 L4 记忆;并 SHALL 提供archived 记忆的列表查询(含淘汰原因与时间)。archive 与恢复 SHALL 记入 `memory_edit_log`。

#### Scenario: archive 并列出

- **WHEN** 管理者 archive 一条 L3 记忆后查询 archived 列表
- **THEN** 该记忆出现在 archived 列表且标注原因(人工 archive / TTL 过期 / 容量淘汰),检索路径不再返回它

#### Scenario: 恢复

- **WHEN** 管理者对 archived 记忆调用恢复端点
- **THEN** 该记忆重新参与检索,操作记入编辑审计

### Requirement: 生命周期统计端点

系统 SHALL 提供记忆生命周期的统计端点:按 workspace 返回各层记忆计数、archived 计数(按原因分布)、最近清扫与整合运行摘要、TTL 配置概览。

#### Scenario: 治理概览

- **WHEN** 管理者打开 Memory Center 概览
- **THEN** 页面经该端点取得各层计数与 archive 分布,数据与实际表内容一致

### Requirement: 召回对话检索端点

系统 SHALL 提供会话召回(recall)的检索端点,能力与 `conversation_search` 工具对齐:按查询语义检索、支持时间窗口、角色过滤、游标分页与 `exclude_session_ids` 排除,遵循既有 recall 层的 workspace 隔离。

#### Scenario: UI 内检索历史对话

- **WHEN** 管理者在 Memory Center 的召回页输入查询并设定时间窗口
- **THEN** 返回召回命中列表(会话、时间、片段),仅含本 workspace 可见会话

### Requirement: 治理端点权限与隔离

全部新增治理端点 SHALL 复用既有认证依赖与 workspace 隔离语义(`workspace_id` 由认证上下文解析,不取自请求参数);治理端点要求 `editor` 或 `admin` 角色;actor 级用户私有记忆的浏览与检索仅对记忆所属用户与 `admin` 角色开放。

#### Scenario: 非 editor 不可访问治理端点

- **WHEN** 仅具备 viewer 角色的用户调用治理端点
- **THEN** 请求被拒绝并返回权限错误

#### Scenario: 私有记忆隔离

- **WHEN** 非 admin 用户检索其他 actor 的用户记忆
- **THEN** 结果为空(或明确拒绝),不泄露其他 actor 的私有事实
