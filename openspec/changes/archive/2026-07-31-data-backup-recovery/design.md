## Context

Hecate 是一个自托管的多租户 Agent 平台，持久化数据分布在 4 个存储层：

| 存储 | 技术 | 数据内容 | 备份特征 |
|------|------|---------|---------|
| 主数据库 | PostgreSQL 16 | 79 个 ORM 模型（agents, conversations, messages, workflows, users, RBAC, audit, evaluations, traces 等） | 结构化、ACID、有租户隔离字段（workspace_id） |
| 向量库 | Qdrant | 知识库 embedding 索引，per-KB collection（`kb_{uuid_hex}`） | 非结构化、重建成本高（100K 文档 re-embed 要数小时） |
| 对象存储 | MinIO | 用户上传文档、解析内容、审计归档、fine-tuning 数据集 | 文件型、按路径组织 |
| 文件系统 | 本地 FS | workspace 目录、agent environment、plugins、context offloading | 文件型、per-session/per-agent |

当前状态：**零备份机制**。PostgreSQL 运行在 Docker 容器中，Qdrant 和 MinIO 使用 Docker volume。任何存储故障 = 数据永久丢失。

业界参考：Dify 仅提供"停服 + tar volumes"方案，无 API/调度/验证。云平台（Bedrock, watsonx）依赖基础设施层备份。业界最佳实践文章（RapidClaw, CallSphere, QubitTool）一致建议：PostgreSQL WAL + PITR、Qdrant snapshot → S3 跨区域复制、3-2-1 法则。

## Goals / Non-Goals

**Goals:**

- 为所有 4 个持久化存储提供统一的备份与恢复能力
- 支持 4 种恢复粒度：全量、按数据类型、按租户（workspace_id）、PITR 时间点
- 备份存储支持内部 MinIO bucket + 外部 S3 兼容存储（AWS S3 / GCS / R2）
- 定时自动备份 + 保留策略可配置
- 备份验证：恢复到临时实例并校验数据完整性
- CLI + REST API 管理接口
- 恢复冲突策略：replace / merge / fail
- Platform Admin 权限控制

**Non-Goals:**

- 不做跨区域 active-active 复制（属于 Horizontal Scaling 13.4 的范畴）
- 不做向量库的 streaming replication（Qdrant 不原生支持，用 snapshot 足够）
- 不做 per-tenant PITR（复杂度极高，业界无先例，通过"全量 PITR + 按租户提取"两步实现）
- 不做备份加密（可后续增强，当前依赖存储层加密）
- 第一版不做 Org/Workspace 级别的自助备份权限（仅 Platform Admin）
- 不做 UI 看板（CLI + API 足够）

## Decisions

### D1: PostgreSQL 备份策略——pg_dump + WAL archive

**决策**：采用 `pg_dump -Fc`（custom format）做全量备份，配合 WAL archive 做持续归档，支持 PITR。

**理由**：
- `pg_dump -Fc` 是 PostgreSQL 推荐的逻辑备份格式，支持并行恢复（`pg_restore -j`）
- WAL archive 提供 RPO ≈ 0 的连续保护，`pg_basebackup` + WAL replay 实现 PITR
- 相比 `pg_dump` 定时备份（RPO = 备份间隔），WAL 方案 RPO 更低
- 业界共识：CallSphere 文章推荐"streaming replication + WAL archiving + periodic full snapshots"三层方案

**替代方案**：
- 仅 `pg_dump` 定时备份（RPO = 备份间隔，最简单但不满足 RPO ≈ 0）
- 逻辑复制（Logical Replication）到备用实例（更复杂，用于读副本而非备份）
- 云托管 RDS 自动备份（Hecate 是自托管，不适用）

**实现**：
- 全量备份：`pg_dump -Fc -f backup.dump` → 上传到 backup storage
- WAL archive：PostgreSQL `archive_command` 配置为 `cp %p /archive/%f` 或直接上传 S3
- PITR 恢复：`pg_basebackup` + `recovery_target_time` + WAL replay

### D2: Qdrant 备份策略——snapshot API per collection

**决策**：遍历所有 collection，对每个 collection 调用 Qdrant snapshot API，将 snapshot 文件上传到 backup storage。

