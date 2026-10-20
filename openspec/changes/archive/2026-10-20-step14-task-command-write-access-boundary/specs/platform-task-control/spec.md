# Spec Delta

## ADDED Requirements

### Requirement: 任务控制命令携带最低写角色门槛

任务控制命令的 REST 写入入口(`POST /api/tasks/{task_id}/commands`、`POST /api/tasks/{task_id}/cancel`)SHALL 在记录或重放任何命令之前施加两级权限门:先校验目标 Task 存在、未软删除且属于调用方当前 workspace——跨 workspace、已删除与不存在的 Task SHALL 返回一致的 404,SHALL NOT 泄露具体原因;再要求调用方在其当前 workspace 具备至少 `EDITOR` 角色,角色不足或缺失 SHALL 返回 403。权限判定 SHALL 取自服务端解析的 `AuthContext` 与既有角色校验构件,SHALL NOT 接受请求体自报的角色;角色门 SHALL 先于命令存储访问、幂等回放、后端调用与任何状态迁移,已降权调用方凭既有 `command_id` 的重放 SHALL 同样被拒绝且既有回执保持不变。拒绝时 SHALL 零副作用:不产生命令回执,Task/Run 状态、outbox 与后端调用 SHALL NOT 发生任何变化。

缺 workspace 的非 system 身份 SHALL 维持既有 403 拒绝,SHALL NOT 退化为全局查询;显式 system-scope 身份的既有行为 SHALL 保持不变,SHALL NOT 因本门槛获得或失去跨 workspace 命令能力。既有审批主体、wait token、`expected_revision`、期限、幂等键绑定与回执状态语义 SHALL 全部保持——`EDITOR` 门槛是新增的最低角色要求,SHALL NOT 替代或弱化原审批校验。服务层的可信内部调用路径(非 REST)SHALL 保持既有签名兼容;两个 REST 入口 SHALL 始终经过本门槛。

#### Scenario: 跨 workspace 命令表现为不存在

- **WHEN** workspace W2 的成员对 workspace W1 中的 Task 发送控制命令或取消请求
- **THEN** 响应为 404,与请求一个不存在的 Task 一致;W1 的 Task 状态、命令回执与 outbox 无任何变化

#### Scenario: 软删除与不存在一致

- **WHEN** 对已软删除的 Task 发送控制命令
- **THEN** 响应与不存在的 Task 一致(404),不区分两种情形

#### Scenario: 同 workspace viewer 被拒

- **WHEN** workspace W1 的 viewer 角色成员对 W1 内 Task 发送控制命令或取消请求
- **THEN** 响应为 403 授权拒绝,命令回执、Task/Run 状态、outbox 与后端调用均不变

#### Scenario: editor 与 admin 可正常下发

- **WHEN** workspace W1 的 EDITOR(或 ADMIN)成员对 W1 内 Task 发送控制命令或取消请求
- **THEN** 请求按既有语义处理(REQUESTED 回执、审批/revision/期限语义不变),行为与门槛引入前一致

#### Scenario: 无角色或缺 workspace 身份被拒

- **WHEN** 有效认证但在当前 workspace 无任何角色的身份,或无 workspace 归属的非 system 身份发送控制命令
- **THEN** 响应为 403 授权拒绝,SHALL NOT 退化为全局查询或绕过角色门

#### Scenario: 降权后旧命令 ID 重放被拒

- **WHEN** 某 `command_id` 曾由 EDITOR 成功下发,该用户随后被降权为 viewer,再以相同 `command_id` 与相同请求体重发
- **THEN** 响应为 403 授权拒绝(角色门先于幂等回放),既有回执内容与状态保持不变

#### Scenario: 拒绝零副作用

- **WHEN** 任一被拒请求(404 或 403)处理完成
- **THEN** 命令存储无新记录、无重放,Task/Run 状态不变,outbox 无新条目,后端无调用

#### Scenario: system scope 行为保持

- **WHEN** 缺 workspace 的非 system 身份或既有请求路径触达命令写入口
- **THEN** 行为与本门槛引入前一致(缺 workspace 维持 403),system-scope 身份不因本门槛新增跨 workspace 命令能力

#### Scenario: 门槛不替代审批语义

- **WHEN** EDITOR 成员对需要审批或携带 `expected_revision`/期限的 Task 下发命令
- **THEN** 原审批绑定、revision 冲突、期限与幂等键绑定语义按既有规则执行,EDITOR 角色不构成对这些校验的豁免
