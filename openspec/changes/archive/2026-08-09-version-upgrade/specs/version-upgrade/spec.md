## ADDED Requirements

### Requirement: hecate-migrate 独立迁移二进制

`pyproject.toml` SHALL 注册 `hecate-migrate` 入口点：`hecate-migrate = "hecate.cli.migrate:main"`。

`src/hecate/cli/migrate.py` SHALL 提供独立迁移 CLI 工具，执行 Alembic 迁移后退出（不启动 uvicorn）。

命令行参数：
- `hecate-migrate`：执行 `alembic upgrade head`（默认行为）
- `hecate-migrate --check`：只检查 pending 迁移数量，不执行。输出 JSON `{"pending": N, "current": "rev_id", "head": "rev_id"}`。退出码 0 = 无 pending，1 = 有 pending。
- `hecate-migrate --downgrade N`：回退 N 个 revision。
- `hecate-migrate --expand-only`：只执行 expand 分支迁移（配合 expand-contract autogenerate）。
- `hecate-migrate --contract-only`：只执行 contract 分支迁移。

`hecate-migrate` SHALL 在迁移失败时退出码非零，输出 stderr 错误信息。

`docker-compose.yml` SHALL 将迁移逻辑从 `hecate` 服务分离到 `hecate-migrate` 一次性服务。`hecate` 服务 `depends_on: hecate-migrate: condition: service_completed_successfully`。

#### Scenario: hecate-migrate 执行所有 pending 迁移后退出
- **WHEN** `hecate-migrate` 被执行
- **THEN** 运行 `alembic upgrade head`
- **THEN** 成功后进程退出码 0，不启动 uvicorn

#### Scenario: --check 模式报告 pending 迁移
- **WHEN** `hecate-migrate --check` 被执行且有 3 个 pending migration
- **THEN** 输出 `{"pending": 3, ...}`
- **THEN** 退出码 1

#### Scenario: docker-compose 先迁移后启动
- **WHEN** `docker compose up -d` 被执行
- **THEN** `hecate-migrate` 服务先启动、执行迁移、成功退出
- **THEN** `hecate` 服务在 `hecate-migrate` 成功退出后启动 uvicorn
- **THEN** `hecate` 服务的 command 不含 `alembic upgrade`

### Requirement: Alembic lock_timeout 安全网

`alembic/env.py` SHALL 在每次迁移连接上执行 `SET lock_timeout = '2s'`（可通过 `ALEMBIC_LOCK_TIMEOUT` 环境变量配置，默认 `'2s'`）。

该设置确保 DDL 操作无法获取锁时在 2 秒内失败（而非无限等待导致连接堆积），提供 fast-fail 安全网。

#### Scenario: lock_timeout 防止锁等待雪崩
- **WHEN** 迁移执行 `ALTER TABLE` 但目标表被长事务锁定
- **THEN** 迁移在 2 秒内失败（`lock_timeout` 错误），不无限等待
- **THEN** 错误信息明确指出是 lock_timeout

#### Scenario: lock_timeout 可通过环境变量配置
- **WHEN** 环境变量 `ALEMBIC_LOCK_TIMEOUT=10s` 被设置
- **THEN** 迁移连接执行 `SET lock_timeout = '10s'`

### Requirement: Alembic expand-contract autogenerate 自动拆分

`alembic/env.py` SHALL 配置 `process_revision_directives` hook，当 `alembic revision --autogenerate` 被调用时，自动将检测到的 schema 变化拆分为两个 revision 文件：

1. **Expand revision**（`{description}_expand.py`）：包含所有加列、加表、加索引、加约束操作
2. **Contract revision**（`{description}_contract.py`）：包含所有删列、删表、改类型、删约束操作

Expand revision 的 `down_revision` 指向当前 head。Contract revision 的 `down_revision` 指向 Expand revision。两个 revision 形成线性链。

实现 SHALL 使用 `alembic.operations.ops` 中的 operation 类型分类：
- Expand：`CreateTableOp`、`AddColumnOp`、`CreateIndexOp`、`CreateConstraintOp`
- Contract：`DropTableOp`、`DropColumnOp`、`DropIndexOp`、`DropConstraintOp`、`AlterColumnOp`（类型变更归 contract）
- 纯 nullable → NOT NULL 变更归 contract（需要先 backfill）

`hecate-migrate --expand-only` SHALL 只执行到 expand revision。`hecate-migrate --contract-only` SHALL 执行 contract revision。

#### Scenario: autogenerate 产生两个 revision 文件
- **WHEN** 开发者在 model 中添加了新列并删除了旧列，运行 `alembic revision --autogenerate -m "swap_columns"`
- **THEN** 产生 `swap_columns_expand.py`（含 AddColumn）和 `swap_columns_contract.py`（含 DropColumn）
- **THEN** contract 的 `down_revision` 指向 expand

