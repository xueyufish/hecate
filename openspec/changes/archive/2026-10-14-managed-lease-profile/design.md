# Design

## Context

见 proposal.md 的动机。当前代码事实(来自本 change 的只读调研):

- `hecate_durable.contracts.credentials.Claims` 为冻结 dataclass(iss/aud/sub/exp/nonce/tenant/delegation_ref/scope),`to_dict` 只序列化非 None 字段——新增可选字段对既有令牌的 HMAC 验证向后兼容;`verify_lease` 只校验签名、期限、受众。
- 平台签发在 `src/hecate/execution/managed_channel.py::issue_lease_for`(scope 取 enrollment 的 `managed_scope`);`StandaloneEnrollmentModel` 已有操作员受控 JSON 列先例(`managed_scope`、`installed_versions`)。
- Runner 侧 `LeaseGate`(`packages/hecate-runner/src/hecate_runner/managed.py`)校验签名/身份/scope/nonce(内存+JSONL 重放记忆);`exp` 用裸墙钟,无可信时间。引擎 `_lease_refusal` 在受保护动作前执行租约门与有界等待续租。
- `hecate_governance.clock` 已交付 `TimeAnchor/validate_clock/TrustedClock`(本地切片 D4),当前只接入审批期限判定(`ApprovalGate.authorize`);进程重启后单调锚丢失。
- 期望配置:`TrustRootModel.config_fingerprint` 存在、`TrustRootRegistry.refresh_config_fingerprint` 可写,但宿主从不上报、平台从不比对——只写字段。
- 平台事件:`PlatformEventService.next_sequence` 为无锁 `max+1`;`platform_events` 只有 `event_id` 唯一索引,`(workspace, run_issuer, run_ref, source_sequence)` 为非唯一索引;`append_resequenced` 迟到事件重排到队尾;两写入方(平台 emit / 投影器)并发分配可同位静默落地。durable 侧已有可镜像的模式:`SqlEventLog` 的位置唯一约束 + 冲突显式 + 读取端 gap envelope。
- `GovernanceService` 安全底线(六类停止)跨模式生效;`PolicyProfile` 目前唯一实现为 Runner 的 `LocalPolicyProfile`。

## Goals / Non-Goals

**Goals:**

- 受管授权租约获得可认证的 policy profile:签发者/受众/主体/scope/策略版本/期限/防重放全量校验,断连最大陈旧窗口与真实时钟偏移经可信时间判定,高风险断连规则(在线或有效本地审批绑定)。
- 重连期望配置从只写字段变为实际执行的验证门。
- 平台治理事件存储获得位置唯一性与分配冲突显式化,fencing 缺口关闭。
- 平台与宿主共用同一套 profile 判定语义与测试(复用本地切片的组件,不新建执行点)。

**Non-Goals:**

- 不启用受保护写动作(`write_enablement: disabled` 不变,旁路封闭仍归后续切片)。
- 不做 I-Cb 里程碑其余组成:step11 中心发布、step14 平台最小视图、step16 完整断连故障集与 PostgreSQL 进程矩阵认证(本 change 的 PostgreSQL 覆盖以既有条件跳过模式为准)。
- 不做供应商账单对账与 reported/estimated/reconciled 报表(step10)、step5e 真实模型验收。
- 不改 step6 执行不变量:nonce 重放、续租等待、命令幂等、事件 `event_id` 去重、终态不重开语义全部保持。
- 不为平台事件读取增加 gap envelope(平台 `source_sequence` 是到达序标签而非权威序,重排语义已存在;孔洞不影响游标分页)。

## Decisions

### D1: `Claims` 增加可选签名 `policy_version` 与 `iat`,enrollment 增加操作员受控 `lease_policy`

- `Claims` 新增 `policy_version: str | None`、`iat: str | None`(ISO 时间,与 `exp` 同格式);`to_dict/from_dict` 按 None 省略,旧令牌可验证、旧网关可解析。`lease_claims()` 新增 `policy_version` 入参并写入 `iat = 签发时刻`;`verify_lease` 不强制二者存在(通道凭据不携带),受管语义由 profile 层执行。
- `StandaloneEnrollmentModel` 新增 `lease_policy` JSON 列(`{policy_version, max_staleness_seconds}`,默认空,操作员随准入/opt-in 设置,网络事件不可变更——与 `managed_scope` 同治理)。平台 `issue_lease_for` 把 enrollment 的 `policy_version` 写入租约。
- Runner `ControlPlaneConfig` 的 `control_plane` 块新增 `lease_policy`(期望 `policy_version` + 本地 `max_staleness_seconds`)。**两侧无需同步配置值**:有效离线窗口取 `min(exp, iat + 本侧配置窗口)`,两侧各按已知值执法,天然 fail-closed(任一侧更短即更早拒绝);文档记录该性质,不引入协商协议。

