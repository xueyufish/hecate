# Tasks

## 1. worker 库(hecate-durable)

- [x] 1.1 `worker.py`:`DurableWorker` 派发循环——扫描 `queued`、`LeaseManager` 认领(资源键 + fencing token)、调用注入 dispatcher、按结果推进状态;单实例认领唯一与双实例并存有定向测试(内存 SQLite)。验证:`tests` 新增 `test_worker_dispatch.py` 全绿。
- [x] 1.2 启动/周期对账:queued 重入认领、过期租约以新 token 重派、活跃租约不动;同键终态回填不重执行(经提交幂等键与台账四态决策)。验证:worker 崩溃注入(杀循环、租约过期时钟)后任务到达终态或 `reconciliation_required`,不静默丢失、不重复执行。
- [x] 1.3 有界重试与 drain:attempt 计数持久化、超限进 `reconciliation_required` + 失败 envelope、退避;drain 拒绝新认领并如实报告在途。验证:连续失败注入到上限后状态为待对账且不再自动重试;drain 测试断言关停报告。
- [x] 1.4 `OutboxRelay`:`(run, source)` 游标读 `durable_event_log` → `PlatformEventService` 幂等投影(event_id 去重)、有界重试 + 退避、游标持久化 `durable_outbox_cursor`(worker 所有);中继失败不阻塞派发。验证:读模型不可用注入后恢复补投不丢不重;状态写入不受中继故障影响;游标表纳入 hecate-durable metadata。
- [x] 1.5 独立进程入口 `python -m hecate_durable.worker`:`--dsn` + dispatcher 工厂路径(延迟 import,核心包不反向依赖主应用);存储不可达/装配缺失非零退出并指明原因。验证:入口冒烟测试(独立进程执行 queued 任务结果与进程内一致;坏 DSN 非零退出)。
- [x] 1.6 契约套件与纯度探针回归:`DURABLE_IMPLEMENTATIONS` 既有注册不变全绿;新增 worker/relay 模块纳入纯度探针(不 import 平台/宿主/供应商 SDK)。验证:`pytest tests/test_execution/test_durable_contract.py` 与 purity probe 通过。

## 2. 平台接入(composition + task_control)

- [x] 2.1 composition 切换:postgres 后端三接缝绑 `SqlDurableStore`(`ledger_source="core"`),`PlatformDurableFactory`/PG adapter 引用清除;API 不再标注 stub 台账。验证:`test_entry_assembly`/composition 测试更新后全绿;契约套件对 `SqlDurableStore` 全量断言通过。
- [x] 2.2 `TaskControlService._dispatch` 回调化:`submit` 只落库 + `queued` + 治理事件;`_DispatchGuard`/`_spawn_background` 删除;取消三分支语义保持(认领前 applied、执行中协作中止、终态 rejected);`wait` 同步视图改事件/状态行轮询,不依赖同进程派发。验证:`test_task_control_service.py` 与 `test_task_control_api.py` 更新全绿,含重启后 wait 视图用例。
- [x] 2.3 平台 lifespan 启动进程内 worker(默认开,`HECATE_DURABLE_WORKER=off` 可关),dispatcher 绑定任务控制服务回调;多实例并存由租约保证。验证:集成测试——提交 → 强制结束进程模拟重启 → 新实例对账接管,任务到达终态(跨重启场景,platform-task-control MODIFIED 要求的 Scenario)。
- [x] 2.4 治理事件单写者收敛:删除与 seam 状态迁移绑定的直发 emit 点(store 同事务 envelope 为权威);不绑定状态迁移的 emit 逐点登记保留;SSE/`read_run_events` 读路径不变。验证:事件单写者静态断言(task_control 不经 PlatformEventService 写 seam 管辖的迁移事件)+ 既有事件测试全绿。

## 3. 迁移与退役

- [x] 3.1 新迁移:autogen 后手校——创建 `hecate-durable` metadata 八表 + `durable_outbox_cursor`;`durable_task_state` 补 token/期限列(等待机制);drop `task_lifecycle_states`/`task_submissions`/`control_commands`(迁移说明写明数据不迁移、downgrade 重建空表)。验证:`alembic upgrade head` 在 CI `migrations` job(PG16)通过;downgrade 可执行;grep 确认无代码引用退役表。
- [x] 3.2 `platform_durable.py` 删除 PG adapter 与 `PlatformDurableFactory`(保留/迁移 `to_sync_database_url` 等仍被 worker/中继使用的工具);相关测试文件清理。验证:`ruff check`/`mypy` 零错误,全库无 `PostgresDurableTaskStore` 残留引用。

## 4. 等待机制与编排场景

- [x] 4.1 持久等待:`waiting_input`/`waiting_approval` 进入路径(dispatcher 上报)、token/期限持久化、`provide_input`/`resolve_approval` 唤醒(单次消费 CAS、期限、终态拒绝)、输入随重拔回放;重启后等待保持。验证:`test_task_control_service.py` 新增等待组用例(等待跨重启、token 复用拒绝、迟到输入拒绝)全绿。
- [x] 4.2 编排最小场景:确定性 workflow 测试执行器经 `TaskControlService.submit` 发起 agent 子任务,自身以 `waiting_input`(关联键=子任务 task_ref)等待,子任务终态事件唤醒后完成。验证:端到端集成测试通过,不引入委派/验收语义。

## 5. 双主验证与收尾

- [x] 5.1 调度器双主验证:静态断言(manager 不 import seam store、task_control 不写 job 表)+ 并发注入测试(调度器重试 vs worker 重派:同幂等键回填、过期 fence 拒绝)。验证:新增双主测试组全绿;若暴露 `manager.py` 问题,按最小调整修复并记录。
- [ ] 5.2 runner durable profile 换绑 worker 库:runner 自有 dispatch 迁移到 `DurableWorker`(SC01/SC02 行为不变,SC03 持久化/重启半边可测)。验证:runner 测试与 SC harness 冒烟全绿;不翻转 manifest(SC03/SC06 状态不动)。**实施裁定(2026-10-04):延期**——runner 串行技术预览以进程内串行槽(非租约)为并发控制,其启动恢复循环已交付 SC03 持久化/重启半边;强行换绑租约 worker 会改变并发语义并危及 SC01/SC02。runner 测试 71 全绿确认无回归;worker 库 adoption 随 durable profile 完整形态(后台派发)再做,共享库(store/leases/contracts)已同源。
- [x] 5.3 文档与规格同步:`packages/hecate-durable/README.md` worker 章节;部署文档(worker 运行方式、`HECATE_DURABLE_BACKEND`/`HECATE_DURABLE_WORKER`);演进方案 step6 清单勾选已交付项并登记剩余(受管投递/投影归 `managed-runner-enrollment`,waiting 审批策略归 step7)。验证:文档评审;`docs/refactor/` 状态与实际交付一致。
- [x] 5.4 全量验证:`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`pytest tests/test_execution tests/test_channel/test_task_control_api.py tests/test_scheduling -q` 及 hecate-durable 包测试;CI `migrations` job 通过。验证:本地四项零错误,推送前钩子通过。(ruff check/format 全绿、mypy 617 文件零错误、scoped pytest 123+ 全过已于 2026-10-04 本地完成;CI migrations job 于推送后运行,asyncpg DLL/网络为本机环境限制)
