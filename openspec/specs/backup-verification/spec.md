# backup-verification Specification

## Purpose
TBD - created by archiving change data-backup-recovery. Update Purpose after archive.
## Requirements
### Requirement: 备份完整性验证
系统 SHALL 支持将备份恢复到临时 PostgreSQL 实例并校验数据完整性。

#### Scenario: 触发验证流程
- **WHEN** 执行 `hecate backup verify <backup-id>`
- **THEN** 系统启动临时 PostgreSQL Docker 容器，执行 pg_restore，查询各表 row count，与 BackupRecord.metadata_ 中记录的 count 对比

#### Scenario: 验证通过
- **WHEN** 临时实例中所有表的 row count 与备份记录一致
- **THEN** 更新 BackupRecord 的验证状态为 verified，记录验证时间

#### Scenario: 验证失败——数据不一致
- **WHEN** 临时实例中某表的 row count 与备份记录不一致
- **THEN** 更新 BackupRecord 的验证状态为 verification_failed，记录不一致的表名和 count 差异

#### Scenario: 验证失败——恢复错误
- **WHEN** pg_restore 执行失败（如备份文件损坏）
- **THEN** 更新 BackupRecord 的验证状态为 verification_failed，记录错误信息

### Requirement: Qdrant 快照验证
系统 SHALL 支持验证 Qdrant snapshot 文件的完整性。

#### Scenario: 验证 Qdrant snapshot
- **WHEN** 执行 Qdrant 备份验证
- **THEN** 系统检查每个 snapshot 文件的 checksum 与备份时记录的 checksum 一致

### Requirement: 自动定期验证
系统 SHALL 支持配置自动定期验证备份完整性（默认每周一次）。

#### Scenario: 每周自动验证
- **WHEN** 配置 `BACKUP_VERIFY_SCHEDULE=0 4 * * 0`（每周日凌晨 4:00）
- **THEN** 系统自动对最近的备份执行验证流程

#### Scenario: 验证失败时告警
- **WHEN** 自动验证发现备份不完整或损坏
- **THEN** 系统触发告警（通过已有的 Alerting 系统），通知管理员

