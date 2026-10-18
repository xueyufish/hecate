# Spec Delta

## ADDED Requirements

### Requirement: 版本生命周期归属校验

Agent 版本生命周期的全部端点（版本列表、版本详情、skill 闭包、漂移报告、发布历史、当前发布版本、版本 diff、版本状态、提交、元数据更新、删除、发布、回滚）SHALL 在读取或变更任何状态之前按 workspace 归属校验调用方：非 system-scope 身份访问不属于其活跃 workspace 的 Agent 时，SHALL 表现为该 Agent 不存在——响应与 Agent 确实不存在时一致（not found），SHALL NOT 泄露该 Agent 或其版本的存在性、数量或内容。缺少 workspace 归属的非 system-scope 身份 SHALL 视为无归属，同样表现为 not found 或授权拒绝，SHALL NOT 获得跨 workspace 访问能力。仅显式 system-scope 身份可跨 workspace 操作。归属校验 SHALL 在服务层执行，使未来非 HTTP 入口（如 MCP 工具）复用同一判定；既有生命周期方法省略身份的调用保留给可信内部路径（运行时版本解析等），SHALL NOT 构成对外端点绕过校验的方式；发布历史查询保留原有缺失身份即拒绝的约束，内部调用也须显式提供身份。

#### Scenario: 跨 workspace 读取表现为不存在

- **WHEN** workspace W2 的成员请求 workspace W1 中某 Agent 的版本列表或任一版本详情
- **THEN** 响应为 not found（404），与请求一个不存在的 Agent ID 时响应一致；W1 该 Agent 的版本数据不出现在任何字段中

#### Scenario: 跨 workspace 写入不改变任何状态

- **WHEN** workspace W2 的成员对 workspace W1 中某 Agent 执行提交、元数据更新、删除、发布或回滚
- **THEN** 响应为 not found（404），且 W1 该 Agent 的版本列表、发布指针、发布凭据与版本内容全部保持不变

#### Scenario: 缺少 workspace 的非系统身份被拒绝

- **WHEN** 携带有效凭证但无活跃 workspace 归属的非 system-scope 身份请求任一版本生命周期端点
- **THEN** 请求被拒绝（not found 或 403），响应不因缺失归属而退化为全库可见

#### Scenario: system scope 显式跨 workspace

- **WHEN** 显式 system-scope 身份请求其他 workspace 中 Agent 的版本生命周期端点
- **THEN** 请求按该端点既有语义正常处理，归属校验不构成额外限制

#### Scenario: 运行时解析路径不受影响

- **WHEN** 通道投递、评测引擎或工作流执行按既有逻辑解析 Agent 的已发布/指定版本
- **THEN** 版本解析行为与语义保持不变，不要求调用方提供身份上下文

### Requirement: 版本生命周期角色门槛

版本生命周期端点 SHALL 在归属校验通过后执行最低角色校验：只读端点（列表、详情、skill 闭包、漂移、发布历史、当前发布版本、diff、状态）SHALL 要求调用方在其活跃 workspace 中至少具备 VIEWER 角色；变更端点（提交、元数据更新、删除、发布、回滚）SHALL 要求至少 EDITOR 角色——回滚以新建版本实现，SHALL 按变更端点对待。角色不足时 SHALL 返回授权拒绝（403），且对应资源的状态 SHALL NOT 发生任何改变。无 workspace 成员身份（缺 workspace 或缺角色）的非 system-scope 调用方 SHALL 被拒绝。显式 system-scope 身份 SHALL 豁免角色门槛。发布入口在既有的 workspace 隔离与发布门禁语义之上 SHALL 适用同一 EDITOR 门槛；发布历史查询在既有 workspace 隔离语义之上 SHALL 适用同一 VIEWER 门槛，二者既有判定 SHALL 保持不变。

#### Scenario: 同 workspace viewer 可读

- **WHEN** workspace W1 的 VIEWER 角色成员请求 W1 内某 Agent 的版本列表、版本详情、漂移报告、diff、版本状态或发布历史
- **THEN** 请求成功返回，内容与更高角色看到的完全一致

#### Scenario: viewer 写入被拒且状态不变

- **WHEN** workspace W1 的 VIEWER 角色成员对 W1 内某 Agent 执行提交、元数据更新、删除、发布或回滚
- **THEN** 响应为 403 授权拒绝，该 Agent 的版本列表、发布指针与版本内容保持不变

#### Scenario: editor 与 admin 写入放行

- **WHEN** workspace W1 的 EDITOR（或 ADMIN）角色成员对 W1 内某 Agent 执行提交、元数据更新、删除、发布或回滚
- **THEN** 请求按该端点既有语义正常处理，不因角色校验产生额外拒绝

#### Scenario: 无成员身份被拒绝

- **WHEN** 有效认证但在其活跃 workspace 中无任何角色（或缺 workspace 归属）的非 system-scope 身份请求任一版本生命周期端点
- **THEN** 响应为 403 授权拒绝或 not found，不返回版本数据

#### Scenario: 发布与历史入口维持既有判定

- **WHEN** 边界生效后，成员身份执行发布（含 require 门禁阻断、force 绕过留审计）或查询发布历史（含 workspace 隔离、不重算门禁）
- **THEN** 判定结果与既有语义一致：门禁照常阻断或放行、凭据照常同事务落库、跨 workspace 历史查询仍为 not found；新增的仅是发布入口的 EDITOR 门槛与历史查询的 VIEWER 门槛


#### Scenario: 发布历史省略身份不降低既有约束

- **WHEN** 服务层调用发布历史查询而未提供 auth_context
- **THEN** 沿用原查询缺失 workspace 身份时的 not found 拒绝，不将其解释为可信内部全局读取；内部调用也须显式提供身份

#### Scenario: viewer force 发布仍被拒绝

- **WHEN** 同 workspace 的 VIEWER 使用 force 请求发布
- **THEN** 返回 403，force 不豁免 EDITOR 门槛，版本、指针和凭据不变
