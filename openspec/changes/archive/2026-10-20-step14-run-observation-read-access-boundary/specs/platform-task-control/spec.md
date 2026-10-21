# Spec Delta

## ADDED Requirements

### Requirement: Run 观察读取携带最低读角色门槛

Run 观察的 REST 读取入口(`GET /api/runs/{run_id}`、`GET /api/runs/{run_id}/actions`、`GET /api/runs/{run_id}/events`、`GET /api/runs/{run_id}/events/stream`)SHALL 在读取动作账本、事件流或建立任何响应流之前施加两级权限门:先校验目标 Run 及其父 Task 均存在、未软删除且属于调用方当前 workspace——跨 workspace、已删除与不存在的 Run SHALL 返回一致的 404(响应 message SHALL 仅回显调用方提供的 run_id),SHALL NOT 泄露具体原因;再要求调用方在其当前 workspace 具备至少 `VIEWER` 角色,角色不足或缺失 SHALL 返回 403。权限判定 SHALL 取自服务端解析的 `AuthContext` 与既有角色校验构件,SHALL NOT 接受自报角色。

SSE 入口(`events/stream`)SHALL 在创建 `StreamingResponse` 之前完成上述两级校验:被拒请求 SHALL 返回正常 HTTP 403/404,SHALL NOT 以"HTTP 200 后发送错误事件帧"的方式呈现授权结果;建立连接之后的运行时异常处理 SHALL 保持既有行为。事件读取路径的 outbox relay 投影(`_pump_events`)SHALL 发生在 Run 资源校验之后——被拒请求 SHALL NOT 触发 relay、产生任何状态变化或存储写入。

VIEWER、EDITOR 与 ADMIN 的成功响应结构、动作字段白名单、事件游标分页、SSE 终态语义与断线续读语义 SHALL 与门槛引入前完全一致。缺 workspace 的非 system 身份 SHALL 维持既有 403 `NO_WORKSPACE` 拒绝,SHALL NOT 退化为全局查询;显式 system-scope 身份的既有行为 SHALL 保持不变,SHALL NOT 因本门槛获得或失去跨 workspace 读取能力。

#### Scenario: 跨 workspace Run 观察表现为不存在

- **WHEN** workspace W2 的成员请求 workspace W1 中某 Run 的详情、动作、事件分页或事件流
- **THEN** 四个入口均为 404,与请求一个不存在的 run_id 一致(message 仅回显调用方提供的 run_id);W1 的 Run、动作账本、事件流与 outbox 均无变化

#### Scenario: Run 软删除与父 Task 删除一致表现为不存在

- **WHEN** Run 自身被软删除,或其父 Task 被软删除或归属改变,任一情形下请求 Run 观察
- **THEN** 响应与不存在的 run_id 一致(404),不区分具体情形

#### Scenario: 同 workspace viewer 可读

- **WHEN** workspace W1 的 viewer 角色成员请求 W1 内 Run 的详情、动作、事件分页或事件流
- **THEN** 四个入口均成功,响应结构与内容与更高角色完全一致;事件游标续读与 SSE 终态行为不变

#### Scenario: 无角色或缺 workspace 身份被拒

- **WHEN** 有效认证但在当前 workspace 无任何角色的身份,或无 workspace 归属的非 system 身份请求任一观察入口
- **THEN** 响应为 403 授权拒绝(缺 workspace 维持既有 `NO_WORKSPACE`),SHALL NOT 退化为全局查询

#### Scenario: SSE 拒绝不建立流

- **WHEN** 未通过可见性或角色校验的调用方请求 `events/stream`
- **THEN** 响应为正常 HTTP 403/404,SHALL NOT 返回 200 或任何 SSE 事件帧;relay 投影未被触发

#### Scenario: 拒绝请求不触发 relay

- **WHEN** 任一被拒请求(404 或 403)处理完成
- **THEN** outbox relay 投影(`_pump_events`)未被调用,动作账本、事件存储、命令回执与 Run/Task 状态均无变化

#### Scenario: system scope 行为保持

- **WHEN** 缺 workspace 的身份(含显式 system-scope key)请求任一观察入口
- **THEN** 行为与本门槛引入前一致(维持 403 `NO_WORKSPACE`),不新增跨 workspace 读取能力
