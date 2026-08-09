## Why

Hecate 当前升级方式为 `alembic upgrade head && uvicorn`——单进程启动时全量迁移后启动服务，每次升级需 5-30 秒停服。p3-mvp-audit 将此列为 P0 阻塞项（"无零停机升级能力。每次部署都需要停服。生产 SLA 不可接受"）。

业界调研（Dify / Coze / Salesforce / Google Gemini / IBM watsonx / 华为 AgentArts / Palantir AIP / Hermes / RapidClaw / Claude Code / Codex / deer-flow / AgentScope / AWS Bedrock）揭示三个关键教训：
1. **Dify #32297 教训**：`flask db upgrade` 启动时执行 + Redis lock 60s TTL → 多副本迁移竞态、schema 不一致。Hecate 当前模式与其完全相同。
2. **成熟平台模式**：迁移从启动路径分离（独立 Job / init container），health check 三端点分离（live/ready/startup），feature flag 与版本管理正交。
3. **Expand-contract 是唯一验证过的零停机 schema 演进模式**（komodoai 37 migrations / OpenStack Nova 10 年实践 / DevOpsNess）。

本 change 建立版本升级的代码级基础：health check 端点、独立迁移入口、Alembic expand-contract autogenerate、两层 feature flag 系统（boot-time + runtime DB-backed）、preflight validation、graceful shutdown、rollback runbook、blue-green 部署模板。

## What Changes

**P0（必须）**：

- **Health check 三端点**：`/health/live`（进程存活）、`/health/ready`（可服务检查：DB/Redis/Qdrant 连通性 + SIGTERM flag）、`/health/startup`（初始化完成）。K8s 三种 probe 对应三个端点。
- **SIGTERM graceful shutdown**：收到 SIGTERM 时立即将全局 flag 置为 draining → `/health/ready` 返回 503 → K8s/LB 摘除 Pod → 排干在途请求 → lifespan 关闭 DB/Redis/sandbox pool → 退出。
- **独立 `hecate-migrate` 二进制**：从 `docker-compose.yml` 的启动命令中分离迁移逻辑。`hecate-migrate` 是独立入口，执行 Alembic 迁移后退出。长运行服务不再包含迁移逻辑。docker-compose 用 `depends_on: condition: service_completed_successfully` 编排。
- **Alembic `lock_timeout` 安全网**：`env.py` 中 `SET lock_timeout = '2s'`，防止 DDL 锁等待雪崩。
- **`/version` API**：返回 `__version__`、git commit SHA、Alembic head revision、build date、Python version。

**P1（推荐）**：

- **两层 Feature Flag 系统**：
  - Tier 1 (boot-time)：`pydantic FeatureSettings` 布尔值，影响初始化路径，改变需重启。
  - Tier 2 (runtime)：`FeatureFlagModel` DB-backed + Redis 缓存。支持布尔开关、百分比灰度、per-tenant targeting、per-user allowlist。REST API 动态变更，不需重启。Feature flag lifecycle 状态机：`draft → active → deprecated → retired`。每次 evaluation 记录 OTel span。
- **Feature flag AST 审计工具**：`hecate flag-audit` CLI 命令扫描 `settings.ENABLE_*` 引用，输出 flag 体检报告（evaluation count、true ratio、code references、lifecycle status）。CI 中 `--check` 模式：deprecated flag 超过 `target_removal_version` → fail；零引用 flag → fail。
- **Alembic expand-contract autogenerate**：`env.py` 添加 `process_revision_directives` hook，`alembic revision --autogenerate` 自动将 schema 变化拆分为 expand revision（加列/加表/加索引）+ contract revision（删列/改类型），消除人因拆分错误。OpenStack Nova 模式。
- **Preflight check CLI + REST API**：`hecate preflight`（CLI）和 `GET /api/preflight`（REST）。检查 DB 连接/版本、Alembic head 匹配、Redis/Qdrant/MinIO 连通性、磁盘空间、feature flag 一致性。
- **Rollback runbook**：`docs/operations/rollback.md` 覆盖 4 种回滚路径：代码回滚（git revert + redeploy）、数据库回滚（alembic downgrade + expand-contract 安全约束）、feature flag 回滚（REST API 禁用，不需 redeploy）、blue-green 回滚（切换 nginx upstream）。
- **docker-compose blue-green 模板**：`docker/docker-compose.blue-green.yml`。两个 hecate 实例（blue/green）+ nginx，通过切换 upstream 实现零停机部署。`deploy/scripts/blue-green-switch.sh` 自动化切换。

## Capabilities

### New Capabilities

- `health-checks`: 基础设施级健康检查端点（`/health/live`、`/health/ready`、`/health/startup`）、SIGTERM graceful shutdown 排干逻辑、`/version` API。独立于业务层 `agent-health-monitoring`。
- `feature-flags`: 两层 feature flag 系统（boot-time pydantic + runtime DB-backed）、lifecycle 状态机（draft→active→deprecated→retired）、灰度 targeting（百分比/租户/用户）、AST 审计工具、CI 集成。
- `version-upgrade`: 版本升级基础设施——独立 `hecate-migrate` 二进制、Alembic expand-contract autogenerate、`lock_timeout` 安全网、preflight validation CLI + REST、rollback runbook、blue-green 部署模板。

### Modified Capabilities

无。本 change 不修改任何现有 spec——health check 端点、feature flag、迁移工具均为全新能力。

## Impact

### 受影响代码

- `src/hecate/main.py`：新增 `/health/live`、`/health/ready`、`/health/startup`、`/version` 路由；lifespan 增加 SIGTERM handler + `SHOULD_ACCEPT_TRAFFIC` flag
- `src/hecate/cli/migrate.py`：新建独立迁移入口
- `src/hecate/cli/preflight.py`：新建 preflight 检查入口
- `src/hecate/cli/flag_audit.py`：新建 feature flag 审计入口
- `src/hecate/core/config.py`：新增 `FeatureSettings`（boot-time flags）
- `src/hecate/models/feature_flag.py`：新建 `FeatureFlagModel` ORM
- `src/hecate/services/feature_flags/`：新建 feature flag 服务层（evaluation engine、Redis cache、REST API）
- `src/hecate/api/management/feature_flags.py`：新建 flag 管理 REST API
- `alembic/env.py`：`lock_timeout` + `process_revision_directives` autogenerate hook
- `pyproject.toml`：新增 `hecate-migrate` entry point
- `docker/docker-compose.yml`：分离 hecate-migrate 与 hecate 服务
- `docker/docker-compose.blue-green.yml`：新建
- `deploy/scripts/blue-green-switch.sh`：新建
- `docs/operations/rollback.md`：新建

### 受影响依赖

- 无新外部依赖（AST 审计用 Python 标准库 `ast`，Redis 已在 `[redis]` extra 中）

### 外部接口

- 新增 4 个公共 API：`/health/live`、`/health/ready`、`/health/startup`、`/version`
- 新增 feature flag 管理 API：`GET/POST /api/feature-flags`、`GET /api/feature-flags/{key}`
- 新增 preflight API：`GET /api/preflight`
- 新增 CLI 命令：`hecate-migrate`、`hecate preflight`、`hecate flag-audit`
- `docker-compose.yml` 启动行为变更：先跑 `hecate-migrate` 服务，成功后启动 `hecate` 服务
