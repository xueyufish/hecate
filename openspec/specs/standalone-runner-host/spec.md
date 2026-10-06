# standalone-runner-host Specification

## Purpose

独立执行宿主(hecate-runner)在无管理平台、无平台管理表、无仓库源码路径的环境中,从 profile 目录加载制品 manifest 并按 step3 执行后端 HTTP 绑定提供最小服务入口,使业务 App 能以技术预览档独立消费执行能力并留下可查询的本地证据。

## Requirements

### Requirement: Cold start without control plane

宿主 MUST 仅凭 profile 目录(manifest、宿主配置、身份信任材料)完成启动并服务请求;MUST NOT 要求管理平台地址、平台管理数据库或完整 `hecate` 主应用的安装;控制面地址未配置或不可达时,只读执行 MUST 仍然完成并留下本地证据。宿主进程 MUST NOT 因控制面缺席而进入降级自授权——本地授权来自客户配置的信任材料,语义与在线无关。受管 profile 是可选叠加:配置了平台地址时宿主按 `managed-runner-enrollment` 能力接入(注册、租约、投递、投影),未配置时行为与本要求所述独立模式完全一致;受管连接的存在 MUST NOT 成为独立执行或本地证据留存的前提。

#### Scenario: Clean install cold start completes a read-only run

- **WHEN** 在干净 venv 中仅安装 hecate-runner 及其声明的依赖,以无控制面配置的 profile 启动宿主并提交一个只读执行
- **THEN** 执行完成,响应携带结果引用,本地证据记录该次执行,全程无主应用代码与仓库源码路径参与

#### Scenario: Missing profile artifacts fail fast

- **WHEN** profile 缺少 manifest、信任材料或配置文件,或 manifest 摘要不匹配
- **THEN** 宿主启动失败并给出指明缺失项的错误,不提供任何服务入口

#### Scenario: 受管地址未配置不影响独立模式

- **WHEN** profile 未配置控制面地址,宿主执行只读任务并写本地证据
- **THEN** 行为与未安装受管能力时一致,无注册/心跳/上传尝试,无因通道缺席的失败

### Requirement: 受管通道为可选叠加且凭据服务端验证

宿主的控制面客户端 SHALL 仅在配置了平台地址与凭据引用时激活;通道认证使用部署域签发的短期凭据,凭据经平台侧验证——客户端自报的身份、角色或作用域声明 MUST NOT 产生任何权限。通道失败(注册被拒、心跳超时、租约过期)MUST NOT 影响独立模式下已授权的本地执行与证据留存语义。

#### Scenario: 自报身份不产生权限

- **WHEN** 宿主客户端在请求中携带自报的管理员身份或角色声明
- **THEN** 平台按凭据验证结果与 enrollment 记录裁决,自报字段被忽略

### Requirement: Server-verified identity at the service entry

宿主 MUST 对每个服务请求做服务端身份校验:凭证对本地信任材料验证,principal、角色与数据域作用域由服务端映射解析。请求体或请求头中自报的角色、域或权限声明 MUST 被忽略;未认证或凭证不可信的请求 MUST 拒绝并写入拒绝证据。预览档角色集仅含 `read_only`;write 与 approval 角色不存在,不能通过请求获得。

#### Scenario: Forged role claim is ignored

- **WHEN** 持有效凭证的调用方在请求体中自报 `admin` 或其他越权角色
- **THEN** 服务端按信任材料解析出的 `read_only` 角色与域作用域执行,自报字段不产生任何权限变化

#### Scenario: Unknown credential rejected with evidence

- **WHEN** 请求携带不在信任材料中的凭证
- **THEN** 请求被拒绝,拒绝记录(含原因类别)可在本地证据中查询

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
### Requirement: Local evidence with no default upload

宿主 MUST 将每次执行与每次拒绝追加写入本地 append-only 证据存储,记录至少:时间、principal、动作或执行标识、outcome 分类(成功/拒绝类别/失败)与结果引用;证据 MUST 可经本地查询接口按 outcome 与 principal 过滤读取。宿主 MUST NOT 向配置之外的任何目标发送业务内容;除已配置的模型 endpoint 与工具指向的业务 API 外,默认零外发。

