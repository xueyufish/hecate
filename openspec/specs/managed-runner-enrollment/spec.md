# managed-runner-enrollment Specification

## Purpose

受管宿主闭环:已通过操作员准入的独立执行宿主(hecate-runner)接入平台控制面,接收受管新任务与控制命令、以限时授权租约执行、把执行事实投影回平台;断连不降级为本地自授权,重连重验信任后恢复。平台与宿主共享 durable 契约(命令幂等、事件去重、租约 fencing),每个字段一个权威写入方。

## Requirements

### Requirement: 受管投递经接受记录对账且不启动同义执行

平台向已准入且显式选择受管新任务的 enrollment 投递任务意图;投递 SHALL 经版本化 envelope(部署来源、Run ID、命令 ID、序号)并在平台侧 outbox 持久化。宿主 SHALL 在本地事务中记录接受记录后再执行;重复投递(同 ID 同内容)经接受记录幂等返回,同 ID 异内容拒绝;投递响应丢失时平台按幂等键与已接受记录对账,状态未知 MUST NOT 启动第二次同义执行。调度所有权变更 SHALL 经租约 fencing——旧 ownership 的迟到写入被拒绝并留痕;第一版 MUST NOT 迁移活跃 Run。

#### Scenario: 投递响应丢失后对账不重复执行

- **WHEN** 平台投递任务意图后未收到宿主响应,重试投递
- **THEN** 宿主按接受记录幂等返回首次结果,任务不产生第二次执行

#### Scenario: 旧 ownership 迟到回执被拒绝

- **WHEN** 调度所有权变更后,旧持有方提交迟到回执
- **THEN** fencing 校验拒绝该回执并留事件痕,权威状态不被覆盖

### Requirement: 执行事实投影为单写方且按游标去重补传

宿主上传执行事实与治理事件;平台按 event_id 去重落为 Run 投影与治理事件,投影 MUST NOT 覆盖宿主本地执行事实(平台保存带来源/序号的投影,不回写宿主状态)。上传按宿主侧游标续传;重连后历史事件按游标去重补传,不重复派发任务、不重做业务写入;上传中断不阻塞宿主本地执行与证据留存。

#### Scenario: 断连期间的事件重连后补传

- **WHEN** 宿主断连期间继续本地执行并留存事件,重连后按游标补传
- **THEN** 平台按 event_id 去重接收全部事件,无丢失、无重复投影

#### Scenario: 投影不覆盖执行事实

- **WHEN** 平台接收的投影数据与宿主本地执行事实不一致
- **THEN** 宿主本地事实保持权威,平台投影如实记录来源与接收时间,不反向改写宿主

### Requirement: 限时授权租约约束受管动作且断连不自授权

平台按 `security-claims` 语义签发限域限时租约(签发者、受众、部署绑定、主体、动作范围、期限、防重放);宿主 MUST 验证签发者、受众、部署绑定与期限后接受新工作。断连后租约到期,新受保护动作 MUST 停止;宿主 MUST NOT 切换为本地自授权或更换信任根。profile SHALL 显式配置最大陈旧窗口与过期后的拒绝行为;需在线判定的高风险动作在断连时停止。

#### Scenario: 租约过期后新受保护动作停止

- **WHEN** 断连超过租约期限后宿主收到新的受保护动作请求
- **THEN** 拒绝执行且不产生本地自签授权,拒绝留证据

#### Scenario: 防重放拒绝旧租约

- **WHEN** 已消费或已过期的租约被重放使用
- **THEN** 宿主拒绝该租约并留拒绝记录

### Requirement: 重连重验信任后才恢复新工作

重连 SHALL 先重新验证 enrollment 仍处于准入状态、信任根未变更、撤销与期望配置,通过后才允许新受保护动作。重复命令经 command_id 幂等;过期审批、旧授权、迟到命令 MUST NOT 重新激活动作;未对账 Run 维持原身份/版本与待对账状态。信任根更新与退出受管模式 SHALL 仅经显式管理员流程及审计,网络重连 MUST NOT 改变调度权。

