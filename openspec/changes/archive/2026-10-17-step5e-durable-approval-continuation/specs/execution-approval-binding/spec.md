# Spec Delta — execution-approval-binding

## ADDED Requirements

### Requirement: 持久审批等待的进入与唤醒授权

任务派发路径的执行遇策略要求审批的动作且装配启用持久审批等待时,执行 SHALL 以类型化等待信号停止该次尝试:信号穿出执行循环时 MUST NOT 被记录为执行失败(不得改写为 `needs_review` 等既有停止类别),对应 Run 尝试以显式 `waiting_approval` 停止类别终态化,任务进入 `waiting_approval` 持久等待;等待契约 SHALL 持久化待执行动作上下文(工具名、参数摘要、主体指纹)与审批引用,审批权威 SHALL 同时铸造判定为 pending 的绑定(判定与消费分离)。停车持久化失败时任务 SHALL fail-closed 进入失败终态,MUST NOT 无等待记录继续。`waiting_approval` 的 `resume` 唤醒 SHALL 以持久审批决定为授权门:存在匹配等待上下文的 approved 绑定且绑定校验通过才允许唤醒;仍 pending 时唤醒被拒绝且不消费唤醒 token,任务保持等待;决定为 rejected 时任务转 `approval_rejected` 失败终态,受保护动作零执行;唤醒载荷中自报的批准 MUST NOT 构成授权。

#### Scenario: 策略审批动作停车而非失败

- **WHEN** 启用持久审批等待的任务尝试执行中,模型对受保护动作发起调用且无同步答复者
- **THEN** 该次尝试的 Run 终态携带停止类别 `waiting_approval`(非 `needs_review`、非无类别失败),任务进入 `waiting_approval` 并持久化待执行动作上下文,审批权威存在同上下文的 pending 绑定

#### Scenario: 停车持久化失败即 fail-closed

- **WHEN** 停车所需的等待契约或 pending 绑定写入失败
- **THEN** 任务进入失败终态并记录原因,不存在"无等待记录但任务继续执行受保护动作"的路径

#### Scenario: 无决定唤醒不消费 token

- **WHEN** 绑定仍为 pending(或不存在匹配绑定)时对 `waiting_approval` 任务发起 `resume`
- **THEN** 唤醒被拒绝并记录,唤醒 token 未被消费,任务保持 `waiting_approval` 可再次唤醒

#### Scenario: 拒绝决定进入终态且零执行

- **WHEN** 匹配绑定的判定为 rejected 时对 `waiting_approval` 任务发起 `resume`
- **THEN** 任务转失败终态并携带 `approval_rejected` 类别,受保护动作从未执行(副作用计数为零),唤醒 token 被消费

#### Scenario: 载荷自报批准不授权

- **WHEN** `resume` 唤醒载荷声明 `approved` 但审批权威中不存在匹配的 approved 绑定
- **THEN** 唤醒被拒绝,受保护动作不被放行,拒绝回执可关联到"无持久审批事实"

#### Scenario: 续跑一次性消费与真实回执

- **WHEN** approved 绑定通过唤醒授权门后,任务经原生续跑门在原尝试(同一 Run 与引擎会话)上续跑
- **THEN** 被停动作在派发前经绑定校验(撤销/期限/参数摘要)后恰好执行一次并消费绑定,模型续跑收到真实工具回执,停车前已完成的动作凭会话键恢复事实不被重复执行,后续重复唤醒或重试不得再次消费同一绑定或重复执行该动作

#### Scenario: 原审批与稳定动作身份跨进程保留

- **WHEN** 原批准被消费后、动作领取前执行进程崩溃，新的 worker 恢复同一待执行动作
- **THEN** 审批消费与实际工具派发 SHALL 使用同一服务端稳定动作标识，允许原绑定对同一动作重绑定；不得重铸新的批准来替代原审批，同参数的新动作仍须重新审批

#### Scenario: 在线权威核验恢复进程的时钟

- **WHEN** worker 重启后读取仍有效的持久批准，其时钟锚属于旧进程
- **THEN** SHALL 在在线 SQL 权威中以数据库时间核验时钟后重锚，保留原决定、引用、消费状态和到期时间；权威不可用、时钟回拨或时钟偏差不可验证时拒绝，不延长批准期限