#### Scenario: Denial evidence is queryable locally

- **WHEN** 发生一次越权拒绝后按拒绝类别查询本地证据
- **THEN** 该次拒绝的记录可读取,含 principal、原因类别与时间

#### Scenario: No upload path exists by default

- **WHEN** 检查预览档宿主的配置与代码路径
- **THEN** 不存在未配置即启用的中心上传通道;证据仅落本地存储

### Requirement: Deterministic CI model and configurable endpoint

宿主默认 MUST 使用确定性 stub 模型,使 CI 场景无网络、无真实模型凭据可运行;配置模型 endpoint 时 MUST 经该 endpoint 调用并把模型来源记录进该次执行证据;使用真实 endpoint 的运行 MUST NOT 被表述为供应商或模型已认证。

#### Scenario: Stub-backed run is deterministic

- **WHEN** 以默认 stub 模型连续运行同一 CI 场景
- **THEN** 执行结果与证据中的业务断言一致,不发生外部模型调用

#### Scenario: Endpoint run labels its model source

- **WHEN** 配置模型 endpoint 并执行
- **THEN** 证据记录模型来源为配置 endpoint,而非 stub,且不携带任何认证性表述

### Requirement: Health and honest shutdown

宿主 MUST 提供健康端点,报告就绪状态与 profile 摘要(backend 类型、能力声明、模型来源、durable 是否启用及存储方言);关停 MUST 拒绝新请求并如实报告在途执行状态。未启用 durable 的预览档 MUST NOT 在关停或崩溃时宣称状态可恢复;启用 durable 的档位关停时 MUST 将在途执行的动作留于台账(已领取未回执的动作在重启后按四态判定),MUST NOT 宣称未落盘的进程内状态(如未持久事件)可恢复。

#### Scenario: Health reflects readiness and profile

- **WHEN** 宿主就绪后查询健康端点
- **THEN** 响应包含就绪状态、能力声明摘要、模型来源与 durable/存储摘要,与实际 profile 一致

#### Scenario: Shutdown reports in-flight honestly

- **WHEN** 关停请求到达时存在在途执行
- **THEN** 宿主拒绝新请求;未启用 durable 时报告显式不可恢复,启用 durable 时在途动作留于持久台账待重启判定,不伪造超出实际保证的恢复承诺
### Requirement: 宿主执行与证据可观测且受身份隔离

宿主 MUST 在接受运行前持久保存准入记录;运行、事件、产物与取消 MUST 限定为原 principal 且当前数据域覆盖原运行数据域。参数校验 MUST 先于业务分发;endpoint 模式 MUST 实际调用配置模型,失败不得静默回退 Stub。取消 MUST 记录请求,并在工具调用边界生效;不能承诺撤回已发生调用。

#### Scenario: 他人或匿名查询 Run

- **WHEN** 匿名或其他 principal 查询已有 Run 或其产物
- **THEN** 拒绝,不返回原运行数据

#### Scenario: 证据不可写

- **WHEN** 准入证据写入失败
- **THEN** 不接受 Run,不调用模型或工具

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

### Requirement: 证据留存有显式的容量阈值、留存期与失败行为

宿主 MUST 支持在配置中声明证据留存策略:留存期(按记录年龄)与容量上限(以后端声明的计量单位);未声明留存策略时行为 MUST 显式——不设上限、不过期,且能力摘要如实报告该缺省,不得静默。留存期到期后的记录 SHALL 被清理,清理后查询不再返回;容量超限时宿主 MUST 先执行留存清理,清理后仍超限则新受保护(非 `readonly`)动作被拒绝(沿用证据不可写的 fail-closed 语义),`readonly` 动作不受影响;拒绝 MUST 带原因类别且可本地查询。证据写入或探测失败 SHALL 按失败类别(不可写/超容量)记录证据门拒绝记录。

