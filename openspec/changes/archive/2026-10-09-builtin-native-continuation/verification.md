# builtin-native-continuation — Verification

> 状态:实现与本地(SQLite)可验证部分完成并已提交(`ac32a82`);并发层正式验收在
> PostgreSQL/CI(`step6-continuation-pg` job),绿后补登"重跑记录"。

## Evidence(本地,SQLite 层)

- `tests/test_services/test_workflow/test_execution_service_resume.py` — 2/2:resume
  标记穿透(resume_interrupted → initial_input=None + resume_value 非空 + 同 session;
  默认路径不受影响)。
- `tests/test_execution/test_worker_integration.py` — 10/10:含漂移拒绝端到端
  (`test_definition_digest_drift_refuses_resume` 的前身断言经更新:gate 拒绝原因更精确)
  与 no-digest 拒绝;准入重验零执行断言保持。
- `tests/test_execution/test_builtin_continuation.py` — SQLite 本地:drift 用例曾在
  SQLite 窗口跑通(后随并发层整体门控到 PG);4 用例无 PG 时诚实 skip。
- 摘要 crash 存续:crash 后 ledger/snapshot 断言 `definition_digest` 在场
  (`_execute` 内 digest 记录为独立 commit)。
- 附带修复实证:dispatcher entry 调用补 `user_id` 后 materializer 检查点可装载
  (此前 tenant 上下文丢失)。

## PG 层(并发验收,CI)

- CI job `step6-continuation-pg`(Linux + postgres:16 服务容器 + asyncpg/psycopg):
  运行 `tests/test_execution/test_builtin_continuation.py` 四用例——挂死恢复原生续跑
  (park + fencing)、回执丢失保守停止、摘要漂移拒绝、等待互斥。
- 本地 Windows 不具备该层运行条件(已归档的环境发现):psycopg 同步连接在
  Proactor 循环线程上经 Docker 端口代理死锁;asyncpg 不受影响但完整套件仍以 CI
  为准。

## Gates

```bash
ruff check src/ tests/ packages/          # All checks passed
ruff format --check src/ tests/ packages/ # 1519 files already formatted
mypy src/ packages/                       # Success: no issues found in 892 source files
pytest tests/test_execution/test_worker_integration.py \
       tests/test_execution/test_builtin_continuation.py \
       tests/test_services/test_workflow/test_execution_service_resume.py -q
# 12 passed, 4 skipped (SQLite 本地层;PG 层由 CI 执行)
```

## Honest scope

- step6d 未整体关闭:并发层验收证据待 CI PG 运行;G2 保持未关闭;不建设平台
  通用 checkpoint 引擎;外部后端按能力声明选择原生恢复或待对账。
- 共享 harness 移入 `tests/test_execution/conftest.py`(SC03/SC05/续跑套件复用),
  worker_integration 既有断言零修改。

## 2026-10-09 重跑记录(CI 首跑失败 → 本地 PG 修复)

CI `step6-continuation-pg` 首跑(4 用例首次真实执行):2 failed、2 passed、4 个
teardown error。本地以专用 PostgreSQL 16 容器(127.0.0.1:5433)复现并定位三个
缺陷,全部修复后 4/4 通过(连跑 3 次稳定):

1. **dispatcher 续跑分支 fall-through(产品缺陷)**:`_run` 中闸门通过分支置
   `run = latest` 后,控制流落到无条件的 `_new_attempt`,新 attempt 覆盖已恢复
   的 run——原生续跑被"换壳"成新 attempt 重执行(受保护动作会重复业务写)。
   修复:`_new_attempt` 加 `if not resume_interrupted` 守卫。摘要漂移测试此前
   通过正因它走闸门拒绝分支、不触达 fall-through。
2. **SessionStateMaterializer 恢复形状缺陷(产品缺陷,两层)**:save 对每个
   通道值包 `{"value": ...}` 信封,`load` 原样返回,`ChannelManager.restore`
   把信封整体赋成通道值——缓存恢复后 live 状态带信封、全量日志折叠是裸值,
   PROJECTION.EQUIVALENT 按不变量 fail closed;且 save 按 `_` 前缀跳过通道,
   丢弃了 logpolicy 显式可记录的 `_route`/`_dispatch`(折叠正确性依赖)。
   修复:load 对称解包信封(`_omitted` 占位符穿透),save 过滤条件改为以
   `should_log_channel` 为准。等价守卫此前在无 EventStore 时短路,该缺陷从未
   被暴露;PG 用例首次真正接通事件存储。
3. **conftest teardown SQL 语法错误(测试设施)**:`DROP SCHEMA IF NOT EXISTS`
   不是合法 PostgreSQL 语法,修复为 `IF EXISTS`;本地 4 用例此前从未运行
   (`_PG_SKIP`),故未暴露。

另:test 1 编排修正——挂死进程在恢复窗口必须保持冻结(gate 在续跑断言完成后
才打开),否则原派发向共享会话日志的迟到追加使 log-as-truth 比对落在移动尾部
上;续跑的模型调用是新调用序号,不受 gate 影响。**已知边界**:迟到写者的
事件日志追加不被 fencing 保护(状态写、动作账本、业务副作用均已闸),事件存储
级 fencing 归入 step6b/租约策略后续范围。

复现环境注记:本地 Docker 端口代理的 IPv6 发布口(::1)为黑洞,psycopg 无
connect_timeout 时先试 `::1` 永久挂起;本地运行统一使用 `127.0.0.1`(CI Linux
不受影响)。

修复后证据:

```bash
HECATE_STEP6_POSTGRES_URL=postgresql+psycopg://...127.0.0.1:5433/postgres \
pytest tests/test_execution/test_builtin_continuation.py -q --timeout=120
# 4 passed(×3 连跑稳定)
pytest tests/test_execution/ tests/test_runtime/ \
       tests/test_services/test_workflow/ tests/test_services/test_orchestration/ -q
# 2092 passed, 30 skipped(SQLite 层回归)
mypy src/ packages/   # Success: no issues found in 892 source files
```

CI `step6-continuation-pg` 的 Linux 首次绿灯仍为 3.x 关闭的形式门槛。

**2026-10-10 补登**:该 CI job 随 PR #237 merge queue 全绿,形式门槛达成;4.1 文档翻转已完成(演进方案 step6d 条目 + `step6-followup-review.md` 2026-10-10 验收记录)。
