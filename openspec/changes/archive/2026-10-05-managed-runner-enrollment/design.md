# Design

## Context

底座(全部在 main):

- **step4**:`StandaloneEnrollmentModel`(pending 命名引用、`managed_new_runs` 默认 false、CheckConstraint 禁止未准入开启)+ `TaskRunRegistry.register_standalone_enrollment` / `set_managed_opt_in`(活跃管理员、契约版本与能力校验、`enrollment_resolver` 注入点——**无真实实现**)。
- **step6 worker**:`hecate-durable` 双端可用——`SqlDurableStore`(命令 command_id 幂等、事件日志 event_id 去重 + 游标读、`LeaseManager` fencing)、`DurableWorker`/`OutboxRelay`;平台侧 `TaskControlService` + lifespan worker + `event_relay` 投影。
- **step3 契约**:`security-claims.schema.json`(iss/aud/sub/tenant/delegation_ref/exp;bearer + mutualTLS 双 profile;回调独立受众)。
- **runner**:无任何控制面代码(grep 证实);durable profile 有 store/leases/启动恢复循环。
- SC05(重连与重复控制命令)与 SC04(受管断连)均 `planned`,blocked_by 本 change。

## Goals / Non-Goals

**Goals:** 受管宿主闭环技术预览——注册→准入(可信解析)→租约签发/验证→受管投递/接受对账→执行事实投影→断连边界→重连重验;SC05 翻转;step6"受管投递与状态投影"勾选;step7 受管切片的租约/断连/重连三项交付。

**Non-Goals:** 策略引擎与审批语义、凭据代理、预算(G4)、SC04 完整翻转(其断连断言由租约到期语义覆盖大半,翻转归 step7 剩余)、受管生产认证(step16)、受管宿主的多租户密度、A2A/协议层接入。

## Decisions

### D1 通道形态:宿主拉取 + 宿主推送(预览档),平台不出站连接

宿主周期拉取派发意图与命令(`GET /managed/deliveries?cursor=`)、按游标推送执行事实(`POST /managed/events` batch);租约随拉取响应签发。平台不向宿主发起出站连接(NAT/隔离网络下不可达,且平台侧连接宿主需另行凭据边界)。备选"平台推送(webhook)"被否:预览档无部署面保证,轮询+游标与 durable 事件游标语义同构。通道认证:平台为 enrollment 签发部署域短期 bearer 凭据(HMAC 签名,`security-claims` 语义的对称密钥实现;JWT/非对称留待生产档),凭据验证在平台 API 层,自报字段忽略(standalone-runner-host ADDED 钉住)。

### D2 投递与接受记录:两侧各用本地 durable 事务,以 (enrollment, delivery_id) 为幂等键

平台侧:worker 的派发回调在 enrollment 目标上追加 `managed_delivery` 行(outbox 模式,同事务落 envelope)——复用 `SqlDurableStore.events`(source=`platform-managed`),delivery 内容为任务意图 envelope;拉取接口按游标读出。宿主侧:拉取后先写本地接受记录(经 `SqlDurableStore.submit_task` 既有幂等——delivery 的 task_ref/run_ref 落 `durable_submission` 与 `durable_task_state`),再入本地执行队列;响应携带接受的 task_ref/revision,平台据此标记 delivered。响应丢失:宿主重拉同 delivery → `submit_task` 幂等返回原关联;平台超时 → 状态 `undelivered/unknown`,对账接口按 enrollment+delivery_id 查询,不重发新 delivery(同 ID 同内容幂等,SC05 断言一)。命令投递同构,`durable_command` 的 command_id 幂等即防重(断言二)。

### D3 投影方向:宿主事件日志 → 平台(反向 relay),单写方以"平台只追加投影行"实现

宿主把本地 `durable_event_log` 游标之后的 envelope 批量上传;平台 `ManagedProjectionService` 按 event_id 去重写入 `platform_events`(复用 `PlatformEventService.append_resequenced` + `find_by_event_id` 既有幂等),并把终态/状态迁移映射为 Run 投影更新(只更新平台 `runs.projection`,永不调用宿主任何写路径)。与 step6 的 `OutboxRelay` 镜像对称:那边 durable→platform_events(进程内),这边远端 durable→同表(经 HTTP)。上传游标归宿主持有(本地表),平台无须回执确认水位(幂等去重使重传无害)。

### D4 租约:`security-claims` 语义的限时授权,宿主侧验证门

