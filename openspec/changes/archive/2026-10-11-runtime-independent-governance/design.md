# Design

## Context

见 proposal.md 的动机。当前代码事实(来自本 change 的只读调研):

- 平台侧策略上下文 `PolicyContext` 仅携带 `workspace_id`;Gateway 的 `CallerIdentity` 只有 workspace/scope 两个维度,principal、委派、预算均不进入判定。
- 审批硬件分裂:聊天路径用 `FailingClosedApprovalCallback`(事件对 + 作用域缓存),Runner 用持久等待(`WAITING_APPROVAL` + 一次性 `wait_token` + `_wake_grant`),`models/approval.py` 的 `ApprovalRecordModel` 只服务于作用域缓存,三者互不衔接;step6 明确把"合法审批判定"留给本步。
- 配额(`QuotaService`)只做事后计量,无预留/结算;成本估算有已知缺陷(固定单价、估算标志),G4 归本步与 step10。
- 派发边界唯一的身份/签名校验是 Runner 动作台账的 `arguments_digest` 冲突检查;平台路径(`runtime_port_adapter`)直连 `tool_registry`,不经任何统一判定。
- `runtime/tool_access.py` 受 spec 约束保持零外部依赖;G1 已修复路径(MCP 变更动作、匿名拒绝)有回归测试必须保持。

## Goals / Non-Goals

**Goals:**

- 平台 builtin/聊天/Gateway/REST/MCP/A2A 与独立宿主 Runner 共用同一受控动作判定语义与测试。
- 身份信封、审批绑定、预算记账以纯数据 + 小接口落在可独立依赖的组件里,宿主不依赖平台主应用。

**Non-Goals:**

- 受管授权租约的完整 policy profile、断连窗口、重连重验证(I-Cb 后续 change)。
- Sandbox adapter 包装、出口绑定、旁路演练;EventStore fencing 缺口修复(随受管切片)。
- 供应商账单对账、完整 reported/estimated/reconciled 报表(step10)。
- 不改 step6 执行不变量:幂等键、fencing、动作台账、等待/待对账不自动重跑。

## Decisions

### D1: 新增 `packages/hecate-governance` 承载受控动作应用组件

组件内容:`ActionPrincipal` 信封(actor/agent_principal/on_behalf_of/delegation_id/数据域/来源)、受控动作记录(`invocation_id`、参数摘要、副作用分类、判定、审批引用、回执)、审批绑定校验(期限/一次性/参数摘要匹配)、本地 policy profile 接口、预算预留/结算接口。依赖仅 pydantic + 标准库,不依赖 ORM、平台服务或 `hecate` 主包。

- 备选一:放进 `packages/hecate-runtime`。否决:治理不是执行机制,runtime 内核不应长出策略/审批语义;且 runtime 的 AGENTS 边界(自足性)会被 enterprise 语义污染。
- 备选二:扩展 `packages/hecate-durable`。否决:durable 的章程是持久存储/worker/契约;策略判定混入会模糊状态所有权。durable 的既有类型(`ToolSideEffectClass`、`Claims`)通过 import 复用或以纯数据镜像,不在 governance 内重定义语义。
- 命名与边界由宿主这一具名消费者证明必要性(满足 `runtime-pluggability` 规则);平台侧由 `core/composition/` 装配 platform adapter(审批表、quota、vault)。

### D2: 身份信封为纯数据,由三个派生 adapter 构建

`ActionPrincipal` 从三处派生:平台入口(`AuthContext` + agent principal 登记)、Runner 服务入口(`TrustedIdentity` 解析结果)、受管投递(接受时打点的 `_host_identity`)。信封以标准库/pydantic 类型传入策略评估与判定,`tool_access.py` 保持零依赖(execution-security 既有需求)。自报字段一律不进信封。

### D3: 执法点放在派发边界,单服务多薄 adapter

- 平台:共享执行应用服务在调用 `tool_registry.execute` 前经 governance 判定;Gateway `on_call_tool` 与 REST/MCP/A2A 入口把已验证上下文传入同一服务,不自行判定。
- Runner:`BusinessApiToolDispatcher` 外层包装(现有身份绑定咽喉点)在台账领取前插入判定;受管租约门仍在最前,顺序为 租约门 → governance 判定 → 台账 claim → 业务派发。
- **claim 与回执的唯一写入方按路径划分,不重复领取**:已有持久动作台账的路径(Runner durable、平台持久任务/子任务执行)由台账独占 claim 与 outcome 回执,governance 只追加判定记录并引用台账执行键;无台账的直连路径(聊天执行、Gateway 直调)由 governance 的权威动作记录充当 claim/回执所有者——判定、claim(`invocation_id` 占位)、执行回执写入同一记录,补上这些路径此前不存在的持久幂等。任何路径不得出现两个 claim 写入方。
- 恢复优先规则:原生续跑/重启恢复路径先查询动作台账既有恢复记录(已决动作回填),审批消费 MUST NOT 阻断已完成动作的重放;批准消费绑定动作身份(`invocation_id`),消费后、领取前崩溃由持久化的消费记录支持重新领取(语义见 `execution-approval-binding` 的一致性协议需求)。
- 验收补充:并发重复请求同一 `invocation_id` 只执行一次(第二个请求关联同一结果);工具已成功而回执持久化失败时按结果未知进入待对账,不盲目重做(沿用 step6 的 G2 词表与恢复决策表)。
- ToolWorker 的构造函数默认行为不变(`execution-security` 的 Backward compatibility 需求保留):强制力来自 composition 装配了 governance 服务,而非改默认参数。