### D2: 受管 profile 落 `hecate_governance.managed`,纯数据事实视图,不新增跨包依赖

- 新模块 `packages/hecate-governance/src/hecate_governance/managed.py`:`ManagedLeaseFacts`(纯数据:issuer/audience/subject/tenant/scope/policy_version/issued_at/expires_at)、`ManagedLeasePolicyConfig`(期望策略版本、最大陈旧窗口、签发者允许列表)、`ManagedLeaseProfile(PolicyProfile)` 与 `ManagedRevocationView(RevocationView)`。
- 租约的密码学校验留在 `verify_lease`(hecate-durable);Runner 侧 `LeaseGate.check()` 成功后把 `Claims` 转换为 `ManagedLeaseFacts` 供 profile 消费——governance 包不依赖 durable(遵循本地切片 D1 的依赖边界,事实以纯数据镜像)。
- `ManagedLeaseProfile` 组合本地规则评估(复用 `LocalPolicyProfile` 的规则配置与评估函数,managed 模式下工具级规则仍生效)并叠加受管门:受管运行的受保护动作在租约事实缺失、超窗、策略版本不匹配或时间不可信时拒绝。它是 `PolicyProfile` 的第二实现(本地 profile 为第一实现),满足 `runtime-pluggability` 准入;装配由 runner `__main__` 按 `control_plane` 是否配置选择,平台侧测试直接构造。
- 撤销视图合并两源:本地 `revoked_principals`(本地切片)+ 信任根撤销状态(经通道错误面观察,宿主记录"信任根已撤销"事实供 profile 查询)。

### D3: 租约时间锚打在安装时刻,期限/陈旧判定经 `validate_clock`

- `LeaseGate` 构造函数接受可选 `clock: TrustedClock`;每次 `update()` 安装新租约时打 `TimeAnchor`(墙钟+单调钟),`check()` 判定 `exp` 与 `iat + max_staleness` 前先 `validate_clock(anchor, now)`——回拨/不可信即拒绝依赖期限的动作,不放宽。
- 进程重启后租约门为空(既有语义),新 pull 安装新租约即建立新锚——避免"重启后时间永远不可信"的过度拒绝(与审批锚不同:审批绑定跨重启持久,锚丢失按本地切片 D4 拒绝;租约不跨重启,锚随租约生命周期)。`iat` 用签方签名值(安装与签发相差一个 poll 间隔,在容忍窗口内);陈旧窗口从 `iat` 起算,不信任本机记录的安装时间。

### D4: 高风险断连规则的执法点在受保护动作授权序列,审批绑定不消耗租约

引擎受保护动作授权顺序改为:

1. 先查本地审批权威:存在与该动作(主体、工具、参数摘要、资源、部署/数据域、任务范围)匹配、未撤销未消费、期限经可信时间判定有效的审批绑定 → 放行(记录授权来源 `local_approval`),不消耗租约 nonce;
2. 否则走既有租约门(校验 + 有界等待续租),授权来源记 `lease`。

两条路径都产生判定与回执证据;审批消费沿用本地切片的 CAS(一次性 `consumed_by_invocation_id`),已决动作回放不重查。`GovernanceService` 及其安全底线不变(该序列在 governance 之前的引擎边界,与本地切片 D3 的"租约门 → governance → 台账"顺序一致,插入的审批查询复用 governance 的绑定校验函数,不另写一套校验)。断连超窗且无绑定的动作仍按既有续租超窗语义显式拒绝、零业务副作用。

### D5: 重连期望配置指纹:宿主本地计算、pull/accept 边界比对、events 上传不受阻

- 宿主指纹 = 规范化 JSON(runner profile 去 secret 引用值后的配置 + 已装载执行定义摘要列表)的 SHA-256,`profile.py` 提供计算函数,不接受请求参数指定。
- `ManagedChannel` 在凭据交换与每次 pull 请求携带指纹(请求字段,签名体之外——指纹是被比对的事实,不是被验证的凭据)。
- 平台在 pull(投递+命令+租约)与 accept 边界比对:操作员已通过 `refresh_config_fingerprint` 登记期望值且与上报不一致 → 拒绝 pull/accept 并返回显式错误(不投递新任务、不签发租约、不下发命令);`/managed/host/events` 与 effect 回执上传**不做**该比对,断连期间证据补传不因配置漂移受阻。未登记期望值时按未启用该门处理(向后兼容)。
- 宿主收到指纹不匹配错误:记录漂移证据、继续 poll(等待操作员刷新期望值或修正配置),租约自然到期后受保护动作按 D3/D4 拒绝——不需要新的降级模式。