**理由**：
- Qdrant 原生支持 snapshot API（`POST /collections/{name}/snapshots`），创建一致性快照
- 每个 Knowledge Base 独立 collection（`kb_{uuid_hex}`），天然支持按租户恢复
- 相比从原始文档重建（re-embed），snapshot 恢复速度快几个数量级
- 业界共识："Back it up like a database, because that's what it is."（RapidClaw）

**替代方案**：
- 从 MinIO 原始文档重建索引（耗时数小时 + API 费用，仅作最后手段）
- Qdrant Raft replication（用于高可用，非备份）

### D3: MinIO 备份策略——mc mirror

**决策**：使用 MinIO Client（`mc`）的 `mirror` 命令进行增量同步到 backup storage。

**理由**：
- `mc mirror` 只同步变更的文件，效率高于全量复制
- 支持指定 prefix 过滤，便于按租户恢复
- MinIO 原生工具，无额外依赖

**替代方案**：
- `mc cp --recursive`（全量复制，效率低）
- S3 Cross-Region Replication（基础设施层，Hecate 不直接管理）

### D4: 备份存储——双目标支持

**决策**：支持两种备份存储目标，通过配置切换：
- 内部：MinIO 的独立 bucket（`hecate-backups`），与主数据 bucket 分离
- 外部：S3 兼容存储（AWS S3、GCS、Cloudflare R2 等），通过 endpoint/access_key/secret_key 配置

**理由**：
- 内部 MinIO bucket 最简单，适合开发/测试环境
- 外部 S3 提供物理隔离，生产环境推荐
- 3-2-1 法则：3 份数据、2 种介质、1 份离线

**配置**：
```
BACKUP_STORAGE_TYPE=minio|s3
BACKUP_MINIO_BUCKET=hecate-backups
BACKUP_S3_ENDPOINT=https://s3.amazonaws.com
BACKUP_S3_BUCKET=hecate-prod-backups
BACKUP_S3_ACCESS_KEY=...
BACKUP_S3_SECRET_KEY=...
```

### D5: 恢复粒度——四层模型

**决策**：支持 4 种恢复粒度：

| 粒度 | 实现方式 | 适用场景 |
|------|---------|---------|
| **全量恢复** | pg_restore + Qdrant snapshot restore + mc mirror + rsync | 灾难恢复：全部数据丢失 |
| **按数据类型** | 选择性恢复 PG / Qdrant / MinIO / FS 中的一个或多个 | 部分故障：如向量库损坏但 PG 正常 |
| **按租户** | 从全量备份提取 workspace_id=X 的数据 | 租户数据损坏或迁移 |
| **PITR** | WAL replay 到指定时间点 | 误操作恢复：如误删数据后回退 |

**按租户恢复的技术路径**：
1. PostgreSQL：从 pg_dump 恢复到临时 DB → 逐表 `SELECT * WHERE workspace_id = X` → INSERT 到目标 DB
2. Qdrant：查询 PG 获取 workspace 关联的 knowledge_bases → 恢复对应的 collection snapshots
3. MinIO：查询 PG 获取 workspace 关联的 document.file_path → 按路径恢复文件
4. Filesystem：按 `WORKSPACE_ROOT/workspace_{ws_id}/` 路径恢复

**租户级表分析**（有 workspace_id 的表 vs 无的表）：

有 workspace_id（租户级，纳入按租户恢复）：
- agents, conversations, messages, knowledge_bases, documents, workflows, tools, skills, prompts, evaluations, datasets, alerts, quotas, budgets, plugins, workspace_members 等

无 workspace_id（平台级，不纳入按租户恢复）：
- organizations, workspaces, users, model_providers, model_deployments, model_pricing, traces（系统级）

### D6: 恢复冲突策略——三种模式

**决策**：提供三种冲突处理模式：

| 模式 | 行为 | 适用场景 |
|------|------|---------|
| `replace` | DELETE 目标 workspace 数据 → INSERT 从备份提取的数据 | 灾难恢复 |
| `merge` | UPSERT：存在则覆盖，不存在则插入，不删除目标侧多余数据 | 数据迁移/同步 |
| `fail` | 目标 workspace 已存在则拒绝恢复 | 安全操作，防止误覆盖 |

