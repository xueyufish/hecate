## ADDED Requirements

### Requirement: PostgreSQL 全量备份
系统 SHALL 支持通过 `pg_dump -Fc` 对 PostgreSQL 数据库进行全量逻辑备份，生成 custom format 的 dump 文件。

#### Scenario: 成功创建全量备份
- **WHEN** 触发全量备份（通过 CLI `hecate backup create --scope=pg` 或 API `POST /api/system/backups` with `scope=pg`）
- **THEN** 系统执行 `pg_dump -Fc`，生成 dump 文件，上传到配置的备份存储，创建 BackupRecord 记录（status=completed, size_bytes, checksum）

#### Scenario: 备份失败时记录错误
- **WHEN** pg_dump 执行失败（如数据库连接失败、磁盘空间不足）
- **THEN** BackupRecord 记录 status=failed 和 error_message，不上传不完整的文件

### Requirement: PostgreSQL WAL 归档
系统 SHALL 支持配置 PostgreSQL WAL 归档到对象存储，实现持续数据保护（RPO ≈ 0）。

#### Scenario: WAL 归档配置检测
- **WHEN** 触发备份且检测到 PostgreSQL 未启用 `archive_mode`
- **THEN** 系统记录 WARNING 日志，提示需要配置 WAL 归档，并继续执行全量备份

#### Scenario: WAL 文件归档到存储
- **WHEN** PostgreSQL 生成新的 WAL 文件且 `archive_command` 已配置
- **THEN** WAL 文件自动归档到配置的备份存储路径（`wal/` 前缀）

### Requirement: Qdrant collection 快照
系统 SHALL 支持通过 Qdrant snapshot API 对所有 collection 进行一致性快照备份。

#### Scenario: 成功创建所有 collection 快照
- **WHEN** 触发 Qdrant 备份
- **THEN** 系统遍历所有 collection（`GET /collections`），对每个 collection 调用 `POST /collections/{name}/snapshots`，下载 snapshot 文件并上传到备份存储

#### Scenario: 单个 collection 快照失败不影响其他
- **WHEN** 某个 collection 的 snapshot 创建失败（如 collection 正在重建索引）
- **THEN** 系统记录该 collection 的失败信息，继续备份其他 collection，最终 BackupRecord 记录部分失败状态

### Requirement: MinIO 对象存储备份
系统 SHALL 支持通过 MinIO Client（`mc`）对 MinIO bucket 进行增量镜像备份。

#### Scenario: 成功创建 MinIO 增量备份
- **WHEN** 触发 MinIO 备份
- **THEN** 系统执行 `mc mirror` 将主 bucket 同步到备份存储的 backup 前缀下，仅传输变更的文件

#### Scenario: 指定 prefix 过滤备份
- **WHEN** 触发 MinIO 备份时指定 `prefix=documents/`
- **THEN** 仅同步 `documents/` 前缀下的文件

### Requirement: 文件系统备份
系统 SHALL 支持对 WORKSPACE_ROOT 和 PLUGINS_DIR 目录进行增量备份。

#### Scenario: 成功创建文件系统备份
- **WHEN** 触发文件系统备份
- **THEN** 系统使用 `rsync` 或 `tar` 将 WORKSPACE_ROOT 和 PLUGINS_DIR 同步到备份存储

#### Scenario: 增量备份仅传输变更
- **WHEN** 两次备份之间只有少量文件变更
- **THEN** 增量备份仅传输变更的文件，不重新传输全部文件

### Requirement: 全系统统一备份
系统 SHALL 支持一键触发全系统备份（PostgreSQL + Qdrant + MinIO + Filesystem），按顺序执行各存储的备份。

#### Scenario: 全系统备份按序执行
- **WHEN** 触发全系统备份（`hecate backup create --scope=all`）
- **THEN** 系统按 PostgreSQL → Qdrant → MinIO → Filesystem 顺序依次备份，每个存储创建独立的 BackupRecord，最终创建一个汇总 BackupRecord（scope=all）

#### Scenario: 部分存储失败时继续
- **WHEN** 全系统备份中某个存储备份失败
- **THEN** 系统记录失败信息，继续备份剩余存储，汇总 BackupRecord 记录为 partial 状态
