# platform-task-control Specification

## Purpose

平台任务控制面:以 `durable-execution-contract` 定稿的契约语义(Task 生命周期、命令回执、幂等键、治理事件 envelope)为基线,交付平台侧的持久投影、任务控制 API(提交/查询/事件读取/命令/待对账)、治理事件发射与调度器接线,使平台对"任务在哪、发生过什么、哪些命令未确认"有权威可查的记录。

## Requirements

### Requirement: 平台任务提交经幂等键绑定服务端上下文

`POST /api/tasks` SHALL 接受业务目标、执行目标(agent/deployment 引用)、输入与可选预算/截止时间,并在服务端验证的调用主体与 workspace 作用域内处理幂等键:键绑定主体、workspace 与规范化请求摘要。相同键、相同摘要的重复提交 SHALL 返回首次提交的 Task/Run 关联,不产生第二次执行;相同键、不同摘要 SHALL 返回冲突错误,原有关联不被改写;键绑定的主体/workspace 与验证上下文不一致 SHALL 被拒绝。提交成功 SHALL 返回持久化的 task_id 与 run_id;请求可携带等待参数以获取同步等待视图(在 Task 到达终态或超时后返回),未携带时提交立即返回 queued 状态。提交后任务 SHALL 经持久派发 worker 投递执行(`durable-execution-worker` 能力):worker 崩溃或进程重启后,未终态任务经对账继续(queued 重派、过期租约以新 fencing token 重派),平台据此宣称跨重启的后台执行保证;已终态任务不被重派。

#### Scenario: 同键同摘要幂等

- **WHEN** 同一主体以相同幂等键与相同请求体重复提交
- **THEN** 响应返回与首次提交相同的 task_id/run_id,系统内不产生第二个 Task

#### Scenario: 同键异摘要冲突

- **WHEN** 同一主体以相同幂等键提交不同请求体
- **THEN** 响应为冲突错误,首次提交的关联不被改写

#### Scenario: 跨主体键作用域拒绝

- **WHEN** 幂等键被绑定主体之外的验证上下文重放
- **THEN** 提交被拒绝,不返回原关联

#### Scenario: 提交后进程重启执行继续

- **WHEN** 提交成功且派发进行中时平台进程重启
- **THEN** 该任务经对账继续执行并到达终态或显式待对账,不静默丢失,不产生第二次同义执行

### Requirement: Task 生命周期状态为持久投影并有单一写入方

平台 SHALL 以持久记录保存每个 Task 的生命周期状态(封闭枚举,来自 durable 执行契约)、revision、记录时间与写入方来源;状态迁移 MUST 经契约迁移校验(终态吸收、待对账只经显式对账收敛、过期 revision 拒绝)。生命周期投影与 TaskRunRegistry 登记的责任记录、Run 的后端投影为分列字段组;本能力 SHALL 只写生命周期投影字段。平台重启后,已提交 Task 的生命周期状态 SHALL 可查。`reconciliation_required` 的收敛 MUST 经显式对账动作并携带收敛目标,拒绝静默改写。

#### Scenario: 重启后状态可查

- **WHEN** Task 提交并进入某生命周期状态后,进程内的内存状态被清空
- **THEN** 查询 API 从持久记录返回该状态与 revision,而非未知

#### Scenario: 待对账只经显式对账收敛

- **WHEN** 对 `reconciliation_required` 的 Task 发起对账并声明收敛目标为 succeeded
- **THEN** 状态迁移为 succeeded 且 revision 递增;未经对账动作的直接迁移被契约校验拒绝

### Requirement: 控制命令为独立回执记录且 HTTP 成功不等于已应用

控制命令端点 SHALL 为每个命令产生独立回执记录(command_id、种类、签发者、目标 task/run 引用、签发时间、状态),状态迁移遵循契约回执状态机(`requested / acknowledged / applied / rejected / expired` 封闭集合,终态吸收)。命令端点的成功 HTTP 响 SHALL 只代表命令已记录(状态 `requested` 或经后端确认的 `acknowledged`),MUST NOT 显示为 `applied`;`applied` 只能来自执行方实际生效的回执。命令可携带期望 revision 与有效期;有效期届满仍未应用的命令在读取时 SHALL 惰性收敛为 `expired` 且不再可应用。取消命令 SHALL 转发至所选 Run 的执行后端取消请求;后端不支持取消时命令记录 MUST 保留显式拒绝状态,MUST NOT 伪造成功。