#### Scenario: 留存期到期的记录被清理

- **WHEN** 证据记录年龄超过配置的留存期
- **THEN** 该记录经留存清理后不再可查询,清理动作本身有痕迹

#### Scenario: 容量超限在留存清理后仍超限则拒绝新受保护动作

- **WHEN** 证据用量超过配置的容量上限,且留存清理后仍超限
- **THEN** 新的受保护动作被拒绝并带超容量原因,readonly 动作继续执行

#### Scenario: 未配置策略时行为显式声明

- **WHEN** profile 未配置证据留存策略
- **THEN** 证据不设上限、不过期,能力摘要如实报告该缺省,不出现静默的截断或丢弃

#### Scenario: 证据门拒绝记录失败类别

- **WHEN** 证据写入或可写探测因不可写或超容量失败
- **THEN** 拒绝记录携带失败类别,可在本地查询到该次拒绝

### Requirement: 审计留存可委托本地 SQL 存储且语义一致

启用 durable profile 的宿主 SHALL 支持把审计留存委托给宿主本地 SQL 存储(与持久任务库同库):证据记录、查询过滤与可写探测的对外行为与本地文件后端一致,留存期与容量上限按同一配置面声明(计量单位由后端声明)。委托后端 MUST NOT 写入平台管理表,也不改变"默认零外发"语义——委托目标是宿主自有存储,不是中心上传。两后端 SHALL 由同一参数化测试钉住一致语义。

#### Scenario: 委托 SQL 后端后证据行为一致

- **WHEN** durable profile 宿主配置审计留存委托本地 SQL 存储,执行任务并查询证据
- **THEN** 记录、过滤查询与可写探测行为与文件后端一致,留存期与容量策略同样生效

#### Scenario: 委托后端不触碰平台表

- **WHEN** 检查委托 SQL 后端的写入目标
- **THEN** 全部落于宿主本地库,不存在对平台管理表的写入,也不产生任何中心上传通道

### Requirement: 受管 CLI 装配缺一即失败

宿主 CLI SHALL 在 `control_plane` 与 durable profile 同时配置时装配受管执行闭环(通道客户端、持久接收队列、串行执行调度、结果上传与关闭处理);`control_plane` 配置而 durable profile 缺席时 MUST 启动失败并指明原因——受管接收队列即宿主持久任务账本,无本地持久化的受管执行不接受。未配置 `control_plane` 的独立模式行为 MUST 与本能力既有要求完全一致。

#### Scenario: 受管配置装配完整闭环

- **WHEN** profile 同时配置 `control_plane` 与 durable 存储并启动宿主
- **THEN** 宿主完成注册与凭据交换,受管投递经持久接收队列被接受并由串行调度执行,事件按游标上传

#### Scenario: 受管配置缺少持久化时拒绝启动

- **WHEN** profile 配置了 `control_plane` 但未配置 durable 存储并启动宿主
- **THEN** 宿主启动失败,错误指明受管执行要求持久任务账本,不提供任何服务入口

### Requirement: 受管投递经串行调度执行且接受不等于执行

已接受的受管任务 SHALL 由宿主串行调度逐个驱动,复用引擎串行执行槽——同一时刻至多一个受管运行在执行,新任务在槽空闲后继续;受管执行走宿主持久任务账本的同一执行路径(动作意图、领取与结果回填由动作台账把关)。接受记录产生 QUEUED 状态;RUNNING 与终态 SHALL 只由实际执行的状态迁移产生。受管任务 MUST NOT 进入 standalone replay 路径;standalone replay MUST 跳过受管来源任务且不改变其状态。

#### Scenario: 受管任务被接受后进入串行执行

- **WHEN** 宿主接受两个受管投递后调度循环运行
- **THEN** 两个任务按串行槽逐个从 QUEUED 进入 RUNNING 再到终态,任一时刻至多一个运行在执行

#### Scenario: standalone replay 不触碰受管任务

