## ADDED Requirements

### Requirement: CLI 备份命令
系统 SHALL 提供 `hecate backup` CLI 命令用于创建、列出和验证备份。

#### Scenario: 创建全量备份
- **WHEN** 执行 `hecate backup create`
- **THEN** 触发全系统全量备份（PostgreSQL + Qdrant + MinIO + Filesystem），输出备份进度和结果

#### Scenario: 创建指定范围备份
- **WHEN** 执行 `hecate backup create --scope=pg`
- **THEN** 仅触发 PostgreSQL 备份

#### Scenario: 列出备份记录
- **WHEN** 执行 `hecate backup list`
- **THEN** 显示所有 BackupRecord 列表，包含 ID、时间、类型、范围、大小、状态

#### Scenario: 列出备份记录（带过滤）
- **WHEN** 执行 `hecate backup list --status=completed --limit=10`
- **THEN** 显示最近 10 条已完成的备份记录

### Requirement: CLI 恢复命令
系统 SHALL 提供 `hecate restore` CLI 命令用于恢复数据。

#### Scenario: 全量恢复
- **WHEN** 执行 `hecate restore <backup-id>`
- **THEN** 显示恢复范围确认提示，用户输入 `yes` 后执行全量恢复

#### Scenario: 按租户恢复
- **WHEN** 执行 `hecate restore <backup-id> --workspace=<workspace_id> --conflict=replace`
- **THEN** 恢复指定 workspace 的所有数据，使用 replace 冲突策略

#### Scenario: PITR 恢复
- **WHEN** 执行 `hecate restore --pitrs="2026-07-29T10:30:00Z" --scope=pg`
- **THEN** 执行 PostgreSQL PITR 恢复到指定时间点

### Requirement: REST API 备份接口
系统 SHALL 提供 REST API 用于备份管理，仅 Platform Admin 可访问。

#### Scenario: 创建备份
- **WHEN** 调用 `POST /api/system/backups` with `{"scope": "all"}`
- **THEN** 触发全系统备份，返回 BackupRecord（status=started）

#### Scenario: 查询备份列表
- **WHEN** 调用 `GET /api/system/backups`
- **THEN** 返回 BackupRecord 列表，支持分页和状态过滤

#### Scenario: 查询单个备份详情
- **WHEN** 调用 `GET /api/system/backups/<backup-id>`
- **THEN** 返回该 BackupRecord 的完整详情，包含 metadata_

#### Scenario: 触发验证
- **WHEN** 调用 `POST /api/system/backups/<backup-id>/verify`
- **THEN** 触发该备份的验证流程，返回验证任务 ID

### Requirement: REST API 恢复接口
系统 SHALL 提供 REST API 用于数据恢复，仅 Platform Admin 可访问，需二次确认。

#### Scenario: 触发恢复
- **WHEN** 调用 `POST /api/system/restore` with `{"backup_id": "...", "scope": "pg", "conflict": "replace", "confirm": true}`
- **THEN** 执行恢复操作，返回恢复任务状态

#### Scenario: 未确认时拒绝恢复
- **WHEN** 调用 `POST /api/system/restore` 且 `confirm` 不为 `true`
- **THEN** 返回 400 Bad Request，要求提供确认参数

### Requirement: Platform Admin 权限控制
系统 SHALL 限制备份/恢复 API 和 CLI 仅 Platform Admin 可操作。

#### Scenario: 非管理员访问备份 API
- **WHEN** 非 Platform Admin 用户调用 `POST /api/system/backups`
- **THEN** 返回 403 Forbidden

#### Scenario: 管理员访问备份 API
- **WHEN** Platform Admin 用户调用 `POST /api/system/backups`
- **THEN** 正常执行备份操作

### Requirement: 备份操作日志
系统 SHALL 记录所有备份/恢复操作到审计日志。

#### Scenario: 备份操作记录审计日志
- **WHEN** 任何用户触发备份或恢复操作
- **THEN** 系统记录审计日志，包含操作类型、操作者、时间、范围、结果