平台在拉取响应中附带新租约(签发者=enrollment 所属 workspace 的受管签发域、受众=该 enrollment 的部署域标识、sub=宿主身份、exp=now+陈旧窗口、nonce);宿主 `managed.py` 的 `LeaseGate` 验证签发者/受众/部署绑定/exp/nonce 未消费,通过才接受 delivery 并允许受保护动作。断连:租约自然过期(exp 之后)→ 拒绝新受保护动作,**不签发本地授权**(门内无本地路径,SC04 断言的宿主侧);重连拉取获得新租约(旧 nonce 拒绝)。最大陈旧窗口 = profile 配置的租约 TTL(显式字段,默认与心跳周期成比例)。防重放:nonce 随租约唯一,宿主侧消费表(本地 durable 表一行一 nonce)。

### D5 重连协议:重验链与"不迁移活跃 Run"

重连 = 宿主重新拉取:平台验证凭据 → enrollment 仍 ADMITTED 且 `managed_new_runs`(撤销则 403 + 停止派发,SC05 断言三)→ 信任根摘要与 enrollment 记录一致(宿主在注册时上报信任根摘要,平台存储;变更即要求管理员重走 `set_managed_opt_in`)→ 期望配置指纹比对(不匹配则拒绝新派发,已执行事实不受影响)→ 签发新租约。宿主侧先补传事件游标再请求新 delivery(顺序保证投影先于新执行)。活跃 Run 不迁移:宿主断连期间的本地执行继续走既有 durable 语义,重连只是恢复通道——两边的 Run 各自持有身份,永不互换 ownership(fencing 已有)。

### D6 可信解析器:workspace 级信任根登记表 + HMAC 凭据闭环

`trust_roots` 平台表(workspace 隔离,命名引用 + 凭据材料摘要 + 状态)。解析器实现:enrollment 的 `host_identity_ref`/`trust_root_ref` 命名引用必须解析到该 workspace 未撤销的登记行,且注册请求携带的凭据证明(注册时用登记材料的 HMAC 挑战应答)匹配——解析结果为 True 才准入。凭据即 D1 的部署域 bearer 材料:注册用挑战应答换取初始凭据,准入后轮换。纯 alpha 档:HMAC 对称密钥,不做 X.509/mTLS(契约 schema 已留 mutualTLS profile,实现随生产档)。

### D7 装配与开关:受管全为叠加,独立模式零感知

runner `RunnerConfig` 增加 `control_plane` 可选段(地址、凭据引用、租约 TTL、拉取/推送间隔);缺失时 `managed.py` 完全不装配(standalone-runner-host MODIFIED 的场景三)。平台侧:enrollment API 挂 workspace 管理路由;宿主通道 API(`/managed/*`)独立 router,凭据依赖注入;`enrollment_resolver` 在 composition 接 `TrustRootResolver`。SC05 场景测试用进程内 HTTP(平台 app + runner 客户端同测试进程,真实通道、 Stub 模型边界),覆盖四断言 + 断连过期 + 撤销。

## Risks / Trade-offs

- [HMAC 对称密钥的凭据强度有限] → 预览档定位明确;契约 schema 与 `LeaseGate` 验证语义按 `security-claims` 落,生产档换非对称实现不动验证面。
- [拉取轮询的延迟] → 预览档可接受(间隔可配);推送/WebSocket 留待需求证据。
- [两侧 durable 语义漂移] → 双端共用 `hecate-durable` 同一实现(非复制),契约套件继续单点钉住。
- [SC04 部分覆盖的表述风险] → proposal/plan 注记明确"租约到期宿主侧已交付,SC04 完整翻转(含在线判定/时钟回拨 profile)归 step7 剩余",不虚报。

## Migration Plan

1. 代码合入(独立模式默认行为零变化);新迁移建 `trust_roots` 与 nonce/凭据所需表(如平台侧需要)。
2. 部署顺序:平台升级 → workspace 登记信任根 → 宿主配置受管段并注册 → 管理员准入并开启 `managed_new_runs` → 宿主开始接受受管派发。
3. 回退:宿主移除 `control_plane` 配置即回独立模式(本地事实保留);平台侧 enrollment 关闭 `managed_new_runs` 停止派发;表保留不删。

## Open Questions

(无——D1/D6 的预览档取舍已定为设计决策;生产档凭据升级是后续 change 的已知项,不影响本规格。)
