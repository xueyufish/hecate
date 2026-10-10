# Proposal

## Why

Step6f 的关闭门槛是"PostgreSQL 上完整宿主故障矩阵、未决外部写和迟到回执"。进程级场景套件(SC04 租约续停/SC05 唤醒链与重连)已具备 PG 参数化(`HECATE_STEP6_POSTGRES_URL` 驱动的 `step6_runner_database_url` fixture),但 CI 从未设置该变量——主 `test` job 只跑 SQLite 参数;`migrations` job 覆盖 durable 核心故障与 Alembic 链,`step6-continuation-pg` 覆盖续跑并发层,宿主**进程级**矩阵仍是缺口。另外本地 Windows 的 Runner 子进程连 Docker PG 被系统拒绝(已归档的 10013 发现),该矩阵只能由 Linux CI 认证。

## What Changes

- 新增 CI job `step6-pg-process-matrix`:postgres:16 服务容器 + `HECATE_STEP6_POSTGRES_URL`,运行三个已参数化的进程级场景文件(SC04 租约续停、SC05 唤醒链、SC05 重连)——fixture 自动展开 `[sqlite, postgres]` 双参数,同一 job 内 SQLite 透视与 PG 矩阵同跑;安装 hecate-runner(主进程 import 需要)与 psycopg/asyncpg/pytest-timeout。
- 场景文件无代码改动(fixture 已就绪);`start_runner` 已按宿主数据库 URL 自动为 Runner 子进程安装 psycopg。
- CI 绿灯后在同一 PR 内翻转文档:演进方案 step6f 条目(追加修正:宿主故障矩阵的 PG 认证交付;SC03/SC06 保持部分覆盖,step7/10 的中心缓冲/补传限额与只读继续策略保留)、followup review 验收记录、场景清单 SC04/SC05 的 gaps 更新。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `platform-scenario-pack`:新增需求——受管宿主进程矩阵(SC04/SC05)SHALL 在 CI 的真实 PostgreSQL 上按参数化常态执行,SQLite 与 PostgreSQL 双参数同一 job 内运行,PG 参数缺失时诚实跳过不冒充通过。

## Impact

- `.github/workflows/ci.yml`:新增 `step6-pg-process-matrix` job(复制既有 postgres 服务容器与安装模式;新增 `uv pip install --no-deps -e packages/hecate-runner` 步骤——主进程 `tests.scenarios.tools.managed_platform` 顶层 import `hecate_runner`)。
- 无生产代码改动;无表/migration 改动。
- 运行时约 10–20 分钟(Linux CI 上 Runner 子进程连服务容器 PG 无 Windows 端口代理问题)。
