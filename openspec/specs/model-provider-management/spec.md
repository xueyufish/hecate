# Model-Provider-Management Specification

## Purpose

模型供应商（Model Provider）与注册模型（Model Registry）是平台级资源：供应商配置携带全局唯一的名称与加密凭据，供全平台 agent 复用。本 capability 定义其管理端点的权限分层。

## Requirements

### Requirement: 供应商与模型的变更为平台管理员操作

模型供应商与注册模型的全部变更操作（创建/更新/删除供应商、连接测试、发现模型、新增/更新/删除/发布/取消发布模型）SHALL 仅允许平台管理员执行。凭据缺失返回 401，已认证非平台管理员返回 403。请求期认证 SHALL 校验用户当前状态（停用用户即使持有效 JWT 亦被拒绝）。

#### Scenario: 非平台管理员修改供应商被拒

- **WHEN** 已认证的 workspace viewer/editor/admin 调用供应商变更类端点（如修改 base_url、切换模型发布状态）
- **THEN** 返回 403，变更不生效

#### Scenario: 停用用户的 JWT 被拒

- **WHEN** 已被停用的用户持未过期的 JWT 调用供应商变更端点
- **THEN** 认证被拒绝（401）

#### Scenario: 平台管理员执行变更

- **WHEN** 平台管理员调用供应商创建/更新/删除或模型发布端点
- **THEN** 操作正常执行

### Requirement: 供应商读取需认证且不泄露凭据

列举供应商与模型 SHALL 要求已认证身份（任意已认证用户可见，供 agent 配置使用）；任何供应商响应 SHALL NOT 包含加密凭据或足以还原凭据的信息。

#### Scenario: 已认证用户读取供应商列表

- **WHEN** 任一已认证用户调用供应商/模型列举端点
- **THEN** 返回供应商与模型元数据（名称、模型 ID、能力等）

#### Scenario: 响应不泄露凭据

- **WHEN** 检查任何供应商读取响应
- **THEN** 响应体不包含加密或明文的供应商 API key