### D6: 平台事件位置唯一约束 + 分配冲突显式收敛

- `platform_events` 新增唯一约束 `(workspace_id, run_issuer_domain, run_ref_id, source_sequence)`(alembic expand 迁移;先修复后建约束:同位多行时保留最早主键行、记录被移除 `event_id` 于迁移日志,存在无法确定性修复的数据则迁移失败并显式报告)。
- `emit`/`append_resequenced` 在 `flush` 捕获 `IntegrityError`:savepoint 回滚 → 按 `event_id` 重查(已存在即幂等跳过,返回已存 envelope)→ 否则有界重试新序号(重分配 `max+1`)。并发竞争收敛为"恰好一条落库,另一条去重或重排",不再同位静默落地。
- 迟到事件沿用 `append_resequenced` 重排到队尾的既有语义(平台序号是到达序标签);重试可能留下序号孔洞,游标分页(`WHERE source_sequence > after ORDER BY`)不受孔洞影响,文档明确孔洞合法性。
- 写入方分列保持:平台 `emit`(`source=PLATFORM`)与投影器(`append_resequenced`,宿主来源)各写各的;投影去重与终态不重开语义不动。

### D7: 验证矩阵与既有回归

- 新增:profile 单测(策略版本/陈旧/时间不可信/撤销/本地审批放行)、LeaseGate 可信时间与锚重建、事件并发分配冲突与迟到重排(真实 SQLite + 既有 PostgreSQL 条件模式)、指纹不匹配拒新工作补传继续(真实 HTTP managed channel 测试)。
- 保持:G1 回归、SC03/SC04/SC05(含 wake chain)、lease gate 既有套件、managed loop、`event_id` 去重与投影回归全部不回退。SC05 扩展"断连超窗 + 本地审批绑定放行/无绑定拒绝"用例。

## Risks / Trade-offs

- [迁移遇到历史同位数据,自动修复有误删风险] → 修复规则确定性(最早主键),被移除 `event_id` 记录于迁移输出;无法判定时迁移失败要求人工处理,不静默。
- [审批绑定放行路径看似"绕过"租约] → 绑定来自本地审批权威(企业登记审批人签发)、一次性 CAS 消费、独立证据路径;与租约是并列授权源而非旁路,评审用"同批次无绑定动作仍拒"验收。租约计数语义(每租约一动作)不变。
- [两个 `PolicyProfile` 实现语义漂移] → 共享契约测试套件(同输入同判定断言跨 local/managed 两 profile),floor 由 `GovernanceService` 统一保证不依赖 profile。
- [时间不可信触发拒绝风暴] → 有意保守(fail-closed);租约锚随新安装重建,恢复路径明确;拒绝原因区分"时间不可信"与"确实超窗",便于运维定位。
- [指纹比对被当作宿主完整性证明] → 指纹只捕获配置漂移(过期配置、未升级定义),防恶意宿主不在本门职责(信任根材料与准入负责);proposal/support-matrix 如实登记。
- [序号孔洞被外部消费者误读为事件丢失] → 平台事件 API 的游标语义文档化(序号为到达序,孔洞不表示丢失);`durable` 侧权威序与 gap envelope 语义不受影响。

## Migration Plan

1. 契约与组件先行:`Claims` 扩展 + `hecate_governance.managed`(facts/config/profile)+ 单测——纯增量,不动行为。
2. 平台:enrollment `lease_policy` 列与 API、`issue_lease_for` 携带策略版本、pull/accept 指纹比对 + managed channel 测试。
3. Runner:`LeaseGate` 时钟与锚、授权序列插入审批绑定查询、`ManagedLeaseProfile` 装配、指纹计算与上报。
4. 平台事件:唯一约束迁移(含修复步骤)+ `emit`/`append_resequenced` 冲突收敛 + 并发/迟到测试。
5. 验证:`ruff`/`mypy`/scoped pytest(hecate-governance、hecate-durable、hecate-runner、test_execution、scenarios)+ G1/step6 回归;SC05 扩展用例;支持矩阵登记未验证项(I-Cb 其余组成、PostgreSQL 进程矩阵条件跳过)。

## Open Questions

- 无:I-Cb 其余组成、旁路封闭与 step16 认证范围已在 proposal"不含"清单显式登记,不影响本设计组件边界与任务分解。