#### Scenario: HTTP 取消成功但回执为 requested

- **WHEN** 对 running Task 调用 HTTP 取消端点且后端接受取消请求
- **THEN** 响应携带 command_id 与状态 requested(或后端确认的 acknowledged),Task 未被标记为 cancelled

#### Scenario: 过期命令惰性收敛

- **WHEN** 一条携带有效期的命令在有效期届满后仍未应用并被查询
- **THEN** 其回执状态返回 expired,且后续不能再迁移为 applied

#### Scenario: 后端拒绝取消时显式记录

- **WHEN** 取消命令转发到的后端不支持取消能力
- **THEN** 命令回执记录 rejected 与拒绝原因,不返回伪造的取消成功

### Requirement: 治理事件采用版本化 envelope 并按 Run 游标可读

平台在 Task 提交、生命周期迁移、命令记录/迁移与对账标记时 SHALL 发射治理事件:版本化 envelope 携带 event_id、task/run 引用、actor、source、source_sequence、事件/接收时间、payload schema 引用与关联/因果 ID;治理事件 profile 下 actor 与 source MUST 非空(分别标识动作主体与权威写入方)。事件 SHALL 持久化并可按 Run 引用 + 游标分页读取;已读取位置之后的断线消费 SHALL 能以游标恢复,不依赖客户端时钟。执行流事件(引擎流映射后的 envelope)SHALL 在任务 API 派发路径持久追加,同步与流式接口为同一 Task/Run 上的等待/订阅视图。事件读取 SHALL 以 workspace 作用域隔离,跨 workspace 读取 MUST 与不存在不可区分。

`source_sequence` 在 (workspace, run) 维度 SHALL 由存储层唯一约束保证:并发的序号分配竞争 MUST 显式冲突失败,经 event_id 去重后重试收敛, MUST NOT 产生同位静默重排;迟到事件(序号低于已存最大序号)的落库 SHALL 显式处理(按 event_id 去重或重序追加),MUST NOT 静默丢弃或破坏游标分页读取语义。治理事件写入方保持单一且分列:平台发射路径与宿主事实投影路径各以自身 source 写入,任一写入方 MUST NOT 覆盖或重开另一方已写入的事实(终态投影不重开语义保持)。存储升级遇到历史同位重复数据时 SHALL 显式报告并修复后再施加唯一约束,MUST NOT 静默保留破坏唯一性。

#### Scenario: 关键迁移发射治理事件

- **WHEN** Task 从 queued 迁移到 running 再到 succeeded
- **THEN** 每次迁移产生一条含 actor 与 source 的版本化事件,按时间可追溯

#### Scenario: 游标断线恢复

- **WHEN** 消费者读取某 Run 事件到游标 C 后断线,重连后携带 C 继续读取
- **THEN** 返回 C 之后的事件,不重复已读事件也不跳过新事件

#### Scenario: 跨 workspace 事件读取隔离

- **WHEN** 以 workspace A 的上下文请求 workspace B 的 Run 事件
- **THEN** 响应与 Run 不存在时不可区分,不泄露事件内容

#### Scenario: 并发序号分配冲突显式且经去重收敛

- **WHEN** 两个写入方并发为同一 Run 分配下一个 source_sequence,双方均提交落库
- **THEN** 存储层唯一约束使后提交方显式冲突失败;该事件经 event_id 去重判定后或被识别为重复(幂等跳过)或重试获得新序号落库,事件流中不存在同位双写或静默丢失

#### Scenario: 迟到事件显式落库不破坏游标读取

- **WHEN** 投影器收到 event_id 未落库但序号低于该 Run 已存最大序号的迟到事件
- **THEN** 该事件按去重/重序语义显式落库,游标分页读取不因迟到追加而跳过或重复返回事件

#### Scenario: 历史同位重复数据升级显式修复

- **WHEN** 存储升级在施加唯一约束前发现同一 (workspace, run, source_sequence) 存在多条历史事件
- **THEN** 升级路径显式报告该数据并按确定性规则修复(保留权威写入方记录),修复完成前不宣称约束已生效

