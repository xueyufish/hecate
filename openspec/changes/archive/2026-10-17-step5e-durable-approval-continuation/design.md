# Design — step5e-durable-approval-continuation

## Context

durable 任务派发链(task worker → dispatcher 回调 → entry service → 引擎工具循环)与审批基础设施均已存在:`TaskLifecycleState.WAITING_APPROVAL`/`ControlCommandKind.RESUME` 在 durable 契约中,dispatcher 已有 `TaskWaitingSignalError` → `_park`(状态 + 单次 token + 期限)与 task-control `_wake_waiting`(token/状态校验);`SqlApprovalLedger`(save/decide/revoke/consume)与 `ApprovalBinding`(judgment 与消费分离、参数摘要钉扎、trusted-time 锚)已交付;step5d-g3 的续跑历史恢复与 step5e 的停止语义(`_stop_category`、终态信封)可直接复用。缺的是三段接线:引擎侧信号、唤醒授权门、续跑消费。见 proposal.md - Why。

## Goals / Non-Goals

**Goals**

- 平台 Agent 经 durable 任务派发的需审批动作:停车 → 人类决定 → 续跑,全程持久事实,含真实 worker 子进程重启。
- 唤醒授权以账本持久决定为门;拒绝显式终态;默认装配行为不变。

**Non-Goals**

- chat 同步审批语义、策略判定规则本身(归授权域/step7)、审批 UI。
- 独立宿主(standalone runner)的持久审批;workflow 父子审批组合;带审批的 workflow 子任务中途断连矩阵。
- 为停车 attempt 新增第四种 Run 终态词汇——沿用 `failed` + 停止类别模式。

## Decisions

### D1: 类型化等待信号在引擎层以 BaseException 派生逃逸

`ApprovalWaitSignal(BaseException)`(hecate-runtime `errors` 模块),携带工具名、参数、风险级别与执行上下文标识。派生自 `BaseException` 而非 `Exception` 的原因:工具循环存在把普通 `Exception` 升级为 `ExecutionStoppedError("needs_review")` 的兜底路径(tool_worker 的动作执行包裹),`Exception` 派生会被改写或误记;`BaseException` 派生(与 `CancelledError`/`GeneratorExit` 同类)保证信号不被中间层吞掉。durable-wait 审批回调(`ApprovalCallback` 实现变体)在无同步答复者时抛出该信号。

- 备选:回调返回哨兵值——需要在 `ApprovalCallback` 契约上扩展返回类型,波及全部既有实现与 chat 路径;信号路径零契约变化。
- 入口服务 `_stop_category` 增加映射:`ApprovalWaitSignal` → `"waiting_approval"`;`_finalize_failure` 走既有终态信封路径(state=failed、stop_category=waiting_approval),复用 step5d 的信封/投影机制,不新增信封 schema。

### D2: 停车两段写入与崩溃窗口

dispatcher 捕获 `ApprovalWaitSignal` 后按序完成:①审批账本铸造 pending 绑定(`task_scope` 绑定任务、参数摘要、主体指纹、期限取等待期限);②dispatcher 写 Run 的 `failed` / `waiting_approval` 停车投影，再 `_park` 写 `WAITING_APPROVAL`。等待契约携带原 `approval_ref`、工具名、参数摘要、主体指纹、目标资源、服务端动作 ID、Run 与引擎会话。绑定或等待写入失败都转任务失败终态，不交给 worker 重新排队尝试。shared-task 入口不独立铸造 Run；投影由 dispatcher 写入。Task 生命周期是持久等待的权威，Run 的停止类别用于区分停车与实际失败。

崩溃窗口:Run 已终态而任务仍 RUNNING 时,既有回填逻辑保持失败回填(fail-closed),不因 `waiting_approval` 类别放行;停车后、派发器进程崩溃的窗口由既有重试预算收敛(重试的执行在无同步答复者下再次停车,无副作用发生)。授权唤醒的路由事实是**已消费的 resume 等待契约**,不是投影类别。

- 备选:任务先停车再终态化 Run——需要入口服务感知停车意图,破坏入口/派发分层;维持"入口只管尝试终态、dispatcher 只管任务生命周期"的既有职责线。

### D3: 唤醒授权门放在 task-control 的 `_wake_waiting`,RESUME 分支专用

