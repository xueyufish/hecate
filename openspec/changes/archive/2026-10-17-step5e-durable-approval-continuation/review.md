# step5e 持久审批与续跑复核

## 范围

用户指定 `feat/step5e-durable-approval-continuation` worktree，基线 `cb817234`，GLM 实现未提交。对照本 change 的 proposal、design、specs 和 tasks 审查信号传播、审批权威、停车/唤醒、动作领取、模型输入及真实子进程恢复。在既有 change 内修正，不提交、推送或归档。

## 发现与修正

| 优先级 | 原实现问题 | 修正与证据 |
|---|---|---|
| P1 | durable 消费者被装入 chat 权威回调；后者把已有批准重铸成新的 `chat:*` 批准，改变引用、任务作用域和时钟锚，实际派发不再核验原审批 | durable 装配改用保留引用的事件回调，治理从服务端身份读取 Task scope；审批事件、原绑定及工具派发引用同一授权。装配反例在修正前返回不同引用、修正后通过；PG 核对只有原绑定存在 |
| P1 | 消费者拿不到服务端动作 ID 时生成随机 UUID，消费后、领取前崩溃无法识别同一动作 | ToolWorker 审批前传递稳定 ID，等待契约钉住该 ID；消费、治理及派发一致。三个真实 worker 进程验证消费后领取前杀进程，恢复仍使用原引用/动作，只执行一次；同参数新动作另行停车审批 |
| P1 | 唤醒请求直接用绑定自身的主体、Task scope 和资源自证，其他 Task 的绑定可通过门 | 主体由持久任务输入重建，Task scope 取命令绑定 Task，动作上下文取等待契约；跨 Task、主体/参数漂移及跨 workspace 反例拒绝且 token 保留 |
| P1 | 只处理 pending 绑定写入失败，等待契约写入失败会被 worker 当一般异常重排队 | 绑定和等待写入失败都经失败终态收口，保留安全原因。新反例在原实现停留 queued，修正后 failed |
| P1 | 拒批只改变 Task，Run 仍显示 `waiting_approval`，缺少 durable Run terminal 事件；并发 CAS 失败的命令也未明确拒绝 | Task/token/命令/terminal 事件同事务提交，Run 投影更新为 `approval_rejected`；CAS 失败显式 rejected。反例原投影错误、修正后通过；普通终态不因旧 consumed wait 重新执行 |
| P1 | 原生恢复把 `fold_session` 返回的日志版本作为 superstep，审计事件较多时误触发迭代上限 | 使用事件中的实际 superstep。独立反例由实际 3 步恢复成 58 步；修正后保持 3 步。真实 PG 停车前成功写入与多次审批场景从迭代上限失败转为成功 |
| P1 | 直接 `ledger.consume` 绕过可信时钟门；重铸新批准又掩盖旧进程时钟问题 | `TaskApprovalLedger` 经在线数据库时间验证后仅重锚原绑定，原期限与消费不变；消费继续经 `ApprovalGate`。回拨、过期、撤销与权威不可用拒绝；撤销后不重铸新的 pending 来续命 |
| P1 | 真实模型测试未断言终态成功、原引用/动作、恢复模型输入和正确结果；失败前的断言又可能阻止报告写入 | 与确定性场景使用相同精确断言，采集实际模型输入/输出与部分失败；记录在 finally 保存，凭据按所选 provider 匹配。保留 GLM 原观察，另存复核后记录 |

## 验证