### Requirement: 待对账查询 API 汇聚未收敛事实

`GET /api/reconciliation/pending` SHALL 以 workspace 作用域返回处于待对账状态的事实:生命周期为 `reconciliation_required` 的 Task、Action 台账中 `pending_reconciliation` 的记录(生产台账实现合入前该列表为空且显式标注来源为开发 Stub)、以及超过有效期仍未决的命令。查询 MUST NOT 触发任何对账动作或状态改写(过期命令的惰性 `expired` 收敛除外);结果中的每项 SHALL 携带足够的引用(task/run/action/command 标识)供后续对账。

#### Scenario: 待对账任务可见

- **WHEN** 一个 Task 因执行结果未知被标记 reconciliation_required 后查询待对账 API
- **THEN** 该 Task 连同其标识与当前 revision 出现在结果中

#### Scenario: Stub 台账来源显式标注

- **WHEN** 在生产台账实现合入前查询待对账 API
- **THEN** Action 部分返回空列表且响应显式标注其来源为开发期 Stub,不虚报已扫描生产台账

### Requirement: 调度器触发经 executor registry 真实执行

定时任务调度器的执行路径 SHALL 将触发分派给 executor registry 按任务目标(agent/workflow)解析的执行器,执行器的结果映射为执行记录的成功/失败与错误信息;不执行任何实际工作的空转路径 MUST 删除。分派 SHALL 尊重既有并发上限与多节点咨询锁语义;执行器失败 MUST 记录失败状态与错误信息,不吞错为成功。调度器的 job 状态(触发/投递/重试记录)与平台任务状态 SHALL 各有单一写入方:调度器 MUST NOT 改写任务生命周期投影,平台 MUST NOT 改写调度器 job 记录;调度器重试与 worker 派发的并发 SHALL 经提交幂等键与租约 fencing 仲裁,旧 ownership 的迟到写入被拒绝并留事件。

#### Scenario: 调度触发真实执行

- **WHEN** 一个绑定 agent 的启用定时任务到达触发时间
- **THEN** 调度器经 registry 将执行分派给 agent 执行器,执行记录反映执行器的真实结果

#### Scenario: 执行器失败如实记录

- **WHEN** registry 分派的执行器返回失败
- **THEN** 该次执行记录为 failed 并携带错误信息,不被记为 success

#### Scenario: 旧 ownership 迟到写入被拒绝

- **WHEN** 调度器重试与 worker 重派并发,过期租约的旧执行方提交结果
- **THEN** fencing 校验拒绝该写入并保留事件,权威状态不被覆盖,不产生重复业务动作

### Requirement: seam 绑定经 composition 单点切换

平台对持久执行接缝(任务状态存储、命令记录器、Action 台账)的消费 SHALL 经 composition 的进程级绑定点:开发/测试默认绑定 InMemory Stub;生产绑定持久执行核心包的 SQL 参考存储(`SqlDurableStore`,三接缝共享同一引擎与事务域),平台 PostgreSQL 直连 adapter 退役;每个生产实现 MUST 注册进参数化契约套件并全量通过既有断言。绑定点 MUST 是唯一的,API 与服务层代码不感知实现替换。SQL 参考存储的每次调用 SHALL 使用独立短事务 session,不在并发请求间共享会话。

#### Scenario: 生产绑定通过契约套件

- **WHEN** `SqlDurableStore` 注册进参数化契约套件
- **THEN** 既有全部契约断言(生命周期迁移、回执状态、幂等键、领取仲裁)对该实现原样通过

#### Scenario: 开发绑定不依赖数据库

- **WHEN** 开发/测试 profile 下任务控制服务运行
- **THEN** 生命周期与命令回执经 InMemory Stub 工作,不要求新表存在

#### Scenario: 台账来源为生产实现

- **WHEN** postgres 后端绑定生效
- **THEN** 三个接缝均来自 `SqlDurableStore`,绑定元数据如实标注台账来源,API 不再标注 stub 台账

### Requirement: 任务等待人工输入或审批为持久等待并可恢复

