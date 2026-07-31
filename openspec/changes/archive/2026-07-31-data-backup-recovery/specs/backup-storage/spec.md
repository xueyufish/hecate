## ADDED Requirements

### Requirement: 内部 MinIO bucket 存储
系统 SHALL 支持将备份文件存储到 MinIO 的独立 bucket（`hecate-backups`），与主数据 bucket 物理隔离。

#### Scenario: 配置使用内部 MinIO 存储
- **WHEN** 环境变量 `BACKUP_STORAGE_TYPE=minio` 且 `BACKUP_MINIO_BUCKET=hecate-backups`
- **THEN** 备份文件存储到 `hecate-backups` bucket，使用与主数据相同的 MinIO 连接配置

#### Scenario: 自动创建备份 bucket
- **WHEN** 备份时 `hecate-backups` bucket 不存在
- **THEN** 系统自动创建该 bucket

### Requirement: 外部 S3 兼容存储
系统 SHALL 支持将备份文件存储到外部 S3 兼容存储（AWS S3、GCS、Cloudflare R2 等）。

#### Scenario: 配置使用外部 S3 存储
- **WHEN** 环境变量 `BACKUP_STORAGE_TYPE=s3` 且配置了 `BACKUP_S3_ENDPOINT`、`BACKUP_S3_BUCKET`、`BACKUP_S3_ACCESS_KEY`、`BACKUP_S3_SECRET_KEY`
- **THEN** 备份文件存储到指定的 S3 兼容存储

#### Scenario: S3 连接失败时回退到内部存储
- **WHEN** 外部 S3 连接失败（网络不可达、认证失败）
- **THEN** 系统记录 ERROR 日志，不执行备份（不自动回退到内部存储，避免静默降级）

### Requirement: 备份文件组织结构
系统 SHALL 按照统一的目录结构组织备份文件。

#### Scenario: 备份文件路径格式
- **WHEN** 创建全量备份（时间戳 20260729_103000）
- **THEN** 备份文件存储在以下路径结构：
  - `20260729_103000/pg/full.dump`
  - `20260729_103000/qdrant/kb_xxxx.snapshot`
  - `20260729_103000/minio/` (mc mirror 目标)
  - `20260729_103000/fs/workspace.tar.gz`
  - `20260729_103000/manifest.json`（备份清单，包含各存储的元数据）

#### Scenario: WAL 归档路径格式
- **WHEN** PostgreSQL 归档 WAL 文件
- **THEN** WAL 文件存储在 `wal/` 前缀下（如 `wal/000000010000000000000001`）

### Requirement: 保留策略
系统 SHALL 支持可配置的备份保留策略，默认保留 24 个 hourly + 14 个 daily + 12 个 monthly 备份。

#### Scenario: 默认保留策略
- **WHEN** 创建新备份且已有超过保留数量的旧备份
- **THEN** 系统自动删除超出保留数量的最旧备份（不删除 WAL 归档）

#### Scenario: 自定义保留策略
- **WHEN** 配置 `BACKUP_RETENTION_HOURLY=48`、`BACKUP_RETENTION_DAILY=30`、`BACKUP_RETENTION_MONTHLY=24`
- **THEN** 系统按自定义策略保留备份

#### Scenario: 保留策略不影响正在使用的备份
- **WHEN** 某个备份正在被恢复操作使用
- **THEN** 保留策略跳过该备份，不在恢复期间删除

### Requirement: 备份清理
系统 SHALL 支持手动清理指定时间范围内的备份。

#### Scenario: 清理指定日期前的备份
- **WHEN** 执行 `hecate backup cleanup --before=2026-06-01`
- **THEN** 系统删除 2026-06-01 之前的所有备份文件和对应的 BackupRecord
