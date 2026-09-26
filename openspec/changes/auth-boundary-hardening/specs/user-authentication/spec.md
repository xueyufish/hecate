# user-authentication Delta

## Purpose

定义用户名密码认证的令牌生命周期语义：登录时的组织上下文解析、刷新时的成员资格重解析、以及请求期认证对成员资格的校验，保证权限撤销在令牌有效期内也能生效。

## ADDED Requirements

### Requirement: 登录签发正确的组织上下文

登录 SHALL 解析用户当前有效的首个 workspace 成员资格（关联 `WorkspaceModel` 读取真实 `org_id`），并 SHALL 过滤已软删除的成员关系与已软删除的 workspace。签发的 access 与 refresh token SHALL 携带一致且正确的 org/workspace/role claims；用户无任何有效成员资格时 SHALL 签发不含 workspace 上下文的令牌（受限身份）。

#### Scenario: 首次登录组织信息正确

- **WHEN** 用户登录且其成员资格对应的 workspace 属于某组织
- **THEN** 签发的 token 中 org_id 为该 workspace 所属组织的 ID（而非 workspace ID）

#### Scenario: 已删除的成员资格不参与解析

- **WHEN** 用户的成员关系行或其 workspace 已被软删除
- **THEN** 该成员资格 SHALL NOT 被选为登录上下文

### Requirement: 刷新令牌重新解析成员资格

刷新令牌兑换 SHALL NOT 原样沿用旧 token 中的组织/角色 claims；系统 SHALL 按数据库当前状态重新解析成员资格并签发新 claims。成员资格已被移除或角色已变更时，新令牌 SHALL 反映当前状态；用户已无任何有效成员资格时 SHALL 签发受限身份令牌。refresh token 本身无效或用户不存在/停用时 SHALL 拒绝刷新。

#### Scenario: 降权后刷新获得新角色

- **WHEN** 用户角色由 admin 降为 viewer 后使用旧 refresh token 刷新
- **THEN** 新签发的 token 携带 viewer 角色

#### Scenario: 移除成员后刷新得到受限身份

- **WHEN** 用户被移出所有 workspace 后使用旧 refresh token 刷新
- **THEN** 新签发的 token 不含 workspace 上下文（受限身份），而非沿用旧的 workspace/role

### Requirement: 请求期成员资格校验

JWT 请求期认证在用户状态（active）检查之外，SHALL 于 token 携带 workspace claim 时校验对应成员资格的当前状态：成员关系存在、未软删除、所属 workspace 未软删除、且数据库中的角色与 token 声明一致。任一不满足 SHALL 拒绝认证（401）。

#### Scenario: 移除成员后旧 access token 失效

- **WHEN** 成员被移出 workspace 后，携带旧 access token（未过期）请求该 workspace 资源
- **THEN** 认证被拒绝（401）

#### Scenario: 角色声明与数据库不一致被拒

- **WHEN** token 声明 admin 但数据库当前角色为 viewer
- **THEN** 认证被拒绝（401），而非按声明的 admin 放行

#### Scenario: 无 workspace claim 的 token 不做成员校验

- **WHEN** 请求携带不含 workspace claim 的有效 JWT 且用户 active
- **THEN** 认证通过并建立受限身份