任务控制面 SHALL 支持进入 `waiting_input`/`waiting_approval` 的持久等待:等待状态与输入/审批契约引用、单次消费回调 token、期限一并持久化;`waiting_input` 经 `provide_input` 命令凭有效 token 唤醒,输入随任务状态持久化并在重派时回放;`waiting_approval` 经 `resume` 命令凭有效 token 与匹配等待上下文的持久审批决定唤醒——仍 pending(或无匹配绑定)时拒绝唤醒且不消费 token,任务保持等待;决定为 rejected 时消费 token 并转 `approval_rejected` 失败终态。token 已消费、已过期、任务已终态或输入与等待契约不符 SHALL 拒绝并记录;进程重启后等待任务保持等待状态且可被唤醒。审批的策略判定、参数绑定校验与职责分离归授权域(step7),本能力只保证等待的持久化、真实回执与恢复;持久审批等待的进入(类型化等待信号与停车上下文)由 `execution-approval-binding` 的对应 requirement 约束。

#### Scenario: 等待跨重启

- **WHEN** 任务等待输入期间平台进程重启
- **THEN** 任务保持 `waiting_input`,有效 token 唤醒后按持久化输入重放派发

#### Scenario: token 单次消费

- **WHEN** 同一回调 token 被重复使用
- **THEN** 第二次唤醒被拒绝并留下拒绝记录,任务状态不被二次改写

#### Scenario: 迟到输入拒绝

- **WHEN** 输入在任务到达终态后到达
- **THEN** 唤醒被拒绝并记录,不复活已终态任务

#### Scenario: 审批等待无决定不消费 token

- **WHEN** `waiting_approval` 任务的审批绑定仍为 pending 时发起 `resume`
- **THEN** 唤醒被拒绝并记录,token 未消费,任务保持 `waiting_approval`

#### Scenario: 拒绝决定转终态

- **WHEN** `waiting_approval` 任务的审批绑定判定为 rejected 时发起 `resume`
- **THEN** token 被消费,任务转失败终态并携带 `approval_rejected` 类别,受保护动作零执行
### Requirement: 平台中断尝试优先恢复原执行会话

平台 durable dispatch 对含受保护动作的中断尝试 SHALL 先验证原生 continuation、冻结图、身份与动作关联均已接通且状态可恢复；可恢复时 SHALL 沿用原 Run 与原 engine 会话继续原执行，已决动作回填真实结果、未决动作停止，MUST NOT 为恢复创建新模型 Run。续跑资格门 SHALL 包含冻结执行定义摘要校验：派发时记录的摘要（工具顺序/Schema、模型引用、persona/guardrail 引用）与续跑时重算的摘要不一致，或原 attempt 的引擎会话持久快照缺失/不可装载时，MUST NOT 续跑。仅普通聊天历史可加载 MUST NOT 作为原生可恢复证明；原生入口未实现或状态不可装载时 SHALL 保持 `reconciliation_required`。业务副作用 SHALL 以实际调用计数验证不重做；外部写已成功但回执丢失的续跑 SHALL 在下一动作前停止并保持待对账。

#### Scenario: 会话可恢复的中断尝试沿用原 Run
- **WHEN** 原生 continuation 已接通且原 attempt 的冻结状态、身份和动作关联均可验证
- **THEN** 沿用原 Run 与原 engine 会话原生续跑，台账仲裁使已决动作零重复调用

#### Scenario: 会话不可恢复的中断尝试保持保守待对账
- **WHEN** 中断尝试含受保护动作但无原生 continuation 或状态不可恢复
- **THEN** Task 保持 `reconciliation_required`，不以新 Run 盲目重放

#### Scenario: 历史存在但无原生续跑
- **WHEN** 中断尝试已有受保护动作且普通 SessionState 可加载
- **THEN** 不重新调用模型，Task 保持待对账

#### Scenario: 执行定义摘要漂移拒绝续跑
- **WHEN** 中断尝试的会话快照存在，但续跑时重算的执行定义摘要（工具顺序/Schema、模型引用、persona/guardrail 引用）与派发时记录的摘要不一致，或原摘要缺失
- **THEN** 不装载该会话、不调用模型，Task 保持 `reconciliation_required` 并记录漂移原因

