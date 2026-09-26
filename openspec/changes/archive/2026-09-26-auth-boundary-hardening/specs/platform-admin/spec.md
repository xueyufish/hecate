# platform-admin Delta

## MODIFIED Requirements

### Requirement: 平台管理员身份解析

系统 SHALL 仅依据两类部署期配置解析平台管理员身份：运维令牌清单（`PLATFORM_ADMIN_API_KEYS`，与请求 Bearer 凭据比对）和用户邮箱白名单（`PLATFORM_ADMIN_EMAILS`，作用于通过 JWT 认证且用户存在的请求）。两类清单 SHALL 支持逗号分隔多值。当两类清单均为空或均未配置时，系统 SHALL 认定不存在任何平台管理员（fail-closed）。

系统级身份（system-scope，即跨 workspace 绕过租户过滤的权限）SHALL 仅授予部署期配置的 env key（`HECATE_API_KEYS` 与 `PLATFORM_ADMIN_API_KEYS`）；任何从请求自身缺失租户上下文（如 JWT 无 workspace claim）推导系统级身份的行为 SHALL 被禁止。数据库签发的 SYSTEM-scope API key SHALL 在认证阶段被拒绝（视同无效凭据）。运维令牌 SHALL 能通过统一认证链建立 system-scope 身份，从而满足平台管理员门禁。

#### Scenario: 运维令牌匹配

- **WHEN** 请求的 Bearer 凭据与 `PLATFORM_ADMIN_API_KEYS` 中任一令牌匹配
- **THEN** 该请求被视为平台管理员身份

#### Scenario: 运维令牌通过统一认证链建立系统身份

- **WHEN** 携带 `PLATFORM_ADMIN_API_KEYS` 中令牌的请求经过统一认证链（JWT → 数据库 API key → env 引导 key）
- **THEN** 该令牌建立 system-scope 的认证身份，后续平台管理员门禁据此放行

#### Scenario: 邮箱白名单匹配

- **WHEN** 请求通过 JWT 认证，且认证用户的邮箱出现在 `PLATFORM_ADMIN_EMAILS` 中
- **THEN** 该请求被视为平台管理员身份

#### Scenario: 清单为空时拒绝所有调用方

- **WHEN** `PLATFORM_ADMIN_API_KEYS` 与 `PLATFORM_ADMIN_EMAILS` 均为空，任意调用方（含持有效 JWT 或数据库 system-scope API key 的用户）请求平台管理员操作
- **THEN** 系统认定其非平台管理员并拒绝

#### Scenario: 数据库系统级 API key 不构成平台身份

- **WHEN** 请求使用数据库签发的 `scope=system` API key 认证
- **THEN** 认证被拒绝（视同无效凭据），该 key 不建立任何身份

#### Scenario: 缺失 workspace 的 JWT 不是系统身份

- **WHEN** 请求携带有效的 JWT，但该 JWT 不含 workspace claim（如用户未加入任何组织）
- **THEN** 该请求不获得系统级身份，不绕过任何租户过滤或 RBAC 检查
