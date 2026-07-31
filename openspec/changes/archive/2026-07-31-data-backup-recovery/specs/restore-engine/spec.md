## ADDED Requirements

### Requirement: 全量恢复
系统 SHALL 支持从备份文件全量恢复 PostgreSQL、Qdrant、MinIO 和文件系统数据。

#### Scenario: 全量恢复 PostgreSQL
- **WHEN** 执行 `hecate restore <backup-id> --scope=pg`
- **THEN** 系统从备份存储下载 pg_dump 文件，执行 `pg_restore` 到目标数据库，完成后记录恢复日志

#### Scenario: 全量恢复 Qdrant
- **WHEN** 执行 `hecate restore <backup-id> --scope=qdrant`
- **THEN** 系统从备份存储下载 collection snapshot 文件，通过 Qdrant snapshot restore API 恢复每个 collection

#### Scenario: 全量恢复 MinIO
- **WHEN** 执行 `hecate restore <backup-id> --scope=minio`
- **THEN** 系统从备份存储下载文件并上传到主 MinIO bucket

#### Scenario: 全量恢复文件系统
- **WHEN** 执行 `hecate restore <backup-id> --scope=fs`
- **THEN** 系统从备份存储下载文件并恢复到 WORKSPACE_ROOT 和 PLUGINS_DIR

### Requirement: 按数据类型恢复
系统 SHALL 支持选择性恢复指定的数据类型（PostgreSQL / Qdrant / MinIO / Filesystem 中的一个或多个）。

#### Scenario: 仅恢复 Qdrant
- **WHEN** 执行 `hecate restore <backup-id> --scope=qdrant`
- **THEN** 系统仅恢复 Qdrant collection snapshots，不影响 PostgreSQL、MinIO 和文件系统

#### Scenario: 恢复多个数据类型
- **WHEN** 执行 `hecate restore <backup-id> --scope=pg,qdrant`
- **THEN** 系统恢复 PostgreSQL 和 Qdrant，不影响 MinIO 和文件系统

### Requirement: 按租户恢复
系统 SHALL 支持按指定 workspace_id 恢复该租户的所有关联数据（PostgreSQL 租户级表、Qdrant 关联 collection、MinIO 关联文件、文件系统 workspace 目录）。

#### Scenario: 按租户恢复 PostgreSQL
- **WHEN** 执行 `hecate restore <backup-id> --scope=pg --workspace=<workspace_id>` 且目标 workspace 不存在
- **THEN** 系统从备份恢复到临时 PostgreSQL 实例，提取 workspace_id 关联的所有行（按依赖顺序），INSERT 到目标数据库

#### Scenario: 按租户恢复 Qdrant
- **WHEN** 执行 `hecate restore <backup-id> --scope=qdrant --workspace=<workspace_id>`
- **THEN** 系统从 PostgreSQL 查询该 workspace 关联的 knowledge_bases，恢复对应的 collection snapshots

#### Scenario: 按租户恢复 MinIO
- **WHEN** 执行 `hecate restore <backup-id> --scope=minio --workspace=<workspace_id>`
- **THEN** 系统从 PostgreSQL 查询该 workspace 关联的 documents.file_path，仅恢复这些文件

#### Scenario: 按租户恢复文件系统
- **WHEN** 执行 `hecate restore <backup-id> --scope=fs --workspace=<workspace_id>`
- **THEN** 系统仅恢复 `WORKSPACE_ROOT/workspace_{workspace_id}/` 目录

### Requirement: PITR 时间点恢复
系统 SHALL 支持 PostgreSQL 的 Point-in-Time Recovery（PITR），通过 WAL replay 恢复到指定时间点。

#### Scenario: PITR 恢复到指定时间点
- **WHEN** 执行 `hecate restore --pitrs="2026-07-29T10:30:00Z" --scope=pg`
- **THEN** 系统使用最近的全量备份 + WAL 归档，执行 PITR 恢复到 2026-07-29T10:30:00Z

#### Scenario: PITR 时间点在备份范围外
- **WHEN** 指定的 PITR 时间早于最早可用的 WAL 归档
- **THEN** 系统返回错误，提示可用的最早恢复时间点

### Requirement: 恢复冲突策略
系统 SHALL 支持三种恢复冲突处理模式：replace、merge、fail。

#### Scenario: replace 模式——先删后建
- **WHEN** 执行 `hecate restore <backup-id> --workspace=<ws> --conflict=replace`
- **THEN** 系统先删除目标 workspace 的所有数据（按 FK 依赖顺序），然后从备份插入数据

#### Scenario: merge 模式——UPSERT
- **WHEN** 执行 `hecate restore <backup-id> --workspace=<ws> --conflict=merge`
- **THEN** 系统对已存在的记录执行 UPDATE，对不存在的记录执行 INSERT，不删除目标侧的多余数据

#### Scenario: fail 模式——拒绝恢复
- **WHEN** 执行 `hecate restore <backup-id> --workspace=<ws> --conflict=fail` 且目标 workspace 已存在数据
- **THEN** 系统返回错误，拒绝执行恢复操作

#### Scenario: fail 模式——目标不存在时正常恢复
- **WHEN** 执行 `hecate restore <backup-id> --workspace=<ws> --conflict=fail` 且目标 workspace 不存在
- **THEN** 系统正常执行恢复，等同于 replace 模式

### Requirement: 恢复前确认
系统 SHALL 在执行恢复操作前要求用户确认，显示恢复范围和影响。

#### Scenario: CLI 确认提示
- **WHEN** 执行 `hecate restore <backup-id> --scope=all`
- **THEN** 系统显示恢复范围（备份日期、数据类型、大小）并要求用户输入 `yes` 确认

#### Scenario: API 强制确认参数
- **WHEN** 调用 `POST /api/system/restore` 且未提供 `confirm=true`
- **THEN** API 返回 400，要求提供确认参数
