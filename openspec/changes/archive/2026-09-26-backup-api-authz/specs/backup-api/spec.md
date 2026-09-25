# backup-api Delta

## MODIFIED Requirements

### Requirement: Platform Admin 权限控制

系统 SHALL 限制备份/恢复 REST API 与 CLI 仅 Platform Admin 可操作。REST 侧的平台管理员判定 SHALL 遵循 `platform-admin` capability 的身份解析规则。全部五个 REST 端点（创建备份、列出备份、查询备份详情、触发验证、触发恢复）SHALL 统一挂载该权限门禁：凭据缺失返回 401，已认证但非平台管理员返回 403。被拒绝的请求 SHALL NOT 触发备份、恢复或验证函数的执行。CLI 命令的使用边界不变（CLI 操作者本身即平台运维）。

#### Scenario: 匿名访问备份 API

- **WHEN** 未携带任何凭据的请求访问任一备份/恢复 REST 端点
- **THEN** 返回 401 Unauthorized，且不执行任何备份/恢复操作

#### Scenario: 非管理员访问备份 API

- **WHEN** 已认证但非平台管理员的用户（含 workspace admin）调用任一备份/恢复 REST 端点
- **THEN** 返回 403 Forbidden，且备份/恢复/验证函数未被调用

#### Scenario: 管理员访问备份 API

- **WHEN** 平台管理员调用 `POST /api/system/backups`
- **THEN** 正常执行备份操作并返回 BackupRecord

#### Scenario: 平台管理员查询备份无需特权函数触发

- **WHEN** 平台管理员调用 `GET /api/system/backups` 或 `GET /api/system/backups/<backup-id>`
- **THEN** 返回备份列表或详情；非平台管理员调用相同端点返回 403

#### Scenario: 恢复保留二次确认

- **WHEN** 平台管理员调用 `POST /api/system/restore` 且 `confirm` 不为 `true`
- **THEN** 返回 400 Bad Request，要求提供确认参数