- **WHEN** 进程重启且持久账本中同时存在 standalone 与受管来源的非终态任务
- **THEN** standalone replay 恢复 standalone 任务,受管任务由受管调度按原 Task/Run 恢复,互不误判、互不重复执行

### Requirement: 受管身份经打点校验且默认拒绝

接受受管投递时,宿主 SHALL 把受管执行身份(principal 派生自受管信任根,数据域来自显式配置,默认空)写入持久化任务输入;执行前宿主校验打点身份与当前配置一致,不一致的任务 MUST 停止并转入待对账——不因配置漂移改写既有执行的归属。数据域未配置时受管工具派发按现有域检查拒绝。

#### Scenario: 身份打点一致时执行

- **WHEN** 受管任务接受时打点的身份与重启后宿主当前配置一致,调度恢复该任务
- **THEN** 执行以打点身份进行,工具派发携带该主体与数据域作用域

#### Scenario: 配置漂移后恢复转入待对账

- **WHEN** 受管任务在重启后其打点身份与当前 `data_domains` 配置不一致
- **THEN** 该任务不执行,转入待对账状态并保留原打点,不做第二次同义执行

### Requirement: 受管关闭诚实收尾

宿主关闭 SHALL 先停止接受新受管投递的调度;串行槽内运行中的任务按既有关闭语义诚实收敛(取消合作边界生效或标记未知并转入待对账);退出前 SHALL 尽力完成一次最终事件上传,使平台投影反映宿主最后已知事实;上传失败不阻塞退出且下次启动补传。

#### Scenario: 关闭时运行中的受管任务被诚实收敛

- **WHEN** 受管运行在执行中时宿主收到授权关闭
- **THEN** 不再驱动新受管任务,运行中任务按取消/未知语义收敛并保留持久事实,最终上传尽力执行后进程退出

### Requirement: 正式执行后端 HTTP 契约绑定

独立宿主 MUST 按 `execution-backend.http.v0_1` 语义暴露能力声明、提交、状态、事件游标、取消与 artifact 引用;正式请求和响应 MUST 通过权威 JSON Schema 校验。宿主 MUST 仅依赖随独立发行物发布的 schema/样本语义,不要求导入 Hecate Python 对象或查询平台 ORM。提交 MUST 绑定 transport 校验出的 subject、header/body 一致的 idempotency key 和 canonical request digest;同 key 同请求返回原 backend run,异请求返回版本冲突。

#### Scenario: 公开协议提交与读取

- **WHEN** 非 Python 客户端以权威 `ExecutionRequest` 调用 `/runs`
- **THEN** 获得结构化 backend `run_ref`,并可用该引用查询 `RunStatus`、`EventPage`、cancel receipt 和 artifact 引用

#### Scenario: 幂等重试不重复执行

- **WHEN** 提交响应丢失后以同一 header/body idempotency key 重试
- **THEN** 返回原 backend run,不创建第二个本地 task/run,不重复业务动作

### Requirement: 执行事件持久化并支持重启后查询

启用 durable profile 的宿主 MUST 将本地执行事件映射为 governance event envelope 并写入本地 durable event log;event id 与 source sequence MUST 在重放中稳定,重复相同事件 MUST 幂等,同一位置不同内容 MUST 冲突并进入待对账语义。宿主重启后 MUST 能按 backend run ref 查询已持久化状态与事件游标,且查询授权基于提交时记录的 server-verified subject。

#### Scenario: 重启后查询终态与事件

- **WHEN** durable Run 已到终态后宿主进程重启,原 caller 以结构化 run ref 查询
- **THEN** 状态与事件来自本地 durable 事实,事件游标可续读且不依赖进程内 state

#### Scenario: 重放事件幂等

- **WHEN** 恢复重放生成同一 run 位置与内容的执行事件
- **THEN** event log 不新增重复事件或 sequence,后续游标读取不重复投递

### Requirement: 受管运行派发入口接入租约门且与打点身份同源

