# Tasks

## 1. 核心包与契约迁移

- [x] 1.1 新建 `packages/hecate-durable/`(pyproject:依赖仅 `sqlalchemy`,`postgres` 可选 extra,workspace source),将 `contracts/execution/{references,events,tools,durable}.py`、`execution/durable.py`、`execution/stub_durable.py` 逐字节迁入(`hecate_durable.contracts.*` / `hecate_durable.seams` / `hecate_durable.stub`),原路径改为纯转发 shim;验证 `pytest tests/test_execution/ -q` 全绿(444 passed 含 G3 入口套件,证明 API 面不变)
- [x] 1.2 根 `pyproject.toml` 依赖 `hecate-durable`(workspace source);`tests/test_execution/test_durable_contract.py` R6 白名单纳入 `hecate_durable.contracts`;`test_contract_purity.py` 扫描范围扩展到新包契约/接缝/Stub 模块并加 storage 泄漏负例;验证纯度探针与签名检查通过(附带修复 phase-0 遗留:同键同体断言的 `to_dict` 分支对任何非同一对象实现必然 AttributeError,改为 dataclass 相等)

## 2. SQL 参考存储

- [x] 2.1 `hecate_durable/storage/models.py`:自有 Base + 八张表(task_state 含 input_payload/run 关联、submission、command、action_intent 含 task/run/session/execution/tool_call 关联列与 claim token、action_outcome 含结果内容/引用/摘要、event_log、run_sequence、lease);SQLite 以 `BEGIN IMMEDIATE` 事务消除多线程写死锁,双方言建表验证
- [x] 2.2 `hecate_durable/storage/store.py`:`SqlDurableStore` 实现三个接缝(独立 session/调用、单语句 CAS 领取、幂等回读、revision fencing、读失败返回 store_unavailable、状态写入同事务发事件);注册进 `DURABLE_IMPLEMENTATIONS`(`sql-sqlite`),验证参数化契约套件 R1–R6 全部通过(54/54)
- [x] 2.3 `hecate_durable/storage/lease.py`:`LeaseManager`(holder/expires/单调 fencing token/注入时钟;过期夺取 token 递增;旧 holder 写入 `StaleFenceError` 拒绝);`record_outcome_ex`(claim token 校验,迟到回执 `LateOutcomeError` 拒绝并以 `late_outcome_rejected` 事件留痕)
- [x] 2.4 `hecate_durable/storage/eventlog.py`:序号分配(run+source 单调、savepoint 竞争处理)、event_id 去重幂等、乱序接受、游标读取 + gap envelope、治理事件 actor/source 校验;验证去重/乱序/缺口场景测试通过

## 3. 内核 action 钩子(G2 收尾)

- [x] 3.1 `hecate_runtime/action_ledger.py`(`ToolExecutionState`/`ToolExecutionResolution`/摘要函数迁入 + `ActionLedgerHook` ABC + `ActionClaimVerdict`);`tool_worker.py` 决策表提取为共用纯函数 `recovery_decision`(行为不变),既有回执/G2 测试全绿(38/38 + 34 分层/自足)
- [x] 3.2 ToolWorker 注入可选 `action_hook`:锁区外先经台账原子领取(输者/冲突走决策表)、错误/成功路径镜像回执(带原始结果)、回执落盘失败不撤销副作用、双权威保守合并、succeeded backfill 增加 ledger 真实内容来源;新测试 `tests/test_runtime/test_action_hook.py` 8/8 覆盖四态、冲突拒绝、回执失败待对账、未配置钩子行为不变

## 4. Runner durable profile

- [x] 4.1 `profile.py`:runner.json `durable` 块(database_url、workspace)校验;durable 下 `write` 工具准入(`approval_required` 仍拒),预览档行为不变;`engine.py` 能力声明随档位翻转;验证 profile 测试通过
- [x] 4.2 `hecate_runner/durable.py`:`RunnerLedgerHook` 适配器(to_thread 卸载同步存储)、稳定动作键(run 派生会话 + 工具名)、提交幂等(Idempotency-Key 头)、Task 生命周期记录、控制命令回执(cancel requested→applied/rejected 按实际生效)、事件持久化、重启对账扫描(成功回填/写停止→reconciliation_required/queued 重派);验证重启恢复测试(模拟崩溃:落盘后新实例)通过
- [x] 4.3 `server.py`:durable 档端点(`GET /tasks`、`GET /tasks/{id}`、`GET /tasks/{id}/actions`、`GET /tasks/{id}/events`、events 走持久日志、提交幂等 409、证据门禁 503);`evidence.py` 增加 `probe()`(真实可审计探测记录);SC06 本地半边:证据不可写停止新受保护动作;验证 SC03/SC06 半边端到端测试 9/9 通过(含双 harness 重启、迟到幂等重放、取消回执收敛)

## 5. 故障注入与独立安装

- [x] 5.1 `packages/hecate-durable/tests/` 故障全家桶:崩溃(意图/领取/outcome 三种落盘点放弃进程态→新实例恢复断言)、租约过期(时钟推进→fencing 拒绝)、迟到回执(旧 token 拒绝+留痕)、存储失败(session 工厂异常→store_unavailable/写路径显式抛错);SQLite 默认 + PG 模式(`DURABLE_TEST_POSTGRES_URL` 门控)参数化,12/12 通过
- [x] 5.2 CI:`test` job 追加 `packages/hecate-durable/tests/`;`migrations` job(postgres 服务)追加 PG 模式步骤;`runtime-wheel-clean-install` job 构建 hecate-durable wheel、clean venv 安装三 wheel、保持无 full-hecate 断言并加 durable 独立导入检查;`check_execution_stack_matrix.py` 扩为三包校验并更新矩阵文档

## 6. 文档与验证

- [x] 6.1 `packages/hecate-durable/README.md`(包定位、方言、故障集、注册指引);`src/hecate/contracts/README.md` 注明契约集群新家与 shim;`docs/refactor/standalone-consumption-baseline.md` §5 SC03/SC06 半边登记 + §6 step6 owner 指派;`enterprise-agent-platform-evolution-plan.md` step6 七项勾选(本地可靠执行、租约/fencing、幂等键、治理事件 envelope、去重/乱序/缺口、G2、故障测试与查询 API);SC03/SC06 保持 planned 未翻转
- [x] 6.2 全量验证:`ruff check`/`ruff format --check`(src/hecate、packages、tests、scripts)0 错误;`mypy src/ packages/` 855 文件 0 错误;`pytest`(test_execution 444 含 G3、test_runtime 回执/G2/分层/自足 86、packages/hecate-durable 12、packages/hecate-runner 71)全绿;feature-inventory check 0 errors;分层测试无新增违规