#### Scenario: 外部写成功但回执丢失时续跑保守停止
- **WHEN** 可续跑的中断尝试在台账中存在已领取但结果未决的受保护动作（外部写已发生、回执未落）
- **THEN** 原生续跑装载原会话，但已决动作仅回填、未决动作不重做；执行在该动作边界停止，Task 进入 `reconciliation_required`，业务 API 调用计数不增加

#### Scenario: 续跑沿用原 Run 且身份在续跑前重验
- **WHEN** 满足全部续跑资格条件并原生续跑
- **THEN** 沿用原 Run 标识与原 engine 会话，不铸造新 attempt；Principal、部署与冻结版本的既 有准入重验先于续跑执行，重验失败时不进入续跑路径

### Requirement: 工作流回调经子任务事实核实后唤醒

平台 SHALL 提供具名的工作流回调入口:回调绑定父任务等待契约中的子任务引用(`await_task_ref`),平台核实该子任务存在于同一 workspace、已达终态、且其生命周期状态与回调声明一致后,才消费一次性 wait token 并以 `provide_input` 语义唤醒父任务(合并子任务结果摘要与回调载荷)。伪造或跨 workspace 的子引用、声明状态与子任务真实状态不符、等待契约不含该子引用的回调 MUST 得到显式 `rejected` 回执,MUST NOT 消费 token、不产生父任务状态变化。重复回调(同 command_id)SHALL 幂等返回既有回执;迟到回调(token 已消费或过期)SHALL 得到显式拒绝。父任务与子任务的持久事实 SHALL 各自跨进程重启保持——重启后回调入口行为不变。

#### Scenario: 经核实的回调唤醒父任务

- **WHEN** 子任务已 succeeded,回调声明 `declared_state=succeeded` 并携带正确 wait_token
- **THEN** 平台核实子任务事实后消费 token,父任务 requeue 且输入含子任务结果摘要,命令回执 `applied`

#### Scenario: 伪造或跨 workspace 子引用被拒绝

- **WHEN** 回调引用的子任务不存在、属于其他 workspace、或声明状态与子任务真实终态不符
- **THEN** 回执 `rejected` 并留原因,wait token 未消费,父任务保持等待

#### Scenario: 重复与迟到回调不重复唤醒

- **WHEN** 同一 command_id 重复提交,或 token 已消费/过期后回调
- **THEN** 幂等返回既有回执或显式拒绝,父任务不二次 requeue,无重复副作用

#### Scenario: 父子分别重启后回调仍可完成

- **WHEN** 父任务等待期间父平台进程重启、子任务在另一进程完成后,回调到达
- **THEN** 父任务等待事实与子任务终态事实均从持久记录核实,回调按正常语义应用

### Requirement: 命令重放绑定授权资源及原请求

平台 SHALL 先核实 workspace 中的 task，再返回任何既有命令回执。相同 command ID SHALL 绑定 task、issuer、kind、payload、schema、token、期限与 expected revision；任何差异 MUST 拒绝，未应用命令重试 SHALL 使用原 issued_at 和 Run 绑定。

#### Scenario: 跨 workspace 或异体重放
- **WHEN** 调用方重用其他资源的 command ID 或改变原命令参数
- **THEN** 访问失败或命令冲突，不暴露其他资源回执且无执行效果

### Requirement: 回调可信子事实不可由载荷替换

平台 SHALL 校验等待契约中的完整 child 引用，包括 kind、issuer 和 ID。回调提供的附加数据 MUST NOT 覆盖平台核实的 child ID 或 state。相同 command ID 重放 SHALL 校验完整原请求，不改变原回执。

#### Scenario: 引用或结果伪造
- **WHEN** 回调使用同 ID 的其他签发域引用或覆盖 child_outcome 的可信字段
- **THEN** 引用不匹配被拒绝；附加数据不能改变可信 child ID/state

### Requirement: 派发使用仍有效的冻结身份与部署版本

平台 SHALL 在运行前重验 Principal 有效性、workspace 和 admitted Run 的部署/版本快照。实际执行 SHALL 使用该版本的工具、模型、persona 和资源引用；失效或版本漂移 MUST 在模型/工具派发前进入待对账。

#### Scenario: 提交后身份撤销或配置漂移
- **WHEN** 排队期间 Principal 被撤销或冻结部署/版本改变
- **THEN** 不调用 Runtime，Task 进入待对账并记录原因