#### Scenario: expand-only 模式只执行加列迁移
- **WHEN** `hecate-migrate --expand-only` 被执行
- **THEN** 只执行 expand revision（新列被添加，旧列仍在）
- **THEN** 新旧代码可共存（新列 nullable，旧列仍有数据）

#### Scenario: contract-only 模式在所有副本升级后执行
- **WHEN** 所有副本已升级到新版本（旧列不再被代码引用），`hecate-migrate --contract-only` 被执行
- **THEN** 执行 contract revision（旧列被删除）

### Requirement: hecate preflight 预检 CLI + REST API

`src/hecate/cli/preflight.py` SHALL 提供 CLI 命令 `hecate preflight`。

`src/hecate/api/management/preflight.py` SHALL 提供 `GET /api/preflight` REST 端点。

两者 SHALL 执行相同的检查集，返回结构化结果：

| 检查项 | 检测内容 | 失败行为 |
|--------|---------|---------|
| `database` | `SELECT 1` + PostgreSQL 版本 >= 16 | FAIL |
| `alembic_head` | 当前 DB revision == 代码 alembic head | FAIL |
| `redis` | `PING`（如 SESSION_STATE_STORE_BACKEND != "memory"） | WARN（可选依赖） |
| `qdrant` | health API 调用 | WARN |
| `minio` | bucket 列表调用 | WARN |
| `disk_space` | 数据目录可用空间 > 1GB | FAIL |
| `env_vars` | 必需环境变量（DATABASE_URL 等）全部设置 | FAIL |
| `feature_flags` | 无 deprecated flag 处于 active 评估状态 | WARN |

CLI 输出表格格式 + 退出码（0 = 全部通过，1 = 有 FAIL，2 = 只有 WARN）。

#### Scenario: 所有检查通过
- **WHEN** `hecate preflight` 被执行且所有依赖可达
- **THEN** 输出所有检查项 PASS
- **THEN** 退出码 0

#### Scenario: DB 不可达导致 FAIL
- **WHEN** 数据库连接失败
- **THEN** `database` 检查项 FAIL
- **THEN** 退出码 1

#### Scenario: REST API 返回相同结果
- **WHEN** `GET /api/preflight` 被调用
- **THEN** 返回 JSON `{"checks": [...], "ready": false, "failures": ["database"]}`

### Requirement: Rollback runbook 文档

`docs/operations/rollback.md` SHALL 存在，覆盖 4 种回滚路径：

1. **代码回滚**：`git revert + docker compose build && docker compose up -d`（最快路径，适用于任何场景）
2. **数据库回滚**：`hecate-migrate --downgrade N`（需遵守 expand-contract 约束——contract revision 不可回滚到 expand 之前如果新代码已写入依赖新 schema 的数据）
3. **Feature flag 回滚**：`POST /api/feature-flags/{key}/transition` body `{"status": "active", "enabled": false}`（不需 redeploy，秒级生效，适用于功能级紧急关闭）
4. **Blue-green 回滚**：`deploy/scripts/blue-green-switch.sh rollback`（切换 nginx upstream 回旧版本，秒级生效）

文档 SHALL 包含决策树："遇到什么问题用哪种回滚？"

#### Scenario: 文档存在且包含 4 种路径
- **WHEN** 开发者打开 `docs/operations/rollback.md`
- **THEN** 包含代码回滚、数据库回滚、feature flag 回滚、blue-green 回滚 4 节
- **THEN** 包含决策树帮助选择回滚路径

### Requirement: docker-compose blue-green 部署模板

`docker/docker-compose.blue-green.yml` SHALL 定义两个 hecate 服务实例（`hecate-blue` 和 `hecate-green`）+ `nginx` 负载均衡器。

`deploy/scripts/blue-green-switch.sh` SHALL 提供 `active` / `status` / `rollback` 子命令：
- `blue-green-switch.sh active blue`：将 nginx upstream 切换到 blue 实例
- `blue-green-switch.sh active green`：切换到 green 实例（部署新版本时用）
- `blue-green-switch.sh status`：显示当前活跃实例
- `blue-green-switch.sh rollback`：切换回上一个活跃实例

部署新版本流程：
1. 当前 blue 活跃 → 在 green 上部署新版本镜像 + `hecate-migrate`
2. `blue-green-switch.sh active green` 切换流量到 green
3. 验证 green 正常 → blue 可保留旧版本供快速回滚
4. 如 green 异常 → `blue-green-switch.sh rollback` 秒级切回 blue

#### Scenario: blue-green 切换零停机
- **WHEN** `blue-green-switch.sh active green` 被执行且 green 实例健康
- **THEN** nginx upstream 从 blue 切换到 green
- **THEN** 用户请求无中断（nginx 热重载）

#### Scenario: rollback 秒级切回旧版本
- **WHEN** green 实例异常，`blue-green-switch.sh rollback` 被执行
- **THEN** nginx upstream 切回 blue（旧版本仍在运行）
- **THEN** 切换时间 < 2 秒
