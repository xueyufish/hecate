## 1. Health Check 端点

- [x] 1.1 在 `src/hecate/main.py` 添加 `GET /health/live` 端点，返回 `{"status":"alive"}`，不检查外部依赖
- [x] 1.2 在 `src/hecate/main.py` 添加 `GET /health/ready` 端点，检查 `SHOULD_ACCEPT_TRAFFIC` flag + DB `SELECT 1` + Redis `PING`（如配置）+ Qdrant health API，全部通过返回 200，任一失败返回 503 + failed 列表
- [x] 1.3 在 `src/hecate/main.py` 添加 `GET /health/startup` 端点，lifespan 完成前返回 503，完成后返回 200
- [x] 1.4 在 `src/hecate/main.py` 添加 `GET /version` 端点，返回 `__version__` / `GIT_COMMIT` / alembic head / Python version / `BUILD_DATE`
- [x] 1.5 在 `tests/test_api/test_health.py` 编写端点测试（live 200 / ready 200+503 / startup 503→200 / version 字段完整）

## 2. SIGTERM Graceful Shutdown

- [ ] [x]在 `src/hecate/main.py` 定义全局 `SHOULD_ACCEPT_TRAFFIC: bool = True` 和 `ACTIVE_REQUESTS: int = 0`
- [x] 2.2 注册 `signal.signal(signal.SIGTERM, _handle_sigterm)` handler，置 `SHOULD_ACCEPT_TRAFFIC = False`
- [x] 2.3 添加 ASGI middleware 在每个请求进入时 `ACTIVE_REQUESTS += 1`、退出时 `-= 1`
- [x] 2.4 lifespan shutdown 部分增加 `await _drain_requests()` 逻辑：等待 `ACTIVE_REQUESTS == 0` 或 `SHUTDOWN_DRAIN_TIMEOUT` 超时
- [x] 2.5 在 `src/hecate/core/config.py` 添加 `SHUTDOWN_DRAIN_TIMEOUT: int = 30` 配置
- [x] 2.6 在 `tests/test_api/test_health.py` 编写 SIGTERM 排干测试（flag 置 False → ready 返回 503 → drain 等待 → 超时强制关闭）

## 3. hecate-migrate 独立迁移入口

- [ ] [x]创建 `src/hecate/cli/migrate.py`，实现 `main()` 函数（argparse: 默认 `upgrade head` / `--check` / `--downgrade N` / `--expand-only` / `--contract-only`）
- [x] 3.1 创建 `src/hecate/cli/migrate.py`，实现 `main()` 函数（argparse: 默认 `upgrade head` / `--check` / `--downgrade N` / `--expand-only` / `--contract-only`）
- [x] 3.2 在 `pyproject.toml` `[project.scripts]` 注册 `hecate-migrate = "hecate.cli.migrate:main"`
- [x] 3.3 修改 `docker/docker-compose.yml`：新增 `hecate-migrate` 一次性服务（`command: hecate-migrate`、`restart: "no"`），`hecate` 服务 `depends_on: hecate-migrate: condition: service_completed_successfully`，`hecate` 服务 command 移除 `alembic upgrade head &&`
- [x] 3.4 在 `tests/test_cli/test_migrate.py` 编写 migrate CLI 测试（upgrade 成功退出 / --check 输出 JSON / --downgrade 回退）

## 4. Alembic lock_timeout + Expand-Contract Autogenerate

- [ ] [x]在 `alembic/env.py` 的 `run_migrations_online` 中添加 `SET lock_timeout = %s` 执行（值从 `ALEMBIC_LOCK_TIMEOUT` 环境变量读取，默认 `'2s'`）
- [x] 4.2 在 `alembic/env.py` 实现 `process_revision_directives` hook 函数：拦截 autogenerate 的 `ops` 列表，按 operation 类型拆分为 expand ops（`CreateTableOp` / `AddColumnOp` / `CreateIndexOp` / `CreateConstraintOp`）和 contract ops（`DropTableOp` / `DropColumnOp` / `DropIndexOp` / `DropConstraintOp` / `AlterColumnOp`），不确定的 op raise `NotImplementedError`
- [x] 4.3 在 `alembic/env.py` 的 `context.configure()` 调用中传入 `process_revision_directives=hook`
- [x] 4.4 创建 `docs/migrations/expand-contract-guide.md` 编写 expand-contract 迁移编写规范
- [x] 4.5 在 `tests/test_migrations/` 编写 autogenerate hook 测试（模拟 AddColumn + DropColumn → 产生两个 revision / 不确定 op raise NotImplementedError）

## 5. Feature Flag 两层系统

