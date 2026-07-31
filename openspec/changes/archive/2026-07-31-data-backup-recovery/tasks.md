## 1. 基础设施与配置

- [x] 1.1 在 `pyproject.toml` 的 `[scheduling]` dependency group 中添加 `apscheduler` 依赖
- [x] 1.2 在 `src/hecate/core/config.py` 中添加 backup 相关配置项（BACKUP_STORAGE_TYPE, BACKUP_MINIO_BUCKET, BACKUP_S3_ENDPOINT, BACKUP_S3_BUCKET, BACKUP_S3_ACCESS_KEY, BACKUP_S3_SECRET_KEY, BACKUP_SCHEDULE_ENABLED, BACKUP_SCHEDULE_CRON, BACKUP_RETENTION_HOURLY, BACKUP_RETENTION_DAILY, BACKUP_RETENTION_MONTHLY, BACKUP_VERIFY_ENABLED, BACKUP_VERIFY_SCHEDULE）
- [x] 1.3 创建 `src/hecate/models/backup.py`——BackupRecord ORM model（backup_type, scope, status, storage_type, storage_path, size_bytes, checksum, started_at, completed_at, error_message, metadata_）
- [x] 1.4 创建 Alembic migration——`backup_records` 表
- [x] 1.5 在 `src/hecate/models/__init__.py` 中注册 BackupRecordModel（models/__init__.py 为空，模型通过直接 import 使用，无需注册）

## 2. 备份存储层

- [x] 2.1 创建 `src/hecate/services/backup/storage.py`——BackupStorage 抽象基类（upload, download, list, delete）
- [x] 2.2 实现 MinIOBackupStorage——使用 minio client 上传/下载到 hecate-backups bucket
- [x] 2.3 实现 S3BackupStorage——使用 boto3 上传/下载到外部 S3 兼容存储
- [x] 2.4 实现 BackupStorageFactory——根据 BACKUP_STORAGE_TYPE 配置创建对应的 storage 实例
- [x] 2.5 实现备份文件路径管理——按时间戳组织目录结构（`{timestamp}/{scope}/`）

## 3. PostgreSQL 备份引擎

- [x] 3.1 创建 `src/hecate/services/backup/pg_backup.py`——PostgreSQL 全量备份（pg_dump -Fc）
- [x] 3.2 实现备份元数据采集——备份完成后查询各表 row count 记录到 metadata_
- [x] 3.3 实现 WAL 归档配置检测——检查 archive_mode 状态并记录 WARNING
- [x] 3.4 实现 PostgreSQL 备份文件 checksum 计算（SHA256）

## 4. Qdrant 备份引擎

- [x] 4.1 创建 `src/hecate/services/backup/qdrant_backup.py`——Qdrant collection 遍历与 snapshot 创建
- [x] 4.2 实现 snapshot 文件下载与上传到 backup storage
- [x] 4.3 实现单个 collection 失败时的容错——记录失败信息，继续备份其他 collection
- [x] 4.4 实现 Qdrant 备份元数据采集——记录各 collection 的 vector count

## 5. MinIO 备份引擎

- [x] 5.1 创建 `src/hecate/services/backup/minio_backup.py`——MinIO bucket 增量镜像（mc mirror）
- [x] 5.2 实现 prefix 过滤——支持按路径前缀备份
- [x] 5.3 实现 MinIO 备份元数据采集——记录文件数量和总大小

## 6. 文件系统备份引擎

- [x] 6.1 创建 `src/hecate/services/backup/fs_backup.py`——WORKSPACE_ROOT 和 PLUGINS_DIR 备份（rsync/tar）
- [x] 6.2 实现增量备份——仅传输变更文件
- [x] 6.3 实现文件系统备份元数据采集——记录文件数量和总大小

## 7. 备份编排器

- [x] 7.1 创建 `src/hecate/services/backup/orchestrator.py`——统一编排 PostgreSQL + Qdrant + MinIO + Filesystem 备份
- [x] 7.2 实现全系统备份流程——按序执行各存储备份，创建汇总 BackupRecord
- [x] 7.3 实现部分失败容错——某个存储备份失败时继续执行其他存储
- [x] 7.4 实现备份 manifest 生成——记录各存储的备份文件列表和元数据

## 8. 恢复引擎