受管运行(经理由受管调度驱动的任务)SHALL 在宿主内标记来源;其保护动作派发 SHALL 先经租约门(签名、期限、部署绑定、nonce 消费)再进入动作意图/领取。租约的签发者与部署绑定 SHALL 与受管身份打点同源(同一受管信任根派生),跨宿主或跨部署绑定的租约 MUST NOT 驱动本宿主的受管动作。standalone(非受管)运行的派发路径 MUST 保持不变——本地信任材料语义与租约门无关。

#### Scenario: 受管保护派发先过租约门

- **WHEN** 受管运行派发保护动作
- **THEN** 租约门先于动作意图/领取验证当前租约,通过后才进入既有台账与业务派发路径

#### Scenario: 跨部署绑定的租约不驱动本宿主动作

- **WHEN** 一个绑定其他部署域的租约出现在本宿主租约门
- **THEN** 验证拒绝(audience 不匹配),保护动作不派发

#### Scenario: standalone 派发路径不受影响

- **WHEN** 非 control_plane 的 standalone 宿主执行保护动作
- **THEN** 派发路径与既有语义完全一致,无租约验证参与

### Requirement: 持久等待绑定契约并可跨重启保持

durable profile 的宿主 SHALL 支持任务进入持久等待:manifest 声明的 `approval_required` 工具在动作台账记录意图后不执行业务调用,业务 API 返回输入契约请求(`input_required`)时不产生业务副作用,两种路径都将任务持久迁移到 `waiting_approval`/`waiting_input`。等待记录 SHALL 绑定原 Task/Run、工具与参数摘要、审批或输入契约引用、一次性 `wait_token` 与期限,并与状态迁移同事务提交。等待中的任务 MUST NOT 占用执行槽或被自动重驱动;进程强制终止并重启后等待任务 SHALL 保持等待并可被合法唤醒。非 durable 的 preview profile MUST 继续拒绝 `approval_required` 工具——无持久化就没有可靠等待。

#### Scenario: 审批工具进入持久等待且不产生副作用

- **WHEN** durable 宿主执行包含 `approval_required` 工具的任务
- **THEN** 动作意图已记录但业务 API 无调用,任务持久进入 `waiting_approval`,等待记录含工具/参数摘要、契约引用、一次性 token 与期限,重启后仍为等待态

#### Scenario: 输入契约请求进入持久等待

- **WHEN** 业务派发返回 `input_required` 与契约引用
- **THEN** 任务持久进入 `waiting_input`,无第二次业务调用,等待记录可经授权查询

### Requirement: 一次性唤醒原子应用且拒绝显式可查

宿主 SHALL 提供授权的唤醒命令入口(`provide_input`/`resume`,经服务端验证身份):命令按 `command_id` 幂等记录并出具独立回执。合法唤醒 SHALL 在单事务内校验命令期限与任务绑定、等待记录的 token 匹配/未消费/未过期,然后消费 token、合并提供的输入到任务输入、迁移 `waiting_*→queued` 并重绑新 attempt Run、命令置 `applied` 并发 `command_state` 事件;后续由串行调度以新 attempt Run 执行。过期命令、过期等待、token 不匹配或重复唤醒 SHALL 得到显式 `rejected` 回执(或既有回执的幂等重放),MUST NOT 派发任何工具、不产生业务副作用。

#### Scenario: 合法唤醒合并输入并以新 Run 执行

- **WHEN** 持有有效 `wait_token` 的调用方提交 `provide_input` 命令与输入载荷
- **THEN** token 被一次性消费,输入并入任务输入,任务以新 attempt Run 被串行执行一次,命令回执为 `applied`

#### Scenario: 过期与重复唤醒不派发工具

- **WHEN** 超过命令或等待期限后唤醒,或同一 `command_id` 重复提交
- **THEN** 得到显式 `rejected` 回执或幂等重放的原回执,工具零派发,业务副作用计数不变

#### Scenario: 错误 token 的唤醒被拒绝

- **WHEN** 唤醒命令携带与等待记录不匹配的 `wait_token`
- **THEN** 命令置 `rejected` 并留原因,等待记录不被消费,任务保持等待