### Requirement: Platform associates successor attempts before accepting successor facts

When a task resumes from a persistent wait, the platform SHALL record the successor attempt association before folding successor execution events into the Task projection. Event ingestion SHALL reject or quarantine successor events whose attempt association is missing, mismatched, or belongs to another workspace. Historical Run queries SHALL remain scoped to their own attempt facts.

#### Scenario: Successor event without association is quarantined

- **WHEN** the platform receives a running or terminal event for a successor Run that has no recorded association
- **THEN** the event is quarantined for reconciliation and does not change the Task projection

### Requirement: Workflow parent-child callback uses a named adapter and persisted facts

平台 SHALL 提供具名 adapter(`WorkflowChildTaskAdapter`):平台任务的输入载荷可声明确定性子任务步骤序列(`workflow.steps`,非 LLM 决定),dispatcher 经 adapter 逐步执行——每步 MUST 通过真实 `TaskControlService.submit` 提交子任务(系统发起,子任务载荷 MUST 盖章父任务引用与编排元数据),随后父任务 MUST 挂入持久等待,等待契约的 `await_task_ref` 绑定该子任务引用。子任务到达终态时,平台 SHALL 从子任务自身载荷的父引用元数据自动发起经核实的 `submit_workflow_callback`(平台 issuer)——回调 MUST 满足既有核实语义(同 workspace、子任务真实终态与声明一致、契约匹配、一次性 token),父任务被唤醒后子结果摘要 MUST 随 `provide_input` 进入持久输入,下一步 SHALL 仅在上一步核实结果进入后才提交。任一步的子任务失败时,adapter SHALL 按 `on_failure` 声明收敛(`fail`:父任务失败;`await`:父任务转待对账)。父任务等待事实、子任务终态事实与跨进程发出的自动回调 SHALL 各自跨进程重启保持。回调 MUST NOT 消费 token、MUST NOT 改变父任务状态,当:伪造或跨 workspace 的子引用、声明状态与子任务真实终态不符、等待契约不含该子引用。重复回调(同 command_id)SHALL 幂等返回既有回执。

#### Scenario: Real adapter callback wakes the parent after child terminal fact

- **WHEN** a workflow parent waits for a child through the named adapter and the child reaches succeeded
- **THEN** the callback verifies the persisted child terminal fact, consumes the parent wait token once, and requeues the parent with the child result summary

#### Scenario: Cross-workspace or forged child callback is rejected

- **WHEN** a callback references a child from another workspace or a child whose terminal fact does not match the declaration
- **THEN** the command receipt is rejected, the parent wait token is not consumed, and the parent remains waiting

#### Scenario: Declarative steps run in order through real submit and park

- **WHEN** 平台任务输入声明两步确定性子任务步骤并真实派发(无 `_execute` monkeypatch)
- **THEN** 第一步子任务经真实 submit 提交且父任务以 `await_task_ref` 绑定它挂入 `waiting_input`;子任务 succeeded 后自动核实回调唤醒父任务,第二步子任务才被提交;两步的子任务事实与父任务唤醒输入均来自平台持久记录

#### Scenario: Child terminal auto-callback rides platform records without an external caller

- **WHEN** 子任务在某进程到达终态,而父任务等待在另一进程(或父平台进程已重启)
- **THEN** 子任务终态路径从自身载荷的父引用元数据自动发起回调,核实基于双方持久事实,父任务被唤醒且子结果摘要进入其持久输入;HTTP 回调端点对外部调用者行为不变

#### Scenario: Failed step converges per declaration across restarts

- **WHEN** 某步子任务失败(声明 `on_failure=fail`),或父任务等待期间父平台进程重启、子任务在另一进程完成
- **THEN** `fail` 声明下父任务以失败终态收敛且剩余步骤不再提交;重启组合下链条按持久事实收敛,无步骤被重复提交、无业务动作重复发生

### Requirement: 位置修复与竞争去重保留真实事件事实

