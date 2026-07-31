# backup-scheduling Specification

## Purpose
TBD - created by archiving change data-backup-recovery. Update Purpose after archive.
## Requirements
### Requirement: 定时全量备份调度
系统 SHALL 支持通过 APScheduler 驱动的定时全量备份，默认每日凌晨 2:00 执行。

#### Scenario: 默认每日备份
- **WHEN** 系统启动且 `BACKUP_SCHEDULE_ENABLED=true`
- **THEN** APScheduler 注册每日 02:00 的全量备份任务

#### Scenario: 自定义调度时间
- **WHEN** 配置 `BACKUP_SCHEDULE_CRON=0 3 * * *`（每日 03:00）
- **THEN** APScheduler 按自定义 cron 表达式调度备份

#### Scenario: 调度任务失败重试
- **WHEN** 定时备份任务执行失败
- **THEN** 系统记录失败日志，下次调度时间自动重试，不手动干预

### Requirement: 备份记录管理
系统 SHALL 通过 BackupRecord ORM model 记录每次备份的完整元数据。

#### Scenario: 备份开始时创建记录
- **WHEN** 备份任务开始执行
- **THEN** 创建 BackupRecord（status=started, backup_type, scope, storage_type, started_at）

#### Scenario: 备份完成时更新记录
- **WHEN** 备份任务成功完成
- **THEN** 更新 BackupRecord（status=completed, size_bytes, checksum, completed_at, metadata_ 包含 row counts）

#### Scenario: 备份失败时更新记录
- **WHEN** 备份任务执行失败
- **THEN** 更新 BackupRecord（status=failed, error_message, completed_at）

### Requirement: 备份元数据记录
系统 SHALL 在备份完成时记录各存储的数据统计信息到 BackupRecord.metadata_。

#### Scenario: 记录 PostgreSQL 统计
- **WHEN** PostgreSQL 备份完成
- **THEN** metadata_ 记录各表的 row count（如 `{"pg": {"agents": 150, "conversations": 3000, ...}}`）

#### Scenario: 记录 Qdrant 统计
- **WHEN** Qdrant 备份完成
- **THEN** metadata_ 记录各 collection 的 vector count（如 `{"qdrant": {"kb_xxxx": 5000, ...}}`）

### Requirement: 备份状态查询
系统 SHALL 支持查询当前正在执行的备份任务状态。

#### Scenario: 查询进行中的备份
- **WHEN** 调用 `GET /api/system/backups?status=running`
- **THEN** 返回所有 status=running 的 BackupRecord 列表，包含已执行时间和进度信息

