## Context

Hecate 当前升级方式为 `alembic upgrade head && uvicorn`——Docker Compose 启动脚本中一次性执行迁移后启动服务。业界调研（15 个平台）揭示三个关键问题：

1. **Dify #32297 教训**：多副本环境下 `alembic upgrade head` 在每个 Pod 启动时执行，Redis lock 60s TTL 过期后导致并发迁移 → schema 不一致。Hecate 当前模式与其完全相同。
2. **Health check 端点缺失**：K8s 三种 probe（liveness/readiness/startup）需要对应 HTTP 端点。当前 `/health` 只返回 `{"status":"ok"}`，不检查依赖，无 SIGTERM 排干。
3. **Feature flag 系统缺失**：紧急 kill switch、灰度发布、功能开关全部依赖环境变量重启——无法运行时变更。

本 change 建立版本升级的代码级基础，使 Hecate 具备生产级版本管理能力。

## Goals / Non-Goals

**Goals:**

- Health check 三端点（live/ready/startup）+ SIGTERM graceful shutdown + `/version` API
- 独立 `hecate-migrate` 二进制，从启动路径中分离迁移逻辑
- Alembic `lock_timeout` 安全网 + expand-contract autogenerate（`process_revision_directives` hook）
- 两层 feature flag 系统（boot-time pydantic + runtime DB-backed + Redis cache）
- Feature flag lifecycle 状态机 + AST 审计工具 + CI 集成
- Preflight check CLI + REST API
- Rollback runbook + docker-compose blue-green 部署模板

**Non-Goals:**

- K8s manifests（Deployment/HPA/PDB）——归入 13.4 Horizontal Scaling
- Agent 级版本流量切分——归入未来 change（需 13.4 多副本基础）
- Argo Rollouts / Flagger canary 自动化——归入未来 change
- Multi-region / DR 部署——归入 EF4

## Decisions

### Decision 1: Health check 三端点分离（而非单端点不同 query param）

**三端点**：`/health/live`、`/health/ready`、`/health/startup`。

理由：
- K8s probe 配置最自然——每种 probe 一个 `httpGet.path`，不需要 query param 逻辑
- `/health/live` 必须超轻量（无 DB 检查），确保不因外部依赖抖动导致 Pod 误重启
- `/health/ready` 可重（检查所有依赖），因为 ready 失败只摘流量不重启
- `/health/startup` 解决慢启动问题（alembic 迁移 + lifespan 初始化可能 > 30s）

**替代方案（拒绝）**：`/health?type=live` 单端点 + query param——增加条件分支，K8s probe 配置更复杂，且 API gateway / LB 可能 strip query param。

### Decision 2: SIGTERM handler 用全局 flag 而非 FastAPI middleware

**全局 `SHOULD_ACCEPT_TRAFFIC` flag**：`signal.signal(SIGTERM, handler)` 在 handler 中置 flag 为 False，`/health/ready` 检查此 flag。

理由：
- Middleware 方案（在每次请求进出时检查 flag）增加每个请求的开销
- 全局 flag 方案只在 readiness probe 路径检查，不影响正常请求
- 社区验证过的模式（dev.to/FastAPI graceful shutdown 文章 + K8s preStop hook 最佳实践）

`ACTIVE_REQUESTS` 计数器用 `asyncio` middleware 实现（仅在 middleware 层 increment/decrement，不影响业务逻辑）。

### Decision 3: hecate-migrate 作为独立 entry point 而非子命令

**独立 `hecate-migrate`**：`pyproject.toml [project.scripts]` 注册独立入口。

理由：
- 单一职责：迁移入口不混入 CLI 的 agent/workflow 管理逻辑
- Docker/K8s 编排清晰：`command: hecate-migrate` vs `command: uvicorn ...`
- 可独立版本化（未来迁移工具可能有自己的 release cadence）
- `hecate migrate` 子命令混入现有 typer CLI 会增加 `hecate --help` 噪声

### Decision 4: Expand-contract autogenerate 用 Alembic 原生 hook

**`process_revision_directives`**：Alembic 原生 API，在 `env.py` 的 `context.configure()` 中传入。autogenerate 完成后，hook 拦截 `ops` 列表，按 operation 类型拆分为 expand list + contract list，分别写入两个 revision 文件。

理由：
- OpenStack Nova 10 年验证过的模式（that.guru 详细文档）
- 不需要外部工具或自定义 fork——Alembic 原生支持
- 开发者仍可手动写 revision（不 autogenerate 的情况）——hook 只在 `--autogenerate` 时触发
- Expand revision + contract revision 形成线性链（contract down_revision = expand），不需要 branch label（branch label 在 OpenStack 早期用过但后来简化为线性链）