事件位置唯一性 SHALL 同样覆盖无 workspace 的 Run。升级历史同位事件 SHALL 使用真实创建时间确定保留顺序并以 UUID 作确定性平局排序，比较语义 MUST 在 PostgreSQL 与 SQLite 成立。并发重用 event_id 时，只有相同 workspace 与相同事件事实 SHALL 视为幂等；内容或归属不同 MUST 显式拒绝，不能使原事件或终态投影被竞争输入覆盖。

#### Scenario: UUID 重复位置升级

- **WHEN** PostgreSQL 上两行 UUID 大小顺序与创建时间顺序相反
- **THEN** 升级保留创建较早记录并报告修复，空 workspace 也只能存一行同位置事件

#### Scenario: 同 event_id 异体竞争

- **WHEN** 两个写入请求并发使用同 event_id 但不同 payload 或 workspace
- **THEN** 后者得到显式冲突，原始事实与投影不被替换

### Requirement: 按 Run 查询动作记录的只读 API

平台 SHALL 提供 GET /api/runs/{run_id}/actions。服务端 SHALL 在台账读取之前，按验证后的 workspace 同时核实指定 Run 及其所属 Task 存在、未删除且归属一致；任一不满足 SHALL 统一返回 NOT_FOUND。平台本地台账 SHALL 使用既有派发链的完整 Run 行引用（issuer 与平台 Run ID），SHALL NOT 使用不同的引擎 session 引用或任务最新 Run 替换查询对象。

响应每动作 SHALL 仅含 action_key、execution_id、tool_call_id、tool_name、arguments_digest、side_effect_class、state、outcome、result_digest、pending_reconciliation；SHALL NOT 返回原始参数、完整结果、session 标识、claim token 或凭据。恢复状态与已记录 outcome SHALL 分列来自真实台账，不从 Task 终态推断。

查询 SHALL 不触发执行、重试、对账或状态迁移。真实查询无记录 SHALL 返回 supported=true 与空列表；绑定台账未提供可调用查询能力 SHALL 返回 supported=false 与来源；存储或结果读取失败 SHALL 返回显式 5xx，不冒充空结果。响应和日志 SHALL NOT 包含异常原文中的连接信息、SQL 参数或业务内容，日志仅记录可关联的 Run ID 与异常类型。本 API 只读取 ledger_source 声明的已绑定平台账本，不宣称读取了远端宿主账本。

#### Scenario: 多 Run 与历史记录独立

- **WHEN** 同一 Task 有多个 Run，历史 Run 的 backend_ref 与平台 Run ID 不同
- **THEN** 各查询只返回指定平台 Run 的动作，不混入最新 Run 或后端 session 引用下的其他动作

#### Scenario: Run 与所属 Task 共同隔离

- **WHEN** Run 不存在、已删除或属于另一 workspace，或父 Task 已删除/不属于该 workspace
- **THEN** 响应统一为所请求 Run 的 404，台账查询零调用，不泄露其他资源引用

#### Scenario: 状态来自真实台账而非终态推断

- **WHEN** 一个已终态 Task 的 Run 中仍有成功、领取中、失败、结果未知动作
- **THEN** 恢复状态、outcome 和待对账标记逐项保留台账事实，不因 Task 成功而改变

#### Scenario: 白名单排除敏感与内部字段

- **WHEN** 台账有真实结果内容、session 或 claim token 等内部字段
- **THEN** HTTP 响应只含白名单字段，内部值和完整结果不进入响应

#### Scenario: 真实空结果与 Stub 不支持区分

- **WHEN** 分别查询可调用但无记录的 SQL 台账和不提供查询能力的 InMemory Stub
- **THEN** 前者 supported=true，后者 supported=false 并如实标注 ledger_source，两者均不虚报其他来源已被扫描

#### Scenario: 存储故障不伪装为空且不泄漏诊断

- **WHEN** 查询异常带有连接 URL、凭据或 SQL 参数，或读取结果不能正确投影
- **THEN** 返回固定显式 5xx；不返回空成功，不把诊断原文写入响应或日志；日志可按 Run ID 和异常类型关联

#### Scenario: 查询零副作用

- **WHEN** 对已有领取动作、结果、生命周期与未决命令执行成功、拒绝或故障查询
- **THEN** 台账、outbox、Task/Run 全字段与命令回执保持不变，不推进过期命令或写入新事实

### Requirement: 按 Task 分页查询命令回执的只读 API