### D4: 审批事实的权威存储与投影分离

- **权威侧**:审批绑定(主体、参数摘要、资源、工具、部署/数据域、期限)与一次性消费/撤销状态持久化于 durable 动作侧存储。审批判定状态(pending/approved/rejected)与消费状态为**分开的字段**;一次性消费在唯一审批记录上原子设置消费占位 `consumed_by_invocation_id`(写入条件:判定为 `approved`、未过期、未撤销、占位为空),占位写入后其他动作消费同一批准得到显式冲突,同动作恢复凭该占位继续。平台与 Runner 共用 governance 的绑定校验函数;Runner 的审批事实落本地 durable 存储,由本地信任材料中登记的审批主体签发,重启后可查询/消费/撤销,不依赖平台审批服务。
- **投影侧**:平台 `ApprovalRecordModel` 扩展列(参数摘要、目标资源、期限、审批人、决策来源)仅作查询投影。既有 `rebuild_approval_projection()` 把该表定义为事件日志的派生态(事件日志是真源),因此消费/撤销 MUST 先落权威存储或以事件写入,再投影到该表;不在投影上直接改消费状态,否则重建即丢。
- Runner `_wake_grant` 增加审批引用,`apply_wake` 后的派发授权记录依据。alembic expand/contract 迁移,不做破坏性 downgrade。
- **期限的可信时间**:审批权威记录在签发时保存时间锚(墙钟 + 单调钟读数);期限判定发现时钟回拨(当前时间早于签发锚)或重启后时间不可信(单调钟丢失且墙钟与锚偏差超容忍窗口)时,拒绝依赖该期限的新动作——本地审批同样适用,不放宽为放行,也不归入后续受管认证。

### D5: policy profile 为接口,平台与本地各自实现

governance 定义最小判定接口,输入为**经校验的参数内容**(非仅摘要)、身份信封、可信资源/任务上下文与预算状态,输出 allow/deny/require_approval + 工具级范围;参数摘要仅用于审批绑定与台账记录,不作为策略评估的输入——现有参数条件与危险模式规则依赖完整参数(`context.arguments`)判定(如 `.env` 写入、危险 shell 参数),仅凭摘要会漏判。平台实现复用现有 `ToolPolicyPipeline`(`PolicyContext` 增补信封与预算字段,以纯数据传入);Runner 实现从 profile 目录加载本地授权配置,并允许业务 App 注册身份/权限 adapter。评估异常一律 fail-closed(与 Gateway 现行为一致);验收补参数级 DENY 的跨入口回归(REST/MCP/Runner 同参同判)。

### D6: 预算记账扩展 Quota 体系,键为 `invocation_id`

预留为**同步原子判定**:派发继续前等待预留提交成功,并发预留串行扣减,超额并发只放行预算内数量。结算按 `invocation_id` 幂等去重;未知用量标记 `estimated/unverified` 不记零;迟到修订幂等合并。释放/保留规则:判定通过但未派发即失败 → 释放;取消且无未知用量 → 释放,存在未知用量 → 保留至对账或超时策略;结果未知期间预留保留。平台落在 quota/usage 存储,Runner 落在本地 durable 存储,两侧同语义同测试。不改供应商侧任何承诺。

**模型调用边界独立接入**:模型调用(同步与流式)在调用边界生成自己的调用级 `invocation_id`(与工具动作的 ID 同构但各自命名空间):请求发出前预留;同步调用随 usage 返回结算;流式调用在收到 usage 帧或正常结束时结算,流中断或取消且无法取得 usage 时保留未知账;超时/连接断开等异常结束按未知处理进入对账。平台接入点为现有模型调用记录钩子(`_record_cost` 所在路径),Runner 接入点为共享执行装配的模型调用处。

### D7: 凭据代理优先,业务 API 携带可核验上下文

