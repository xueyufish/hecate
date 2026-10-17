# Verification — step5e-durable-approval-continuation

> 以下保留 GLM 初始验证记录。复核发现与修正、原始失败证据及修正后支持范围以 [review.md](review.md) 为准；旧计数不代表最终修正版全部验收。

## 静态门

- `ruff check src/ tests/ packages/`：0 错误（修复过程:导入排序自动修复、E501 两处换行、SIM115 对 Popen 持有的日志句柄加 noqa、两处未使用变量删除）。
- `ruff format --check src/ tests/ packages/`：1592 files already formatted。
- `mypy src/`：637 个源文件零错误（修正:回填 outcome 字典的显式 `dict[str, str | None]` 注解）。
- `openspec validate step5e-durable-approval-continuation --strict`：valid。

## 确定性测试（SQLite 分层）

- `tests/test_execution/test_approval_wait_park.py`：12 passed——
  - 引擎侧信号语义:`_stop_category(ApprovalWaitSignal) == "waiting_approval"`;入口服务终态化 state=failed + error_category=waiting_approval 后信号原样重抛(不被工具循环改写为 needs_review)。
  - 停车机制:任务 `waiting_approval` + 等待契约(approval_ref/工具/参数摘要) + 账本 PENDING 绑定(task_scope=任务 id、一次性、consumed 为空)三者齐备;绑定铸造失败 → 任务失败终态(fail-closed)。
  - 崩溃窗口:终态 waiting_approval 投影 + 等待未消费 → 回填失败终态。
  - 授权唤醒:decide(approved) + resume → APPLIED 且**续跑同 Run/引擎会话**(stub 断言 resume_interrupted=True);pending 拒绝不消费 token 且可再次唤醒;rejected 消费 token 转 `approval_rejected` 终态且零执行;revoked/expired/载荷 ref 不匹配 → 拒绝且 token 不消费;载荷自报 approved 不授权。
  - 消费者单测:匹配调用一次性消费(第二次 CONFLICT 拒绝)、工具不符/参数漂移再抛等待信号(新停车)。

## 真实进程重启 E2E(PostgreSQL,一次性容器)

- 确定性变体 `test_approval_park_survives_process_restart_and_runs_once`:**passed 19.3s**——子进程 A(真实 dispatcher/引擎/审批回调/账本,仅 provider 脚本化)认领→策略 ASK 触发→引擎停车(token 签发)→**kill -9**;父进程账本 decide(approved)+`resume` APPLIED;子进程 B(全新解释器/装配,同 PG,会话快照 postgres 后端)认领 → `[wire] resume=True` → 原生续跑门放行(同 Run)→ 绑定派发时消费 → **副作用计数文件恰 1 行(跨进程)** → 终态 succeeded、结果含真实回执值 TCK-1。
- 真实模型变体 `test_durable_approval_restart_with_real_model`:**passed 97.0s**,`zai/glm-4.5-flash`(key 从主 checkout .env 进程注入,未写入任何文件/报告)——模型自主发起受保护调用 → 停车 → 进程终止 → 批准 + 唤醒 → 新进程续跑 → 单次执行 → 终态回答携带真实回执(`Ticket created successfully! The ticket ID is **TCK-1**.`)。记录落盘 `tests/scenarios/baselines/durable_approval_restart_20261016T155804310Z.json`(gate=false, cost 未核实, latency 91.7s 含模型轮)。
- 环境隔离:确定性变体在真实模型 env 共存下仍走脚本化 provider(实测 22.3s)——`_child_env` 仅在真实测试显式 opt-in 时传递 `HECATE_E2E_REAL_MODEL`。

## 实施中发现并修正的设计/缺陷

1. **唤醒路由事实源修正**(proposal/specs/design 已同步更新):任务路径的入口关联(shared-task)不携带 run_ref,入口服务不写 Run 投影——"唤醒即新 attempt"的原始设想被推翻:动作台账恢复决策按引擎会话键工作,新会话会使已完成写动作的恢复记录失明。授权唤醒改以**已消费的 resume 等待契约**为路由事实,经 step6d 原生续跑门(为授权唤醒放行终态投影拒绝,其余准入不变)在原 Run/引擎会话续跑。
2. **submit 白名单遗漏**:任务输入载荷是固定形状白名单,`approval_wait` 开关需显式加入 `persisted_input`(E2E 揭示:子进程内 `approval_wait=False`)。
3. **重构丢分支回归**:第二轮重构丢失 `record.revision == 0` 首派发分支,导致首派发误走 `_new_attempt`(空快照 KeyError)——由既有 task-control 套件(4 个失败)捕获并修复;修复后 29+2198 全绿。此为重构安全性网络按设计工作的实例,记录于此以正视听。
4. **子进程测试的管道阻塞风险**:worker stdout 改为文件句柄(Popen PIPE 无人读取会塞满 64KB 缓冲导致子进程阻塞)。

## 定向回归(受影响域)

`tests/test_execution tests/test_runtime packages/hecate-runtime/tests packages/hecate-durable/tests tests/test_enterprise`:**2198 passed, 44 skipped, 0 failed**(419.99s)。既有 task-control 套件(17)、workflow 子任务契约、worker 集成、治理绑定、审批账本、durable 契约不改断言全绿。

## 宽回归

范围:channel/execution/runtime 包/durable 包/a2a/scheduling/workflow/MCP/IM/层级守卫/enterprise/S12/SC03。
结果:**2553 passed, 46 skipped, 0 failed**(555.21s)。首跑在 test_channel 早段一次导入期超时(Windows 文件系统瞬时停顿,MainThread 卡于 find_spec 的路径 stat;test_channel 单独复跑 90 passed 无复现),完整复跑干净通过——超时未复现、非本 change 引入。

跳过项沿用既有套件语义(JS pilot 依赖未装、未配置 PG 的专项、多 schema 样例等),本 change 的 PG 专项以实际容器实跑覆盖(见上),不因 skip 授予认证。

## CI

`.github/workflows/ci.yml` PG 作业新增两行:`test_approval_wait_park.py`(确定性层)与 `test_durable_approval_restart.py`(真实子进程重启,timeout=420)。远程结果以推送后 CI 为准。

## 跳过与边界(如实)

- PG 专项(E2E/真实变体)未配置 `HECATE_STEP6_POSTGRES_URL` 时显式 skip——skip 不计为通过。
- 真实模型记录 cost_amount=null、price_version=unverified、gate=false;不构成默认切换或生产认证。
- chat 同步审批、独立宿主(SC03 的本地持久等待)、workflow 父子审批组合不在本 change 范围;`HecateExecutionBackend.describe_capabilities` 的 `resolve_approval` 维持 unsupported(任务层停车+新派发语义,非六方法的活运行审批解析)。
- 临时 PG 容器(端口 55441)验收后已删除;主 checkout 的 .env 仅进程注入。
