# Spec Delta

## ADDED Requirements

### Requirement: Task 观察读取携带最低读角色门槛

平台 Task 观察的 REST 读取入口(`GET /api/tasks`、`GET /api/tasks/{task_id}`、`GET /api/tasks/{task_id}/runs`)SHALL 在进入业务读取之前施加读权限门:

- `GET /api/tasks/{task_id}` 与 `GET /api/tasks/{task_id}/runs` SHALL 先校验目标 Task 存在、未软删除且属于调用方当前 workspace——跨 workspace、已删除与不存在的 Task SHALL 返回一致的 404(响应 message SHALL 仅回显调用方提供的 task_id),SHALL NOT 泄露具体原因;再要求调用方在其当前 workspace 具备至少 `VIEWER` 角色,角色不足或缺失 SHALL 返回 403。
- `GET /api/tasks` SHALL 先要求有效 workspace 与至少 `VIEWER` 角色,再进入列表查询;分页、`lifecycle_state` 过滤与 `unrecorded` 投影语义 SHALL 保持不变。

权限判定 SHALL 取自服务端解析的 `AuthContext` 与既有角色校验构件,SHALL NOT 接受自报角色;可见性判定 SHALL 先于角色判定,角色门 SHALL 先于列表/详情业务查询、durable 状态读取、派发与 relay。拒绝时 SHALL 零副作用:Task/Run 行、durable 状态与 outbox SHALL NOT 发生任何变化。缺 workspace 的非 system 身份 SHALL 维持既有 403 `NO_WORKSPACE` 拒绝,SHALL NOT 退化为全局查询;显式 system-scope 身份的既有行为 SHALL 保持不变,SHALL NOT 因本门槛获得或失去跨 workspace 读取能力。VIEWER、EDITOR 与 ADMIN 的成功响应结构 SHALL 与门槛引入前完全一致。

#### Scenario: 跨 workspace Task 观察表现为不存在

- **WHEN** workspace W2 的成员请求 workspace W1 中某 Task 的详情或 Run 列表
- **THEN** 两个入口均为 404,与请求一个不存在的 task_id 一致(message 仅回显调用方提供的 task_id);W1 的 Task 行、durable 状态与 Run 列表均无变化

#### Scenario: 软删除与不存在一致

- **WHEN** 对已软删除的 Task 请求详情或 Run 列表
- **THEN** 响应与不存在的 task_id 一致(404),不区分两种情形

#### Scenario: 同 workspace viewer 可读

- **WHEN** workspace W1 的 viewer 角色成员请求任务列表、W1 内 Task 的详情或 Run 列表
- **THEN** 三个入口均成功,响应结构、分页、状态过滤、`unrecorded` 投影、等待信息与 Run 排序与更高角色完全一致

#### Scenario: 无角色或缺 workspace 身份被拒

- **WHEN** 有效认证但在当前 workspace 无任何角色的身份请求任一观察入口,或无 workspace 归属的非 system 身份请求任一观察入口
- **THEN** 响应为 403 授权拒绝(缺 workspace 维持既有 `NO_WORKSPACE`),SHALL NOT 退化为全局查询

#### Scenario: system scope 行为保持

- **WHEN** 显式 system-scope key 请求任一观察入口
- **THEN** 行为与本门槛引入前一致(维持 403 `NO_WORKSPACE`),不新增跨 workspace 读取能力

#### Scenario: 拒绝请求不进入业务读取

- **WHEN** 任一被拒请求(404 或 403)处理完成
- **THEN** 列表/详情业务查询未被执行,durable 状态、命令回执与 outbox 均无变化,且伪造请求体角色(如 `role=admin`)SHALL NOT 提权

#### Scenario: 既有语义保持

- **WHEN** viewer 及以上角色成员正常使用三个观察入口
- **THEN** 列表分页与状态过滤、详情的责任记录/生命周期投影/等待信息、Run 列表排序与字段与门槛引入前完全一致