Gateway 既有凭据经纪(headers 注入)扩展为携带服务身份 + on-behalf-of 上下文;Runner 业务 API adapter 增加代理模式,凭据在代理侧注入,调用方参数与日志无凭据。直发仅限短期限域令牌。adapter 配置校验拒绝明文 secret(加载期失败,不降级)。

### D8: 灰度先审计模式,安全底线跨模式不可关闭

governance 接入期支持 audit 模式:新旧判定并行评估、按既有路径执行、差异落日志;按 workspace/入口开关切换强制。**安全底线(已知撤销/硬拒绝、当前策略要求审批的受保护动作缺少有效审批绑定、身份信封缺失或不可验证、本地证据不可写 → 停止新受保护动作)在 audit、强制与回退三种模式下均生效**,回退到既有路径同样执行底线判定,审计模式不得以对比为由暂停底线;可灰度的只有策略差异(阈值、规则宽严),不是底线本身。底线不扩大审批范围:策略明确放行且无需审批的受保护动作不触发"缺审批"分支。G1 既有回归作为不变守护;step6 矩阵(SC04/SC05)继续跑,新增 SC"本地批准写入与重启"、"审批等待期间派发"用例。

### D9: invocation_id 的生成、作用域与既有键映射

`invocation_id` 由派发边界在包装受控动作时生成,作用域为**逻辑调用级**:同一逻辑调用跨 attempt、重启与恢复保持同一 ID(恢复路径从持久化记录取回,不重签);ID 与参数摘要、身份信封一并持久化于动作台账。与既有键的映射:Runner 侧以 `run_id:tool_name` 动作键为逻辑身份基座、在其持久记录中关联 `invocation_id`;ToolWorker 的执行标识同样建立映射。同 ID 携带异参数或异主体再次提交 → 冲突拒绝(与台账 `arguments_digest` 冲突检查同族,扩展到主体维度)。

### D10: 受保护动作的启用门槛与部署信任声明

依赖 Hecate 强制路径保证(工具网关不可旁路等)的受保护(写类)动作,其启用 SHALL 以旁路封闭验证通过为前提,允许的部署组合进支持矩阵。**运维确认/留痕不构成启用依据**——留痕不能替代执行边界。显式豁免只把部署归入较低治理等级的实验范围:其中仅可启用安全不依赖 Hecate 旁路封闭的动作(如目标业务 API 侧经部署验证独立完成最终授权),该等级不计入依赖强制保证动作的认证。旁路封闭的完整验证(Sandbox 包装、出口绑定、攻击演练)仍延后到后续切片;声明随部署配置持久化并进入支持矩阵。

## Risks / Trade-offs

- [每次派发增加判定与可能的 DB 往返,热路径延迟上升] → 本切片**不做授权结果缓存**:判定依赖参数内容、资源与任务上下文,缓存键要么覆盖全部判定输入、要么在任务/资源授权不同的场景漏判(同主体同工具同参数跨任务授权不同),撤销/策略变更的显式失效也覆盖不了任务上下文变化;每动作逐次判定,成本由预算预留账本的轻量行级操作控制(预留同步原子,不可异步化,见 D6)。后续如引入缓存,必须覆盖全部判定输入且动态检查(撤销、期限、预算)仍逐动作执行,另行评审。
- [audit 模式双评估产生大量差异噪音] → 差异按类别聚合计数,采样落详情,审计期有明确期限。
- [审批表扩展迁移影响既有数据] → expand/contract;既有行视为参数摘要未绑定的历史批准,强制期开始后不再授权新参数集(符合"旧批准不能用于新参数")。
- [本地 policy profile 配置错误导致全部拒绝] → fail-closed 是有意行为;提供启动期配置自检与明确错误信息,不提供"跳过策略"逃生门。
- [范围滑向受管切片] → 租约门代码不动;受管 profile 认证条目留在 proposal 的"不含"清单,review 时核对。

## Migration Plan

1. 先落 `hecate-governance` 包 + 平台/Runner adapter(判定默认 audit 模式)。
2. 审批表扩展迁移 + 绑定校验接入聊天回调与 Runner 唤醒。
3. 预算预留/结算接入平台 quota 与 Runner 本地账。
4. 凭据代理与 on-behalf-of 上下文接入 Gateway 与 Runner 业务 adapter。
5. 按 workspace/入口逐段开启强制;回退 = 关闭强制开关回到既有路径,但安全底线(D8)在回退后仍生效;已产生的历史证据与审批消费状态保留,不复活。
6. 验证:`ruff`/`mypy`/scoped pytest + SC 新用例 + G1/step6 回归全绿;真实模型多轮验收归 step5e,不在本 change 冒充。

## Open Questions

- 无:受管窗口参数、沙箱包装细节均属后续切片,不影响本设计的组件边界与任务分解。