系统 SHALL 提供只读查询 `GET /api/tasks/{task_id}/commands?limit=&offset=`（默认 limit=20，limit 上限 100、offset ≥ 0），返回 `{items, total}`：items 为该 Task 的命令回执列表，total 为过滤后总数。排序 SHALL 为 `issued_at DESC, command_id DESC` 的稳定序。查询 SHALL 覆盖该 Task 的全部五种命令状态（`requested`/`acknowledged`/`applied`/`rejected`/`expired`），本能力不提供状态筛选。列表读取 SHALL 是纯已记录事实：`requested` 与 `acknowledged` SHALL 原样返回，SHALL NOT 呈现或改写为 `applied`；已过期的 `requested` 在列表中 SHALL 保持 `requested`；列表查询 SHALL NOT 推进任何命令状态、SHALL NOT 触发命令执行、SHALL NOT 执行单条查询的懒过期清扫。查询引用 SHALL 从可见 Task 行的 issuer_domain 与 id 构造，SHALL NOT 将 issuer 固定为 hecate。Task 可见性 SHALL 先行校验：不存在、已删除或属于其他 workspace 的 Task SHALL 统一返回请求 Task 的 404，且可见性失败时 SHALL NOT 读取命令存储。返回字段 SHALL 沿用单条回执白名单（command_id、kind、state、issued_at、issuer、task_ref、run_ref、expires_at、expected_revision），SHALL NOT 返回 `payload`、`payload_schema_ref`、`detail_ns`、`extra` 等内部内容。命令存储查询或投影失败 SHALL 返回明确的错误（固定文案，不含存储层原文），SHALL NOT 呈现为空列表。既有 `GET /api/commands/{command_id}` 的行为（含懒过期）SHALL 保持不变。

#### Scenario: 分页与稳定排序

- **WHEN** 某 Task 存在多条命令回执，客户端以 limit=1、offset 递增分页请求
- **THEN** 各页按 `issued_at DESC, command_id DESC` 拼接后恰为全集且无重复，total 在数据不变时保持一致

#### Scenario: 五种状态全部原样返回

- **WHEN** 某 Task 的命令中同时存在 requested、acknowledged、applied、rejected、expired 五种状态
- **THEN** 列表包含全部五条且 state 均为持久化的原值，requested/acknowledged 不被呈现为 applied

#### Scenario: 过期 requested 在列表中保持原状

- **WHEN** 某 requested 命令的 expires_at 已过，客户端查询列表
- **THEN** 该命令在列表中仍为 requested，列表查询未将其改写为 expired（单条查询的懒过期行为不受影响）

#### Scenario: 列表查询零状态推进

- **WHEN** 客户端对同一 Task 连续多次查询列表
- **THEN** 各命令的 state、持久化行内容与治理事件均不变，不产生新事实

#### Scenario: 不可见 Task 统一 404 且零存储读取

- **WHEN** 以不存在、已删除或属于其他 workspace 的 task_id 请求列表
- **THEN** 响应均为该请求 task_id 的 404，且命令存储未被调用

#### Scenario: 返回字段限于白名单

- **WHEN** 列表返回任一命令回执
- **THEN** 条目字段仅为单条查询白名单九字段，不含 payload、payload_schema_ref、detail_ns、extra

#### Scenario: 存储失败明确报错

- **WHEN** 命令存储查询抛出异常
- **THEN** 响应为 500 且错误文案为固定文本，不含存储层异常原文，也不返回空列表

#### Scenario: 单条查询行为不变

- **WHEN** 客户端以 `GET /api/commands/{command_id}` 读取一条已过期的 requested 命令
- **THEN** 该读取仍执行懒过期（返回 expired），与本列表查询的只读边界互不影响

#### Scenario: 使用可见 Task 的真实 issuer

- **WHEN** 可见 Task 的 issuer_domain 不是 hecate，且命令存储中存在同 id 的不同 issuer 记录
- **THEN** 列表仅返回该可见 Task 完整引用对应的命令

#### Scenario: 投影失败与存储故障同样受保护

- **WHEN** 命令记录读取成功，但白名单字段投影失败
- **THEN** 响应为固定文案的 TASK_CONTROL_ERROR，不泄露异常原文，不返回成功空列表