**不选 branch label**：branch label 在 Alembic 中增加 merge complexity。线性链更简单，`hecate-migrate --expand-only` 只需要跑到 expand revision 的 `down_revision` 即可。

### Decision 5: 两层 Feature Flag（boot-time + runtime）

**Tier 1 (boot-time)**：`FeatureSettings(BaseSettings)` 在 `config.py`。影响初始化路径的 flag（如 backend 选择）。

**Tier 2 (runtime)**：`FeatureFlagModel` ORM + Redis 缓存。支持灰度 / per-tenant / per-user。

理由：
- 有些 flag 必须在进程启动时确定（如 `SESSION_STATE_STORE_BACKEND`——启动后不能动态切换 Redis → Postgres）
- 有些 flag 应该运行时可变（如紧急 kill switch——不应为了关一个功能而重启所有副本）
- 两层分离确保各自的最优行为：Tier 1 零开销（直接读 settings），Tier 2 低开销（Redis cache hit < 0.1ms）

**不选纯 runtime（拒绝）**：如果所有 flag 都 DB-backed，启动期 flag 也需要 DB 连接才能评估——这在 DB 不可达时导致 chicken-and-egg 问题。

### Decision 6: Feature flag lifecycle 状态机 + 自动淘汰

**状态机**：`draft → active → deprecated → retired → deleted`

理由：
- Python 动态语言不报 stale flag——需要系统级机制
- `deprecated` + `target_removal_version` 给出明确的清理 deadline
- `retired` + AST 审计 = 自动发现未清理的引用
- CI `--check` 模式让过期 flag 无法合并——从流程上消除 flag 债务

**eval_count / last_true_count 自动标记**：`last_true_count / eval_count == 1.0` 持续 N 天的 active flag，系统自动建议转为 `deprecated`（通过 `hecate flag-audit` 输出）。

### Decision 7: Consistent hash 用于 percentage 灰度

**`hashlib.sha256(f"{flag_key}:{user_id}").hexdigest()` 取前 8 字节 mod 100**。

理由：
- 不引入 `mmh3` 外部依赖——Python 标准库 `hashlib` 足够
- consistent hash 确保同一用户在同一 flag 下总是得到相同结果（不 flip-flop）
- `flag_key` 参与 hash 确保不同 flag 的灰度分布独立

## Risks / Trade-offs

- [Health check DB 检查增加延迟] `/health/ready` 每次检查 DB/Redis/Qdrant 连通性，K8s 默认 5s 探测一次。→ **Mitigation**：使用连接池已有的 connection check（`SELECT 1`），latency < 1ms。连接池本身维护心跳，不需要新建连接。
- [Feature flag Redis 缓存一致性] REST API 变更后到所有副本生效有 TTL 延迟（5s）。→ **Mitigation**：变更时立即 `DEL feature_flag:{key}`，下一个请求从 DB 重新加载。5s TTL 是 worst case。
- [Expand-contract autogenerate 误拆] `AlterColumnOp`（如 nullable → NOT NULL）的分类可能不明确。→ **Mitigation**：hook 对不确定的操作 raise `NotImplementedError`，要求开发者手动分类。宁可失败不可静默误分类。
- [SIGTERM 排干超时] 慢请求（如 LLM streaming）可能 > 30s。→ **Mitigation**：`SHUTDOWN_DRAIN_TIMEOUT` 可配置。LLM streaming 请求应有自己的客户端超时机制。超时后强制关闭是可接受的（客户端 retry）。
- [Feature flag Tier 2 需要数据库表] 引入 `feature_flags` 表和 migration。→ **Mitigation**：这是标准实践。表很小（通常 < 50 行），不影响性能。

## Migration Plan

本 change 引入的新表：`feature_flags`（FeatureFlagModel）。

迁移步骤：
1. `hecate-migrate` 创建 `feature_flags` 表（expand migration——新建表，无锁风险）
2. 启动后 FeatureFlagModel 可用，但无数据——所有 Tier 2 flag 默认不存在（评估返回 False）
3. 操作员通过 REST API 创建需要的 flag（`POST /api/feature-flags`）

**docker-compose.yml 变更**：
- 原：`command: sh -c "alembic upgrade head && uvicorn ..."`
- 新：`hecate-migrate` 一次性服务 + `hecate` 服务 `depends_on: hecate-migrate`

**回滚策略**：`hecate-migrate --downgrade 1` 回退 `feature_flags` 表创建。回滚前确保 `feature_flags` 表为空（无业务数据依赖）。

## Open Questions

无。所有架构决策已通过 explore 阶段讨论确认。
