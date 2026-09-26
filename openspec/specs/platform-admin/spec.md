# platform-admin Specification

## Purpose

定义平台管理员（Platform Admin）身份的唯一解析来源，作为全平台级操作（备份/恢复等）的鉴权基础。在数据库身份管线（功能 11.16/11.17）落地前，平台管理员身份来自部署期配置引导，而非任何可由用户自行获取或推导的凭据。

## Requirements

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

### Requirement: System-scope API key 铸造限制

`POST /api/api-keys` 创建 `scope=system` 的 API key SHALL 仅允许平台管理员执行；非平台管理员的铸造请求 SHALL 返回 403 且不产生任何持久化记录。铸造 `scope=workspace` 的 key 的行为不受本限制影响。

#### Scenario: 非平台管理员铸造系统级 key 被拒

- **WHEN** 非平台管理员的已认证用户调用 `POST /api/api-keys` 且 `scope=system`
- **THEN** 返回 403，且数据库中不产生新的 API key 记录

#### Scenario: 平台管理员铸造系统级 key 成功

- **WHEN** 平台管理员调用 `POST /api/api-keys` 且 `scope=system`
- **THEN** 返回 201 与新 key 凭据

#### Scenario: 普通用户铸造 workspace 级 key 不受影响

- **WHEN** 已认证用户调用 `POST /api/api-keys` 且 `scope=workspace` 并提供有效 `workspace_id`
- **THEN** 行为与此前一致（按既有规则创建）