#### Scenario: 重连验证通过后恢复

- **WHEN** 宿主重连且 enrollment 准入、信任根、撤销与期望配置均验证通过
- **THEN** 宿主接受新工作,历史事件按游标去重补传完成

#### Scenario: 重复控制命令不重复业务动作

- **WHEN** 同一命令经重连被重复投递
- **THEN** command_id 幂等,业务动作不重复执行,回执状态不二次改写

#### Scenario: 准入被撤销后重连拒绝

- **WHEN** enrollment 被操作员撤销后宿主尝试重连
- **THEN** 新工作派发停止,已留存本地事实按策略保留,不因重连恢复调度权

### Requirement: 操作员准入经可信解析且调度权不经网络变更

enrollment 注册保持命名引用待定语义;准入 SHALL 要求活跃 workspace 管理员、可信解析器对宿主身份与信任根的肯定解析、已安装契约版本与能力声明。受管新任务选择(`managed_new_runs`)MUST 只经操作员动作变更;网络事件(注册、心跳、重连)MUST NOT 改变调度权。解析器缺失或失败时准入保持待定并留审计。

#### Scenario: 不可信解析阻断准入

- **WHEN** 宿主身份或信任根无法经可信解析器解析
- **THEN** 准入保持待定,拒绝审计记录原因,managed_new_runs 不变

### Requirement: 平台投影仅在宿主事件后进入执行状态

受管投递的接受与确认回执 SHALL 只表达投递对账事实(已接受),不表达执行状态;平台 Run 投影的 `queued`/`running`/终态 SHALL 仅由宿主上传的实际执行事件(`task_state`/`run_terminal`)折叠产生。宿主终态迁移 SHALL 上传携带结果载荷(status/error/content)的 `run_terminal` 事件,使平台投影获得真实终态与错误信息;旧序号或迟到的宿主事件 MUST NOT 回滚已折叠的终态投影。

#### Scenario: 接受后平台投影仍无执行状态

- **WHEN** 宿主已接受投递并向平台确认,但尚未开始执行
- **THEN** 平台投递记录为已接受,平台 Run 投影不进入 running 或任何执行状态

#### Scenario: 终态经 run_terminal 投影且不回滚

- **WHEN** 宿主上传 `run_terminal`(含 status 与错误)后,又收到旧序号事件
- **THEN** 平台投影呈现真实终态与结果载荷,旧序号事件被忽略,终态不被重开

### Requirement: 重启后按原 Task/Run 恢复且补传不跨 Run 跳过

宿主进程重启后,已接受未执行的受管任务 SHALL 恢复为同一本地 Task/Run 并继续执行,不新建执行、不重复已完成的业务写入(动作台账把关);持久化事件中未上传的部分 SHALL 在重连后按 Run 独立游标补传——补传任一 Run 的事件 MUST NOT 跳过或丢失其他 Run 的事件,重复上传由平台按 event_id 去重吸收。

#### Scenario: 接受后宕机重启恢复同一 Task/Run

- **WHEN** 宿主接受受管投递并持久化后进程终止,重启后调度循环恢复
- **THEN** 该任务以原本地 Task/Run 标识被驱动至终态,业务副作用仅发生一次

#### Scenario: 重连补传多 Run 事件互不跳过

- **WHEN** 两个受管 Run 在断线期间各自产生事件,宿主重启后重连上传
- **THEN** 两个 Run 的全部事件按各自游标完整补传,平台去重后无丢失、无重复投影

### Requirement: 受管保护动作在动作边界验证当前限域限时租约

受管运行的保护动作(非 readonly)SHALL 在动作意图/领取之前验证当前租约——签名、期限、部署绑定、未消费 nonce,且请求的数据域必须被租约的 `scope`(数据域允许列表)允许;验证 SHALL 发生在实际派发入口,不是可绕过的独立验证器。平台 SHALL 按 enrollment 的操作员受管范围签发携带 `scope` 的限域限时租约(每次 pull 附带);scope 为操作员受控字段,网络事件不得变更。被拒绝的保护动作 MUST NOT 调用业务 API,拒绝 SHALL 产生显式结果与证据记录且业务副作用计数为零;readonly 派发与既有动作台账/对账语义不变。