`resume` 唤醒在既有 token/状态/期限校验后追加:按等待契约中的 `approval_ref` 查询账本绑定——

- `judgment=pending` 或绑定缺失 → 拒绝回执,token 不消费(命令记录 rejected,payload 带原因),任务保持等待;
- `judgment=rejected` → 消费 token，任务与 Run 转 `approval_rejected` 失败终态；Task 终态、消费与 durable terminal 事件同事务提交，等待契约保留为已消费证据；并发 CAS 失败的命令显式 rejected。
- `judgment=approved` → 绑定校验(`check_binding`:参数摘要/主体/期限/撤销/可信时间)通过则消费 token、合并决定摘要入持久输入、任务重排队;校验失败按其 mismatch 类别拒绝唤醒,token 不消费。

`provide_input` 唤醒不加此门(输入等待无审批事实)。授权门读账本是授权域事实的消费,不是策略判定——职责边界不变。

- 备选:门放在 dispatcher 续跑时才校验——token 已被消费,唤醒方收到成功回执但执行又立刻停车,造成"假成功 + 二次停车"循环;门前置到唤醒命令避免浪费 attempt 与 token。

### D4: 续跑走原生续跑门,同 Run/同引擎会话(修正原"新 attempt"设想)

审批唤醒后的续跑经 step6d 原生续跑门在**原 Run 与原引擎会话**上继续(`resume_interrupted=True` 语义:内核从 checkpoint + 事件日志尾部恢复通道状态,在被停调用处重驱)。原设想"唤醒即新 attempt Run"被实施分析否定:动作台账的恢复决策(`_recovery_outcome`/`_log_recorded_content`)以**引擎会话为键**,新会话下已完成写动作的恢复记录不可见,模型重发会被当作新动作重复执行——直接违反"已完成动作不重复执行"。原生续跑门为 `waiting_approval` 停车放行"终态投影拒绝"这一条(其余准入——定义摘要、快照可加载——全部保留);放行前提是任务等待契约已被授权唤醒消费。若门因快照不可加载/定义漂移拒绝,按既有保守路径转 `reconciliation_required`。"唤醒即新 attempt"规则继续适用于无引擎状态的等待(workflow 输入等待),行为不变。

### D4b: 续跑消费复用既有 `consume` 协议,不在唤醒时消费

唤醒只做授权门校验(读判定),**不消费**绑定;消费发生在续跑真正派发受保护动作时(`consume(approval_ref, ActionRequest)`——既有四态:consumed/same_action_rebound/conflict/not_approved)。续跑尝试的审批回调内联后端装配为"持久绑定消费者":调用与绑定上下文(工具名+参数摘要)匹配时消费,成功才执行;不匹配(模型发出了新的受保护调用)视为新动作,再次抛出等待信号重新停车;`conflict`/`expired`/`revoked` 按既有停止语义处理。模型侧:原生续跑恢复引擎通道状态,被停调用带历史上下文,模型重发命中消费路径。

- 该分工与 `execution-approval-binding` 既有"审批消费与动作领取的一致性协议"requirement 对齐:消费绑定动作身份,唤醒不消费。

### D4c: 复核后的动作、授权与时钟接线

- ToolWorker 在审批之前生成并传递服务端稳定 `invocation_id`，审批消费、治理再次消费与实际工具派发使用同一 ID。恢复同一动作允许 `same_action_rebound`；同参数的新 ID 必须再次审批。缺稳定动作身份则停止，不生成随机消费 ID。
- durable 装配采用保留原引用的事件回调，不经 chat 权威包装器重铸批准。主体从持久任务输入重建，Task scope 与等待上下文独立于绑定校验，不能用绑定自身字段证明绑定正确。治理层从服务端身份读取 Task scope，chat 默认仍以会话为 scope。
- `TaskApprovalLedger` 在在线 SQL 权威中对跨进程时钟锚重新核验：读取数据库时间，检查本地时钟偏差与持久锚回拨后，只更新原批准的时钟锚。批准引用、决定、消费关联与原到期时间不变；无在线权威、时钟不可验证、撤销及过期均拒绝，不能通过重铸新批准绕过。最终派发仍经既有 `ApprovalGate`。
- 原生恢复以事件中的 `superstep` 恢复执行步数，日志版本只用于事件位置。二者混淆会在短任务积累较多审计事件后误触发迭代上限。
- 已成功/失败的普通 Run 即使留有旧 consumed wait 也只回填终态，不重新续跑。只有当前 Run 的已消费审批等待可进入审批续跑门。

