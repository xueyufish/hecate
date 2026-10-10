# Design

## Context

PG 参数化的全部零件已存在,缺的只是 CI 接线:

- `tests/scenarios/conftest.py` 的 `step6_runner_database_url` fixture:`HECATE_STEP6_POSTGRES_URL` 存在时自动展开 `[sqlite, postgres]` 双参数,缺失时诚实 skip。
- 三个受管进程场景文件已接该 fixture:SC04(`test_sc04_lease_renewal_policy.py`)、SC05 唤醒链(`test_sc05_managed_wake_chain.py`)、SC05 重连(`test_sc05_managed_reconnect.py`)——覆盖已接受未执行/重投、宿主 kill/restart、外部写成功但回执丢失、过期命令、租约停发有界拒绝、控制面重启幂等投递(即 6f 门槛列出的故障面)。
- `runner_harness.start_runner` 已按宿主 URL 自动为 Runner 子进程安装 psycopg;主进程侧 `managed_platform.py` 顶层 import `hecate_runner`,因此 job 必须安装 hecate-runner(主 `test` job 有同款步骤)。
- 既有 PG CI 模式可复制:`migrations` job(durable 故障 + Alembic drift,`DURABLE_TEST_POSTGRES_URL`)、`step6-continuation-pg`(并发续跑,`HECATE_STEP6_POSTGRES_URL`)。

## Goals / Non-Goals

**Goals:**

1. 新增 `step6-pg-process-matrix` job:PG 服务容器 + 双参数运行三个受管进程场景文件。
2. 复用既有安装模式,补齐 hecate-runner 可编辑安装与驱动依赖。
3. CI 绿灯后同一 PR 内完成文档翻转(方案 step6f、followup review、场景清单)。

**Non-Goals:**

- SC03/SC06(standalone 宿主)的 PG 参数化——6f 门槛聚焦受管宿主矩阵;standalone 场景保持 SQLite,部分覆盖如实标注。
- durable 核心故障套件与 Alembic 链(已由 `migrations` job 覆盖)。
- Windows 本地的 Runner 子进程 PG 连接(系统级 10013 拒绝,已归档;认证以 Linux CI 为准)。
- 新场景编写——矩阵场景已存在,本 change 只做执行接线。

## Decisions

1. **独立 job 而非扩展现有 job**:进程矩阵运行 10–20 分钟(wheel 构建 + 多进程场景双参数),与快速反馈的 `test` job 分离;`step6-continuation-pg` 保持窄聚焦(6d 并发层),矩阵 job 的失败不阻塞其信号。
2. **运行 `tests/scenarios/` 的三个参数化文件而非全目录**:SC01/SC02/SC03/SC06 是 standalone 场景,SQLite 已由主 job 覆盖;矩阵 job 只跑受管双参数,控制时长。选择器:`tests/scenarios/test_sc04_lease_renewal_policy.py tests/scenarios/test_sc05_managed_wake_chain.py tests/scenarios/test_sc05_managed_reconnect.py`。
3. **安装步骤照抄主 job**:uv sync(同款包列表)+ `uv pip install --no-deps -e packages/hecate-runner`(主进程 import)+ `uv pip install pytest-timeout "psycopg[binary]>=3.2" "asyncpg>=0.29"`。`--no-deps` 与主 job 一致(Runner 的运行依赖由 wheel 安装路径提供,开发环境只 import 其 managed 模块类型)。
4. **文档翻转以 CI 绿灯为前置**:任务顺序上 docs 翻转排在 job 接线之后;若 PR 首跑矩阵失败,先修复再翻转。任务勾选规则:绿灯前 docs 任务保持未勾(诚实门槛),绿灯后同 PR 内完成。

## Risks / Trade-offs

- Runner 子进程在 CI 上首次连服务容器 PG:Linux 无 Windows 端口代理问题,且 SC05 的 harness 已在本地验证过 PG 参数化的代码路径;剩余风险是 CI 环境差异,由 job 首跑暴露。
- 双参数使矩阵 job 时长加倍(wheel 构建每参数一份干净 venv):wheel 产物通过 `dist_dir` 复用(`wheel_dist` module fixture),只构建一次。
- PG 参数下的场景时长可能超 SQLite(`--timeout=420` 覆盖);若 CI 出现超时,优先调大 job 级 timeout 而非降低断言。