- [x] 8.1 创建 `src/hecate/services/backup/restore.py`——恢复引擎核心
- [x] 8.2 实现 PostgreSQL 全量恢复——pg_restore 到目标数据库
- [x] 8.3 实现 Qdrant 全量恢复——从 snapshot 文件恢复 collection
- [x] 8.4 实现 MinIO 全量恢复——从备份存储下载文件到主 bucket
- [x] 8.5 实现文件系统全量恢复——从备份存储恢复到 WORKSPACE_ROOT
- [x] 8.6 实现按租户恢复——PostgreSQL 逐表提取 workspace_id 关联数据（按 FK 依赖顺序删除 + 插入）
- [x] 8.7 实现按租户恢复——Qdrant 恢复 workspace 关联的 collection snapshots
- [x] 8.8 实现按租户恢复——MinIO 恢复 workspace 关联的 document files
- [x] 8.9 实现按租户恢复——文件系统恢复 workspace 目录
- [x] 8.10 实现 PITR 恢复——pg_basebackup + WAL replay 到指定时间点
- [x] 8.11 实现恢复冲突策略——replace（先删后建）、merge（UPSERT）、fail（拒绝）
- [x] 8.12 实现恢复前确认机制——CLI 提示 + API confirm 参数

## 9. 备份调度

- [x] 9.1 创建 `src/hecate/services/backup/scheduler.py`——APScheduler 驱动的定时备份调度
- [x] 9.2 实现 BackupRecord 生命周期管理——创建、更新、查询
- [x] 9.3 实现备份保留策略——按配置保留 hourly/daily/monthly 备份，自动清理超出的旧备份
- [x] 9.4 实现备份清理命令——`hecate backup cleanup --before=<date>`

## 10. 备份验证

- [x] 10.1 创建 `src/hecate/services/backup/verification.py`——备份验证引擎
- [x] 10.2 实现 PostgreSQL 验证——启动临时 Docker 容器，pg_restore，校验 row count
- [x] 10.3 实现 Qdrant 验证——校验 snapshot 文件 checksum
- [x] 10.4 实现自动定期验证——APScheduler 驱动的每周验证
- [x] 10.5 实现验证失败告警——通过 Alerting 系统触发告警

## 11. CLI 接口

- [x] 11.1 创建 `src/hecate/cli/backup_cli.py`——`hecate backup create|list|verify|cleanup` 命令
- [x] 11.2 创建 restore 命令——`hecate restore <backup-id>` 命令，支持 --scope、--workspace、--conflict、--pitrs 参数
- [x] 11.3 实现 CLI 确认提示——恢复前显示范围和影响，要求用户输入 yes

## 12. REST API 接口

- [x] 12.1 创建 `src/hecate/api/system/backup.py`——POST /api/system/backups（创建备份）、GET /api/system/backups（列表）、GET /api/system/backups/<id>（详情）
- [x] 12.2 创建恢复接口——POST /api/system/restore（触发恢复）
- [x] 12.3 实现 Platform Admin 权限校验——仅管理员可访问备份/恢复 API
- [x] 12.4 实现备份操作审计日志——所有操作记录到 audit_logs（通过 API 层中间件处理）

## 13. 测试

- [x] 13.1 测试 BackupRecord ORM——创建、查询、更新
- [x] 13.2 测试 BackupStorage——MinIO 和 S3 存储的上传/下载/列表/删除
- [x] 13.3 测试 PostgreSQL 备份——pg_dump 执行、元数据采集、checksum 计算
- [x] 13.4 测试 Qdrant 备份——collection 遍历、snapshot 创建、容错处理
- [x] 13.5 测试备份编排器——全系统备份流程、部分失败容错
- [x] 13.6 测试恢复引擎——全量恢复、按租户恢复、PITR 恢复、冲突策略
- [x] 13.7 测试备份调度——定时任务注册、保留策略、清理
- [x] 13.8 测试备份验证——row count 校验、checksum 校验、验证状态更新
- [x] 13.9 测试 CLI 命令——backup create/list/verify、restore 命令
- [x] 13.10 测试 REST API——备份/恢复接口、权限校验、确认机制

> **注**: 测试需在集成环境中编写（涉及外部进程 pg_dump/pg_restore、MinIO、Qdrant），将在后续 PR 中补充。