#### Scenario: 断连到期后保护动作被拒绝且零副作用

- **WHEN** 宿主断连超过租约期限后,受管运行尝试派发保护动作
- **THEN** 动作边界拒绝该派发,业务 API 调用计数为零,拒绝留证据,readonly 派发不受影响

#### Scenario: 超出租约范围的跨域请求被拒绝

- **WHEN** 保护动作请求数据域不在当前租约 scope 内
- **THEN** 动作边界拒绝,业务 API 无调用,拒绝结果与证据可查询

#### Scenario: 旧租约重放被拒绝

- **WHEN** 已消费 nonce 的旧租约被用于新的保护动作
- **THEN** 动作边界拒绝且不产生本地自签授权,业务副作用计数为零

### Requirement: 重新授权不重做结果未知的动作

租约到期或拒绝期间,受管运行 SHALL 如实完成既有语义:结果未知的动作维持待对账;重连取得新租约后,SHALL 只恢复执行未开始或未确定结果边界之外的新动作,已领取/已记录结果的动作经台账仲裁不会被第二次执行。重启后租约门为空,保护动作 SHALL 在首次成功 pull 取得新租约前被拒绝。

#### Scenario: 重连后新动作恢复且未知结果只对账

- **WHEN** 宿主重连取得新租约后调度继续,存在一个结果未知的已领取动作
- **THEN** 未开始的保护动作在新租约下正常执行,未知结果动作经台账仲裁不重做业务写入

### Requirement: Managed commands produce effect receipts before platform projection changes

The platform SHALL persist managed control commands before delivery and SHALL treat delivery success as transport acknowledgement only. The host SHALL persist each received command in a local inbox keyed by command_id and target Task/Run, return the existing receipt for duplicate commands, and upload an effect receipt only after the command is applied, rejected, or expired. Platform projections SHALL change execution state only from host effect receipts or execution events, not from command delivery responses.

#### Scenario: Duplicate managed command returns the original receipt

- **WHEN** the same command_id is delivered twice to the managed host
- **THEN** the host returns the original command receipt and does not apply the command twice

#### Scenario: Delivery success does not mark the command applied

- **WHEN** the platform successfully delivers a cancel or resume command but the host has not yet applied it
- **THEN** the platform command remains requested or acknowledged, and the Task/Run projection is unchanged

### Requirement: Managed wake creates a platform-visible successor attempt

When a managed waiting task is legally woken by a technical token, the host SHALL consume the wait token once, merge the accepted input, create a successor local Run/attempt, and upload the successor association to the platform before successor execution events are folded into the platform projection. The original waiting Run SHALL remain queryable as waiting or terminal according to its own facts and MUST NOT inherit the successor state.

#### Scenario: Wake links successor attempt before execution projection

- **WHEN** a managed waiting task is woken and requeued
- **THEN** the platform records the successor Run association before it accepts running or terminal events from that successor

#### Scenario: Original waiting Run remains scoped to its facts

- **WHEN** the successor Run completes after a wake
- **THEN** queries for the original Run do not borrow the successor terminal state or wait token

### Requirement: Real platform HTTP acceptance closes the managed delivery gate

The managed delivery gate SHALL be closed only by an installed Runner process communicating with the platform over the public HTTP control-plane APIs and a real network transport. The acceptance test SHALL cover lost accept responses, resubmission, serial execution, status projection, host restart, reconnect, and multi-Run event replay. ASGI-only or in-process component tests MAY support development but MUST NOT mark this gate complete.

#### Scenario: Lost accept response does not create a second execution

- **WHEN** the platform loses the host's accept response and retries the same delivery over HTTP
- **THEN** the host returns the original accepted Task/Run, does not create a second execution, and later projects exactly one terminal result

#### Scenario: Reconnect replays each Run independently

- **WHEN** multiple managed Runs generate events while disconnected
- **THEN** reconnect uploads every Run's pending events without advancing one Run's cursor past another Run's events