- [x] 5.1 在 `src/hecate/core/config.py` 添加 `FeatureSettings(BaseSettings)` 类，包含 boot-time 布尔 flag（初始为空，按需添加）+ `@model_validator` 跨字段校验
- [x] 5.2 创建 `src/hecate/models/feature_flag.py` 定义 `FeatureFlagModel(Base)` ORM（key / status / enabled / targeting_rules / description / created_at / updated_at / target_removal_version / evaluation_count / last_true_count）
- [x] 5.3 创建 Alembic migration（expand-only）生成 `feature_flags` 表 + 索引 `idx_feature_flags_status`
- [x] 5.4 创建 `src/hecate/services/feature_flags/__init__.py` 导出 `FeatureFlagService`
- [x] 5.5 创建 `src/hecate/services/feature_flags/evaluator.py` 实现 `async def evaluate(key, *, tenant_id=None, user_id=None) -> bool`（Redis cache → DB fallback → targeting rules 评估 → consistent hash → OTel span）
- [x] 5.6 创建 `src/hecate/services/feature_flags/service.py` 实现 CRUD + lifecycle transition + Redis cache invalidation
- [x] 5.7 创建 `src/hecate/services/feature_flags/redis_cache.py` 封装 Redis flag cache 读写（TTL 5s）
- [x] 5.8 在 `src/hecate/core/deps_feature_flags.py` 实现 FastAPI 依赖注入 `get_feature_flag_service`
- [x] 5.9 创建 `src/hecate/api/management/feature_flags.py` REST API（GET 列表 / GET 单个 / POST 创建 / PATCH 更新 / POST transition / DELETE 删除）
- [x] 5.10 在 `src/hecate/main.py` 注册 feature flags router
- [x] 5.11 在 `src/hecate/main.py` lifespan 中初始化 `app.state.feature_flag_service` 单例
- [x] 5.12 在 `tests/test_models/test_feature_flag.py` 编写 ORM 测试
- [x] 5.13 在 `tests/test_services/test_feature_flags/` 编写 evaluator / service / cache 测试
- [x] 5.14 在 `tests/test_api/test_feature_flags_api.py` 编写 REST API 测试

## 6. Feature Flag AST 审计工具

- [x] 6.1 创建 `src/hecate/cli/flag_audit.py` 实现 `hecate flag-audit` CLI（默认表格模式 + `--check` CI 模式）
- [x] 6.2 实现 AST 扫描逻辑：`ast.walk` 遍历 `src/hecate/**/*.py`，匹配 `settings.ENABLE_*` 和 `settings.feature_settings.ENABLE_*` 属性引用
- [x] 6.3 实现 `--check` 模式退出码逻辑：deprecated 超过 target_removal_version → FAIL；retired 有引用 → FAIL；draft 零评估超 30 天 → WARN
- [x] 6.4 在 `pyproject.toml` `[project.scripts]` 注册 `hecate-flag-audit = "hecate.cli.flag_audit:main"`（或作为 `hecate` 子命令）
- [x] 6.5 在 `tests/test_cli/test_flag_audit.py` 编写审计工具测试（检测引用 / 过期 flag FAIL / retired 有引用 FAIL）

## 7. Preflight Check

- [x] 7.1 创建 `src/hecate/cli/preflight.py` 实现 `hecate preflight` CLI
- [x] 7.2 创建 `src/hecate/services/preflight.py` 实现 `async def run_checks() -> list[CheckResult]`（database / alembic_head / redis / qdrant / minio / disk_space / env_vars / feature_flags）
- [x] 7.3 创建 `src/hecate/api/management/preflight.py` 实现 `GET /api/preflight` REST 端点
- [x] 7.4 在 `src/hecate/main.py` 注册 preflight router
- [x] 7.5 在 `tests/test_cli/test_preflight.py` 编写 preflight CLI 测试

## 8. Rollback Runbook + Blue-Green 部署模板

- [x] 8.1 创建 `docs/operations/rollback.md`，覆盖 4 种回滚路径（代码回滚 / 数据库回滚 / feature flag 回滚 / blue-green 回滚）+ 决策树
- [x] 8.2 创建 `docker/docker-compose.blue-green.yml`（hecate-blue + hecate-green + nginx + hecate-migrate + 共享 postgres/redis/qdrant/minio）
- [x] 8.3 创建 `deploy/scripts/blue-green-switch.sh`（active / status / rollback 子命令，通过 `nginx -s reload` 热切换 upstream）
- [x] 8.4 创建 `docker/nginx/blue-green.conf.template` nginx 配置模板（upstream blue/green + 切换变量）

## 9. 验证

- [ ] 9.1 跑 `ruff check src/hecate/ tests/` — 干净
- [ ] 9.2 跑 `ruff format --check src/ tests/` — formatted
- [ ] 9.3 跑 `mypy src/` — no issues
- [ ] 9.4 跑 `python -m pytest tests/ -q` — 全部通过（既有测试零回归）
- [ ] 9.5 跑 `hecate-migrate --check` — 输出 pending=0
- [ ] 9.6 跑 `hecate preflight` — 所有检查 PASS
- [ ] 9.7 跑 `hecate flag-audit` — 无 flag 时输出空表
- [ ] 9.8 跑 `openspec validate version-upgrade --strict` — valid
- [ ] 9.9 手动验证：`docker compose up -d` → hecate-migrate 先完成 → hecate 启动 → `GET /health/live` 200 → `GET /health/ready` 200 → `GET /version` 返回完整信息
- [ ] 9.10 手动验证：创建 feature flag → 评估 → 变更 → 缓存失效 → 再评估得到新结果

## 10. PR + Merge

- [ ] 10.1 创建 branch `feat/version-upgrade`
- [ ] 10.2 提交（commit message 格式 `feat(version-upgrade): add health checks, migration safety, feature flags, preflight, blue-green`）
- [ ] 10.3 推送 + 开 PR
- [ ] 10.4 等 pre-commit hook + CI 通过
- [ ] 10.5 合并到 main
- [ ] 10.6 更新 `docs/features/feature-catalog.md` + `roadmap.md` + `p3-mvp-audit.md`
- [ ] 10.7 `/opsx-archive version-upgrade`
