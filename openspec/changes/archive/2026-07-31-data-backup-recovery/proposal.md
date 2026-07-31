## Why

Hecate 当前没有任何数据备份与恢复机制。PostgreSQL（79 个 ORM 模型）、Qdrant（知识库向量索引）、MinIO（用户文档/审计归档）和文件系统（workspace/agent environment/plugins）中的数据一旦丢失将不可恢复。这是 P3 MVP 审计中标记的 **P0 发布阻塞项**——无备份机制意味着数据库故障 = 全站数据永久丢失，无法上生产。

业界对比：自托管 Agent 平台标杆 Dify 仅提供"停服 + tar 打包 volumes"的原始方案，无 API、无调度、无验证。云托管平台（Bedrock、watsonx、Agentforce）依赖基础设施层自动备份，不暴露应用级备份 API。Hecate 有机会成为自托管 Agent 平台中备份方案最完善的。

## What Changes

- 新增 **Backup Engine**：通过 `pg_dump`（全量）+ WAL archive（持续）备份 PostgreSQL；通过 Qdrant snapshot API 备份向量索引；通过 `mc mirror` 备份 MinIO；通过 `rsync` 备份文件系统
- 新增 **Restore Engine**：支持全量恢复、按数据类型恢复（PG / Qdrant / MinIO / FS）、按租户恢复（指定 workspace_id）、PITR 时间点恢复
- 新增 **Backup Storage**：支持内部 MinIO bucket 和外部 S3 兼容存储（AWS S3 / GCS / R2）双目标
- 新增 **Backup Scheduling**：APScheduler 驱动的定时备份，保留策略可配置（默认 24h + 14d + 12m）
- 新增 **Backup Verification**：恢复到临时实例并校验 row count / collection count，记录验证结果
- 新增 **BackupRecord ORM model**：记录每次备份的元数据（类型、范围、大小、状态、校验和、存储位置）
- 新增 **CLI 命令**：`hecate backup create|list|verify`、`hecate restore <backup-id>`
- 新增 **REST API**：`POST /api/system/backups`、`GET /api/system/backups`、`POST /api/system/restore`、`POST /api/system/verify`
- 恢复冲突策略：`replace`（先删后建）、`merge`（UPSERT）、`fail`（拒绝）三种模式
- 权限层级：第一版仅 Platform Admin 可操作（后续增强 Org/Workspace 级别）

## Capabilities

### New Capabilities

- `backup-engine`: 备份引擎核心——PostgreSQL pg_dump + WAL archive、Qdrant snapshot、MinIO mc mirror、Filesystem rsync 的统一编排
- `restore-engine`: 恢复引擎——全量恢复、按数据类型恢复、按租户（workspace_id）恢复、PITR 时间点恢复，支持 replace/merge/fail 冲突策略
- `backup-storage`: 备份存储层——内部 MinIO bucket + 外部 S3 兼容存储的双目标支持，保留策略（24h + 14d + 12m）配置
- `backup-scheduling`: 定时备份调度——APScheduler 驱动的全量/增量备份调度，BackupRecord 记录每次备份元数据
- `backup-verification`: 备份验证——恢复到临时实例、校验数据完整性（row count、collection count）、记录验证结果
- `backup-api`: 管理接口——CLI（`hecate backup` / `hecate restore`）+ REST API（备份/恢复/查询/验证），Platform Admin 权限控制

### Modified Capabilities

（无已有 spec 需要修改）

## Impact

- **新增代码**：`src/hecate/services/backup/`（backup engine、restore engine、storage、scheduler、verification）、`src/hecate/models/backup.py`（BackupRecord ORM）、`src/hecate/api/system/backup.py`（REST API）、`src/hecate/cli/backup.py`（CLI）
- **新增依赖**：`apscheduler`（调度，可选 `[scheduling]` dependency group）
- **数据库变更**：新增 `backup_records` 表（Alembic migration）
- **配置变更**：新增 backup 相关环境变量（`BACKUP_STORAGE_TYPE`、`BACKUP_S3_ENDPOINT`、`BACKUP_SCHEDULE`、`BACKUP_RETENTION_*` 等）
- **运维要求**：PostgreSQL 需配置 `archive_mode=on` + `archive_command`（WAL 归档到对象存储）
- **Docker Compose**：可选挂载 backup volume
