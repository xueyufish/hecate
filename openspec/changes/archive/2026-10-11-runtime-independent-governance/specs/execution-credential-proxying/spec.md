# Spec Delta

## Purpose

定义执行侧凭据治理:adapter 配置只存 secret 引用,凭据代理优先并以可核验服务身份或 on-behalf-of 上下文调用业务 API,直发凭据必须短期且限定作用域,明文不得进入事件与 trace。

## ADDED Requirements

### Requirement: adapter 配置只存 secret 引用

工具 adapter、模型 adapter 与业务目标的配置 SHALL 只保存 secret 引用,不接受明文凭据;含明文 secret 的配置在保存或加载时 SHALL 被拒绝。事件、trace 与证据记录 MUST NOT 包含凭据明文。

#### Scenario: 配置含明文凭据被拒绝

- **WHEN** 一份 adapter 配置内联了明文 API key 并被提交加载
- **THEN** 配置被拒绝,错误指明须改用 secret 引用,加载失败不产生降级的可用配置

#### Scenario: 事件与 trace 无凭据明文

- **WHEN** 受保护动作的决策、执行回执与调用参数被写入证据
- **THEN** 其中凭据字段以引用或脱敏形式出现,检索不到明文

### Requirement: 凭据代理优先

对业务系统的调用 SHALL 优先经凭据代理:调用携带可核验的服务身份或 on-behalf-of 上下文,由代理按目标注入凭据,凭据不落入调用方可见的参数或日志。业务 API 侧仍自行完成最终资源权限与状态机校验。

#### Scenario: 业务 API 调用携带可核验身份上下文

- **WHEN** Agent 代表某 principal 调用业务 API
- **THEN** 请求携带可核验的服务身份与 on-behalf-of 上下文,业务 API 可独立验证并授权

#### Scenario: 凭据不经调用方参数传递

- **WHEN** 经代理的业务调用被执行与记录
- **THEN** 调用方参数与日志中不含目标凭据,凭据仅在代理侧注入

### Requirement: 直发凭据必须短期且限定作用域

确需直接下发凭据时(代理不可用),系统 SHALL 只发放短期、限定作用域的令牌,绑定本次任务/动作范围;长期凭据 MUST NOT 直接进入执行环境。作用域外的使用 SHALL 被目标侧或网关拒绝。

#### Scenario: 直发凭据短期且限域

- **WHEN** 代理不可用,系统对某工具直发凭据
- **THEN** 凭据带有效期与作用域标记,超期或越域使用被拒绝且可观察

#### Scenario: 长期凭据不进入执行环境

- **WHEN** 执行环境(工具进程或沙箱)请求凭据
- **THEN** 环境内只能获得短期作用域令牌或既有脱敏规则处理后的变量,取不到平台长期凭据