- 原新增审批模块：12 passed，含一个注入异步函数替代同步 mint 函数的未 await 警告；复核将该注入修正为同步失败，不改变失败断言。
- 首批复核反例：4 failed，分别证明原引用替换、跨 Task 绑定放行、拒批投影不收敛及等待写入失败被重排队。修正后定向模块通过。
- Pregel/审批/task-control 定向：66 passed，3 个既有测试持有未 await 后台协程的警告；后续补充的边界案例由宽回归继续验证。
- PG 子进程故障集：4 passed，85.71s。涵盖普通审批重启、停车前成功写入、消费后领取前崩溃，以及同参数新动作的再次审批。事件、快照、审批和 Task/Run 均在 PostgreSQL，效果计数跨进程核对；恢复首轮模型输入必须含真实回执。
- 真实模型：`zai/glm-4.5-flash`，1 passed，79.57s。新观察 [durable_approval_restart_20261016T170954390Z.json](../../../tests/scenarios/baselines/durable_approval_restart_20261016T170954390Z.json) 记录两个实际模型输入及对应输出、原审批消费、一次工具效果和最终结果；记录的场景耗时 73.83s。成本/价格未核实，gate=false。
- 首次新增运行中两处测试接线问题（缺 checkpoint 参数、ORM 字段名误用）已修正，不将其算作产品缺陷证据；执行步数反例与 PG 故障结果另有实际失败证据。
- 宽回归：2631 passed、51 skipped、14 warnings、零失败，627.54s。覆盖 execution/runtime/enterprise/channel/A2A、scheduling/workflow、MCP/IM、分层守卫、S12/SC03、基线采集器，以及 runtime/durable/governance 包。跳过项包括未配置 PG、JS pilot 依赖、已有多 schema 专项与手动真实模型场景；本 change 的 PG 与真实模型另有实跑证据，其余 skip 不授予认证。
- 既有 PG 原生续跑、入口契约与真实 TCP 断连：14 passed，105.90s。
- 拒批事件经过 outbox 转写仍保留类别的补充回归：审批、task-control 与 worker integration 合计 52 passed，87.19s，3 个既有未 await 后台协程警告。与宽回归重叠，不合计为唯一用例数。
- Ruff/格式检查通过，1592 个 Python 文件；mypy 覆盖平台、runtime、durable 与 governance 的 764 个源文件，零错误；strict OpenSpec 验证通过。未过滤既有 fixture 收集、第三方弃用和后台协程警告。
- 文档链接检查发现并修正方案及 ADR 的既有基线归档失效链接。
- OpenSpec apply 状态 `all_done`，19/19 tasks 完成；所有改动仍未提交。
- 测试使用专属 `hecate-approval-review-pg` PostgreSQL 16 容器，端口 `127.0.0.1:55443`。结束后核对名称及 `hecate.review` 标签，已删除该容器与测试卷；合成数据可重建，原始验收记录保留，既有业务数据库容器未改动。

## 复现命令

在本 worktree 的虚拟环境中执行。PG 测试须自行配置隔离的 `HECATE_STEP6_POSTGRES_URL`，不使用业务数据库。

```text
python -m pytest tests/test_execution/test_approval_wait_park.py tests/test_execution/test_task_control_service.py tests/test_execution/test_worker_integration.py -q
python -m pytest tests/scenarios/test_durable_approval_restart.py -q -k "not real_model" --timeout=180
python -m pytest tests/test_execution/test_builtin_continuation.py tests/test_execution/test_entry_contract_pg_e2e.py tests/test_channel/test_g3_real_tcp_disconnect.py -q --timeout=180
ruff check src/ tests/ packages/
ruff format --check src/ tests/ packages/
mypy src/ packages/hecate-runtime/src/ packages/hecate-durable/src/ packages/hecate-governance/src/
openspec validate step5e-durable-approval-continuation --strict
```

真实模型另设 `HECATE_E2E_REAL_MODEL=1`、`BASELINE_MODEL` 和所选 provider key，执行 `tests/scenarios/test_durable_approval_restart.py::test_durable_approval_restart_with_real_model`。凭据仅经进程环境注入，不写入记录。

## 证据与支持边界

平台持久任务须显式 `approval_wait=true`，治理、事件和快照均按已有内置部署装配。批准由可信服务边界写入既有权威，新增企业审批 HTTP/UI、职责分离策略及控制面进程重启未获本轮认证。数据库时间作为在线权威的信任前提保留；本轮不授予离线时钟信任。普通 chat 同步审批与默认引擎开关保持原行为；Run 的停车投影为 failed/`waiting_approval`，Task 生命周期才是等待事实的权威。

合成业务工具仅为测试 fixture，不向平台产品加入工单能力。原 GLM 真实观察保持不变；本轮不补造其缺少的模型输入、权威引用或原始回执。真实模型结果不是完整双路径性能比较，也不关闭 step5e 的延迟/成本、Runner 自主选择、独立 workflow 身份与生产退出门槛。
