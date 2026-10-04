# Spec Delta

## MODIFIED Requirements

### Requirement: Manifest-gated read-only preview profile

预览档宿主 MUST 在启动时校验制品 manifest:未启用 durable profile 时,声明 `write` 或 `approval_required` 权限工具的 manifest MUST 使启动失败并指明违规工具;启用 durable profile(配置持久化存储)时,`write` 权限工具经 manifest 声明准入——独立模式下业务 API 是授权与审批的权威(SC03"不依赖平台审批服务"),每个 write 动作 MUST 经持久 Action 台账(意图先于分发、原子领取、outcome 回执)执行;`approval_required` 工具在任何档位 MUST 仍被拒绝(审批绑定归 step7)。仅允许列表内的工具可参与执行;工具参数 MUST 在分发前通过其声明的 schema 校验。未启用 durable 的预览档 MUST 将后台自动重试、长任务恢复与持久任务经能力声明报告为 `unsupported`;启用 durable 的档位 MUST 将持久任务与重启恢复如实声明为已支持,MUST NOT 以缺省或静默方式表达任一方向。

#### Scenario: Write tool in manifest fails startup

- **WHEN** 未启用 durable 的 profile manifest 声明了一个 `write` 权限的工具
- **THEN** 宿主启动失败,错误指明该工具名与权限类别,不加载任何执行能力

#### Scenario: Durable profile admits manifest-declared write tools through the ledger

- **WHEN** durable profile 的 manifest 声明 `write` 权限工具且请求经服务端身份验证
- **THEN** 该工具经持久台账执行(意图先落盘、领取、回执),未获业务 API 授权时以业务拒绝结果返回,不产生越权写入

#### Scenario: Approval-required tools stay refused

- **WHEN** 任意档位的 manifest 声明 `approval_required` 权限工具
- **THEN** 启动失败或该工具被拒绝,审批语义不被伪造为已支持

#### Scenario: Invalid tool arguments rejected before dispatch

- **WHEN** 执行请求中的工具参数不符合工具声明的 schema
- **THEN** 分发被拒绝,错误与拒绝证据记录参数校验失败,业务 API 未被调用

### Requirement: Health and honest shutdown

宿主 MUST 提供健康端点,报告就绪状态与 profile 摘要(backend 类型、能力声明、模型来源、durable 是否启用及存储方言);关停 MUST 拒绝新请求并如实报告在途执行状态。未启用 durable 的预览档 MUST NOT 在关停或崩溃时宣称状态可恢复;启用 durable 的档位关停时 MUST 将在途执行的动作留于台账(已领取未回执的动作在重启后按四态判定),MUST NOT 宣称未落盘的进程内状态(如未持久事件)可恢复。

#### Scenario: Health reflects readiness and profile

- **WHEN** 宿主就绪后查询健康端点
- **THEN** 响应包含就绪状态、能力声明摘要、模型来源与 durable/存储摘要,与实际 profile 一致

#### Scenario: Shutdown reports in-flight honestly

- **WHEN** 关停请求到达时存在在途执行
- **THEN** 宿主拒绝新请求;未启用 durable 时报告显式不可恢复,启用 durable 时在途动作留于持久台账待重启判定,不伪造超出实际保证的恢复承诺

## ADDED Requirements

### Requirement: Durable profile 提供持久任务、幂等提交与控制命令回执

启用 durable profile 的宿主 MUST 将每次执行登记为持久 Task(`queued/running/…/reconciliation_required` 生命周期由 `DurableTaskStore` 记录),提交请求携带幂等键(绑定服务端验证主体、部署域与请求摘要):同键同体返回同一 Task/Run,同键异体返回冲突错误,MUST NOT 创建第二个同义执行。取消等控制命令 MUST 落独立回执记录(`requested → applied` 仅在实际生效后;协作取消在工具边界生效),协议层成功 MUST NOT 显示为 `applied`。宿主 MUST 提供最小查询 API:Task 列表/状态、按 Task 的动作恢复查询(四态 + 真实结果引用 + 待对账标记)与按游标的事件读取(读持久事件日志,游标跨重启有效)。

#### Scenario: 提交幂等

- **WHEN** 相同幂等键、相同请求体在首次提交后重复提交
- **THEN** 返回同一 Task/Run 关联,不产生第二次执行;同键不同请求体返回 409 冲突

#### Scenario: 取消命令回执真实

- **WHEN** 取消请求被接受且在工具边界实际生效
- **THEN** 命令记录迁移到 `applied`;仅传输成功而未生效时保持 `requested`/`acknowledged`,不显示 `applied`

#### Scenario: 事件游标跨重启

- **WHEN** 宿主重启后客户端以旧游标读取事件
- **THEN** 从持久事件日志返回游标之后的连续事件与显式缺口标记,不重建顺序

### Requirement: 重启对账不重复派发且恢复真实结果

启用 durable profile 的宿主重启时 MUST 扫描非终态 Task:已成功动作经台账决策回填真实结果(不重新执行);`claimed`/`outcome_unknown` 的写动作 MUST 安全停止并将 Task 标记 `reconciliation_required`(仅经显式对账收敛);QUEUED 未开始任务可重新派发。重启重放 MUST 使用稳定的动作键(run 派生会话与工具名),MUST NOT 因进程内标识变化而绕过台账判定造成重复业务写入。

#### Scenario: 崩溃于写动作 outcome 落盘后

- **WHEN** 写动作 outcome 已持久化、Task 未到终态时宿主崩溃并重启
- **THEN** 恢复查询返回该动作的真实结果引用,业务 API 调用次数不变,无重复写入

#### Scenario: 崩溃于写动作领取后回执前

- **WHEN** 写动作意图与领取已落盘、outcome 未落盘时宿主崩溃并重启
- **THEN** 该动作判定待对账并安全停止,Task 进入 `reconciliation_required`,不自动重跑

### Requirement: 本地证据不可写停止新受保护动作

启用 durable profile 的宿主在派发受保护(非 `readonly`)动作前 MUST 验证本地证据存储可写(以真实可审计的探测记录验证);探测失败时该动作 MUST 被拒绝执行,新的含受保护动作执行 MUST 被拒绝准入,`readonly` 动作 MAY 继续。持久台账写失败 MUST 同样 fail-closed(写动作停止),执行后 outcome 落盘失败 MUST 保持待对账,MUST NOT 把重试证据/回执写成重试业务动作。

#### Scenario: 证据存储不可写

- **WHEN** 本地证据存储写入失败后提交含 write 动作的执行请求
- **THEN** 该请求被拒绝(新受保护动作停止),readonly 请求不受此门禁影响,拒绝被记录

#### Scenario: 执行后落盘失败保持待对账

- **WHEN** 动作已实际执行但 outcome 持久化失败
- **THEN** 台账停留领取态并标记待对账,后续恢复不自动重跑,重试只针对回执落盘
