# Spec Delta

## Purpose

平台任务控制面:以 `durable-execution-contract` 定稿的契约语义(Task 生命周期、命令回执、幂等键、治理事件 envelope)为基线,交付平台侧的持久投影、任务控制 API(提交/查询/事件读取/命令/待对账)、治理事件发射与调度器接线,使平台对"任务在哪、发生过什么、哪些命令未确认"有权威可查的记录。

## ADDED Requirements

### Requirement: 平台任务提交经幂等键绑定服务端上下文

`POST /api/tasks` SHALL 接受业务目标、执行目标(agent/deployment 引用)、输入与可选预算/截止时间,并在服务端验证的调用主体与 workspace 作用域内处理幂等键:键绑定主体、workspace 与规范化请求摘要。相同键、相同摘要的重复提交 SHALL 返回首次提交的 Task/Run 关联,不产生第二次执行;相同键、不同摘要 SHALL 返回冲突错误,原有关联不被改写;键绑定的主体/workspace 与验证上下文不一致 SHALL 被拒绝。提交成功 SHALL 返回持久化的 task_id 与 run_id;请求可携带等待参数以获取同步等待视图(在 Task 到达终态或超时后返回),未携带时提交立即返回 queued 状态。派发模式为进程内执行(A 轨 worker 合入前),平台 MUST NOT 宣称跨重启的后台执行保证。

#### Scenario: 同键同摘要幂等

- **WHEN** 同一主体以相同幂等键与相同请求体重复提交
- **THEN** 响应返回与首次提交相同的 task_id/run_id,系统内不产生第二个 Task

#### Scenario: 同键异摘要冲突

- **WHEN** 同一主体以相同幂等键提交不同请求体
- **THEN** 响应为冲突错误,首次提交的关联不被改写

#### Scenario: 跨主体键作用域拒绝

- **WHEN** 幂等键被绑定主体之外的验证上下文重放
- **THEN** 提交被拒绝,不返回原关联

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

#### Scenario: 关键迁移发射治理事件

- **WHEN** Task 从 queued 迁移到 running 再到 succeeded
- **THEN** 每次迁移产生一条含 actor 与 source 的版本化事件,按时间可追溯

#### Scenario: 游标断线恢复

- **WHEN** 消费者读取某 Run 事件到游标 C 后断线,重连后携带 C 继续读取
- **THEN** 返回 C 之后的事件,不重复已读事件也不跳过新事件

#### Scenario: 跨 workspace 事件读取隔离

- **WHEN** 以 workspace A 的上下文请求 workspace B 的 Run 事件
- **THEN** 响应与 Run 不存在时不可区分,不泄露事件内容

### Requirement: 待对账查询 API 汇聚未收敛事实

`GET /api/reconciliation/pending` SHALL 以 workspace 作用域返回处于待对账状态的事实:生命周期为 `reconciliation_required` 的 Task、Action 台账中 `pending_reconciliation` 的记录(生产台账实现合入前该列表为空且显式标注来源为开发 Stub)、以及超过有效期仍未决的命令。查询 MUST NOT 触发任何对账动作或状态改写(过期命令的惰性 `expired` 收敛除外);结果中的每项 SHALL 携带足够的引用(task/run/action/command 标识)供后续对账。

#### Scenario: 待对账任务可见

- **WHEN** 一个 Task 因执行结果未知被标记 reconciliation_required 后查询待对账 API
- **THEN** 该 Task 连同其标识与当前 revision 出现在结果中

#### Scenario: Stub 台账来源显式标注

- **WHEN** 在生产台账实现合入前查询待对账 API
- **THEN** Action 部分返回空列表且响应显式标注其来源为开发期 Stub,不虚报已扫描生产台账

### Requirement: 调度器触发经 executor registry 真实执行

定时任务调度器的执行路径 SHALL 将触发分派给 executor registry 按任务目标(agent/workflow)解析的执行器,执行器的结果映射为执行记录的成功/失败与错误信息;不执行任何实际工作的空转路径 MUST 删除。分派 SHALL 尊重既有并发上限与多节点咨询锁语义;执行器失败 MUST 记录失败状态与错误信息,不吞错为成功。

#### Scenario: 调度触发真实执行

- **WHEN** 一个绑定 agent 的启用定时任务到达触发时间
- **THEN** 调度器经 registry 将执行分派给 agent 执行器,执行记录反映执行器的真实结果

#### Scenario: 执行器失败如实记录

- **WHEN** registry 分派的执行器返回失败
- **THEN** 该次执行记录为 failed 并携带错误信息,不被记为 success

### Requirement: seam 绑定经 composition 单点切换

平台对持久执行接缝(任务状态存储、命令记录器、Action 台账)的消费 SHALL 经 composition 的进程级绑定点:开发/测试默认绑定 InMemory Stub;生产绑定平台 PostgreSQL adapter;每个生产实现 MUST 注册进参数化契约套件并全量通过既有断言。绑定点 MUST 是唯一的,后续核心实现(durable-execution-core)合入时在同一处切换,API 与服务层代码不感知实现替换。PostgreSQL adapter 的每次调用 SHALL 使用独立短事务 session,不在并发请求间共享会话。

#### Scenario: 生产绑定通过契约套件

- **WHEN** 平台 PostgreSQL adapter 注册进参数化契约套件
- **THEN** 既有全部契约断言(生命周期迁移、回执状态、幂等键、领取仲裁)对该实现原样通过

#### Scenario: 开发绑定不依赖数据库

- **WHEN** 开发/测试 profile 下任务控制服务运行
- **THEN** 生命周期与命令回执经 InMemory Stub 工作,不要求新表存在