**理由**：
- 业界无平台在备份层内置智能冲突解决（Salesforce 用 upsert，AWS RDS 整库覆盖）
- 三种模式覆盖最常见的恢复场景
- `fail` 作为默认模式，最安全

### D7: 调度——APScheduler + BackupRecord

**决策**：使用 APScheduler 驱动定时备份，BackupRecord ORM 记录每次备份元数据。

**理由**：
- APScheduler 是 Python 生态成熟的调度库，支持 cron 表达式
- 已在 pyproject.toml 的 `[scheduling]` dependency group 中声明
- BackupRecord 提供备份历史追踪、状态管理、存储位置记录

**BackupRecord schema**：
```python
class BackupRecordModel(BaseModel):
    __tablename__ = "backup_records"
    
    backup_type: str          # "full" | "incremental" | "wal"
    scope: str                # "system" | "pg" | "qdrant" | "minio" | "fs"
    status: str               # "pending" | "running" | "completed" | "failed"
    storage_type: str         # "minio" | "s3"
    storage_path: str         # 备份文件存储路径
    size_bytes: int | None    # 备份大小
    checksum: str | None      # SHA256 校验和
    started_at: datetime
    completed_at: datetime | None
    error_message: str | None
    metadata_: dict | None    # 额外元数据（row counts, collection counts 等）
```

### D8: 验证——恢复到临时实例 + 数据校验

**决策**：备份完成后可选触发验证流程：恢复到临时 PostgreSQL 实例，校验 row count 与备份时记录的 count 一致。

**理由**：
- "An untested backup is not a backup"（业界共识）
- 恢复到临时实例是最可靠的验证方式
- 校验 row count + collection count 是轻量但有效的完整性检查

**验证流程**：
1. 启动临时 PostgreSQL Docker 容器
2. pg_restore 到临时实例
3. 查询 row count per table，与 BackupRecord.metadata 中记录的 count 对比
4. 记录验证结果到 BackupRecord
5. 销毁临时容器

## Risks / Trade-offs

- **[风险] WAL archive 需要 PostgreSQL 配置变更**：`archive_mode=on` + `archive_command` 需要修改 postgresql.conf 或 Docker 环境变量。→ 缓解：提供文档和 Docker Compose 配置模板，首次备份前自动检测 WAL 配置状态
- **[风险] 按租户恢复的级联完整性**：workspace 数据通过 FK 关联，按 workspace_id 删除可能违反 FK 约束。→ 缓解：按依赖顺序删除（先删子表再删父表），使用 `ON DELETE CASCADE` 或手动级联
- **[风险] 大规模 PostgreSQL 恢复耗时**：79 张表的 pg_restore 可能需要数分钟到数十分钟。→ 缓解：使用 `pg_restore -j 4`（并行恢复），在设计中明确预期恢复时间
- **[风险] Qdrant snapshot 期间性能影响**：snapshot 创建会读取磁盘，可能影响查询性能。→ 缓解：在低峰期调度备份，Qdrant snapshot 是异步操作
- **[权衡] pg_dump vs pg_basebackup**：pg_dump 是逻辑备份（可按表恢复），pg_basebackup 是物理备份（更快但整库）。→ 决策：全量用 pg_dump（支持按租户恢复），WAL archive 用 pg_basebackup 模式（支持 PITR）
- **[权衡] 验证频率 vs 资源消耗**：每次备份都验证需要启动临时 DB，消耗资源。→ 决策：验证为可选操作，默认每周验证一次

## Migration Plan

1. **前置条件**：修改 PostgreSQL 配置启用 WAL archive（需重启 PG）
2. **数据库迁移**：Alembic migration 创建 `backup_records` 表
3. **代码部署**：部署 backup 服务代码
4. **首次全量备份**：触发首次全量备份，验证所有 4 个存储的备份完整性
5. **配置调度**：配置定时备份调度（默认每日全量 + 每小时增量）
6. **回滚**：备份功能独立于业务逻辑，可安全移除而不影响运行中的服务

## Open Questions

- 备份文件的加密是否需要在第一版实现？（当前方案依赖存储层加密）
- 备份文件的跨区域复制是否纳入本 change？（当前仅支持单目标存储）
- 是否需要备份文件的保留策略自动清理？（当前保留策略仅控制不创建新备份，不自动删除旧备份）
