# Tasks

> 状态基线:commit `ac32a82`。实现与本地(SQLite)可验证部分已全部交付;并发层
> (挂死恢复/回执丢失/等待互斥)的正式验收在 PostgreSQL 上执行——本地经
> `HECATE_STEP6_POSTGRES_URL`(CI job `step6-continuation-pg` 或本地 Docker PG)运行,
> 无该变量时这些用例诚实 skip(不冒充通过)。本地 Windows 的环境限制已归档记录:
> psycopg 同步连接在 Proactor 循环线程上经 Docker 端口代理会死锁(asyncpg 不受影响)。
>
> **2026-10-09 重跑记录**:CI 首跑暴露三个缺陷(dispatcher 续跑分支 fall-through、
> materializer 信封/过滤、conftest DROP SCHEMA 语法),修复后本地专用 PG 容器
> 4/4→5/5 通过(连跑稳定);详见本 change `verification.md` 重跑记录。3.x 据此
> 翻转,Linux CI 首次绿灯为形式门槛。

## 1. 续跑标记穿透

- [x] 1.1 `WorkflowExecutionService.execute` 新增 `resume_interrupted` 入参:为真时不写 initial_input,以 `resume_value` 调用 Pregel(内核 `_restore_from_checkpoint` 装载同一会话);`EntryExecutionService.execute` 经 `**execute_kwargs` 透传。验证:workflow 服务级单测 2/2——resume 路径 initial_input=None、resume_value 非空、session 不变;默认路径不受影响。
- [x] 1.2 `builtin.py` 中断观察的事实记录:interrupt 时 detail 携带 resumable 事实(会话快照已持久化、engine_session_id),移除 "continuation controls are unsupported" 表述;STOP_UNKNOWN 保持。验证:既有 builtin 兼容样本零断言修改通过。

## 2. 资格门与同 Run 续跑

- [x] 2.1 定义摘要:首跑派发时计算(工具顺序/Schema、模型引用、persona/guardrail 引用,取自冻结 execution_snapshot)并写入 run 快照 `definition_digest`,**立即独立 commit**(崩溃中丢单仍存续,续跑才有据)。验证:happy path crash 后 ledger/snapshot 断言 digest 在场。
- [x] 2.2 dispatcher 资格门:准入重验(既有,保持在前)→ 中断事实(非 failed)→ 快照可装载 → 摘要一致 → 同 Run 续跑(不铸造新 attempt);任一不满足转 `reconciliation_required` 并细分原因。验证:worker-integration harness 集成——漂移拒绝端到端通过(真实 dispatcher,拒绝原因含 "drifted"、模型零调用);no-digest 拒绝(worker_integration 回归,原因更精确);SQLite 可用窗口内同 Run 续跑链路实证(calls=3、runs=1、业务零重复)。
- [x] 2.3 与等待/唤醒路径互斥:TaskWaitingSignalError 的 park 路径不进续跑门;`test_protected_interrupt_does_not_treat_conversation_as_checkpoint` 语义保持(聊天历史不作为续跑依据)。验证:既有测试零修改通过;park→waiting_input 断言在 PG 用例内(waiting 互斥)。

## 3. 故障矩阵验收(PostgreSQL 层)

- [x] 3.1 回执丢失:账本存在 claimed-undecided 受保护动作时,续跑装载原会话但在该动作边界停止、Task 转 `reconciliation_required`,业务调用计数不增加。验证:`test_receipt_loss_stops_conservatively_at_resume` 本地 PG 绿(闸门通过后由动作边界 post-check 保守停止,零新 attempt、零业务重复;2026-10-09,×3 稳定);CI Linux 待跑。
- [x] 3.2 摘要漂移:篡改冻结配置或摘要后,续跑被拒、原因记录为漂移、模型零调用。验证:`test_definition_digest_drift_refuses_resume` **CI 首跑即绿** + 本地 PG 复跑绿。
- [x] 3.3 身份漂移顺序:Principal/部署失效时既有准入拒绝先于续跑资格门,零执行。验证:既有 `test_queued_execution_revalidates_admitted_identity_and_version`(SQLite)保持;新增 PG 专属用例 `test_admission_drift_refuses_before_resume_gate`(gate 故意先开,顺序错误即调用数增长)本地 PG 绿。
- [x] 3.4 进程边界等价:同一 durable 库上新 dispatcher 实例重驱动中断任务(进程重启等价物),续跑成立且零重做;报告如实标注与真实 kill/restart(step6f PG 矩阵)的差别。验证:park/kill 两种模拟模式本地 PG 绿(续跑用例均经全新 DurableWorker/dispatcher 实例重驱动);真实进程级矩阵仍归 step6f。
- [x] 3.5 挂死恢复后迟写入 fencing:park 模式释放挂死派发,续跑完成,迟到的终态写被 fencing 拒绝、业务零重复。验证:happy path 尾部断言本地 PG 绿 ×3(fencing 拒绝 warning 在场、tool 计数恒 1)。已知边界:迟到写者的事件日志追加不受 fencing 保护,归 step6b/租约策略后续。

## 4. 文档同步

- [x] 4.1 `docs/refactor/enterprise-agent-platform-evolution-plan.md` step6d 条目按 PG 验收证据更新;`docs/refactor/step6-followup-review.md` 追加验收记录。验证:方案文本与 CI 运行证据一致。(2026-10-10 完成:CI `step6-continuation-pg` 随 PR #237 merge queue 全绿,两处文档已按证据更新)

## 5. 验证

- [x] 5.1 本地层门禁:`ruff check src/ tests/ packages/`、`ruff format --check` 全绿;`mypy src/ packages/` 892 文件零错;本地测试(worker_integration 10 + 续跑模块 SQLite 层 2 passed/4 skip + resume 单测 2)通过;PG 层验收由 `step6-continuation-pg` CI job 执行,绿后补登本文件并翻转 4.1/3.x。
- [x] 5.2 (2026-10-09 补登)PG 层门禁:本地专用 PG 容器 5/5 通过;SQLite 层受影响面回归 `tests/test_execution/ + tests/test_runtime/ + tests/test_services/{test_workflow,test_orchestration}/` 2092 passed/30 skipped;materializer 单测 10/10(含信封往返、`_route` 保留、占位符穿透新回归)。CI Linux 首次绿灯仍待推送后确认,绿后翻转 4.1。