### D5: 默认关闭,装配级开关

durable-wait 审批回调仅在持久任务输入 `approval_wait=true` 时显式装配；未启用时维持现状。chat 路径装配不受影响。内置后端 `describe_capabilities` 的 `resolve_approval` 维持 `unsupported`：控制命令在 Task 层经授权唤醒原 Run，不新增后端六方法中的活运行审批接口。无引擎恢复状态的输入等待仍沿用既有新 attempt 规则。

### D6: 重启证据形态——真实 worker 子进程 + 平台进程内命令面

确定性 E2E(PG 专用,进 CI):平台事实(Test/绑定/命令)由测试进程持有;worker 以独立子进程运行(复用 durable worker 独立进程入口的既有形态);会话快照存储用 `SESSION_STATE_STORE_BACKEND=postgres`,使子进程 B 的原生续跑门能从 PG 加载停车尝试的引擎快照。流程:子进程 A 认领 → 执行至停车(观察 `WAITING_APPROVAL` 持久化)→ 真实终止 A(杀进程)→ 账本 decide(approved) → task-control `resume` → 断言重排队 → 启动子进程 B(全新装配,同 PG)→ B 认领并经原生续跑门续跑 → 受保护动作恰执行一次(合成工具副作用计数)→ 终态。负例矩阵(重复 resume、过期绑定、参数变化唤醒、撤销后唤醒、跨 workspace resume)用进程内新装配对象验证,不重复子进程开销。

- 备选:全链路双 HTTP 进程——审批决定与 resume 的 HTTP 面尚未交付(企业审批 API 是独立切片),用服务层命令面不降低重启证据的真实性(被重启的是执行进程,不是命令面)。

### D7: 真实模型变体沿用基线采集器模式

真实模型变体复用同一真实子进程场景，按所选 provider 验证凭据，显式 opt-in 后执行。断言停车、实际杀进程、原 Run/会话、原审批、稳定动作消费、单次效果、恢复首轮模型输入中的真实回执及正确结果引用；不规定模型的自然语言措辞。每次以新文件记录实际输入/输出、绑定、效果、终态及部分失败，保留原记录。成本与价格版本保持未知，gate=false；不作为完整双路径 rubric 重跑或生产认证。

## Risks / Trade-offs

- [`BaseException` 派生信号逃逸可能穿透调用方的宽泛 `except BaseException`] → 审计入口服务/派发包装的既有 `except` 块,确保只有预期的两层(dispatcher 捕获、入口服务 `_stop_category` 识别)接触它;为信号增加专门测试(不被工具循环改写、不落 needs_review)。
- [Run failed/waiting_approval 投影被消费端误读为任务失败] → 终态信封与 Run 投影的类别字段是权威区分;任务状态是等待语义唯一权威;文档与测试显式钉住两者差异。
- [唤醒门与续跑消费之间的 TOCTOU(门通过后、消费前绑定被撤销)] → 消费时的 `consume` 重核(既有协议要求"消费前重新核验撤销/期限"),门只是避免无效 attempt 的优化与显式回执,不是唯一防线。
- [子进程测试在 Windows CI 的进程管理脆弱] → 复用 SC05 已验证的子进程 + 端口/状态文件轮询模式;超时与清理路径显式;PG 容器作业已有先例。
- [pending 绑定堆积(决定永不到来)] → 绑定期限 + 等待期限一致;过期后唤醒按 expired 拒绝;不做主动清扫(留待治理运营切片)。

## Migration Plan

纯增量:新回调变体、新停止类别、新唤醒门分支均默认不激活;无 schema 迁移(pending 绑定用既有 `ApprovalBinding` 字段;等待契约用既有 `wait` JSON 列)。回滚 = 关闭装配开关。部署顺序无约束;账本/等待契约旧数据不受影响(旧等待无 `approval_ref` 时门按"绑定缺失"拒绝——仅影响新停车路径不存在旧行,因为停车路径本身是新增)。

## Open Questions

(无——装配开关的具体配置键位(部署级 vs 任务输入级)在实现时按既有配置快照模式对齐,不影响规格与任务拆分。)
