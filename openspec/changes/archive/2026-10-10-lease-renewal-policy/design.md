# Design

## Context

受管租约链路现状(全部已存在,本 change 不改 HTTP 契约与租约格式):

- 平台每次 `/managed/host/pull` 响应携带新签发租约(`issue_lease_for`,新 nonce,TTL 默认 120 s,scope 来自准入的 managed_scope);签发是无状态的,平台不记录已发 nonce。
- 宿主 `LeaseGate`(packages/hecate-runner `managed.py`)持有当前租约;`check()` 验证签名/受众/部署绑定/期限并消费 nonce(内存 set);`update()` 目前**不做任何验证**直接替换 `_current`。
- 引擎 `_lease_refusal`(engine.py):受保护动作派发前检查租约;`CredentialError`(无租约/过期/nonce 已消费)时有界等待(硬编码 5 s,50 ms 轮询)等新租约;范围越界立即拒绝。拒绝 → `dispatch_denied` → observer STOP_UNKNOWN → Run 以 failed 收敛,停止后续工具,零业务副作用。
- `is_decided_action` 让已决动作回放跳过闸门(managed-command-acceptance 已交付,保持)。
- 缺口:nonce 消费仅内存,重启即失忆;等待预算是源码常量,未进 profile;6b 缺少进程级验收证据。

## Goals / Non-Goals

**Goals:**

1. 续租策略成为显式声明:等待预算从源码常量提升为宿主 profile 注入的参数,默认值不变(5 s),文档同步。
2. 重放窗口收口:`update()` 安装前验证(签名/期限/部署绑定/身份),拒绝结构无效与过期租约;nonce 消费持久化到宿主本地状态目录,跨重启保持。
3. 进程级验收:SC05 同族 wheel + 真实平台 HTTP 套件覆盖多写续租、停发拒绝、过期拒绝、nonce 重放(含跨重启回注)、已决回放跳过闸门。

**Non-Goals:**

- 工具级动作范围、合法审批判定、撤权流程(step7)。
- 平台侧事件日志追加的 fencing(平台 EventStore 与租约的接线归 step6f 矩阵/step7 撤权窗口)。
- 租约格式、签发语义、pull 协议变更(平台侧零修改)。
- 平台 durable worker 的执行租约(fencing token 体系)——本 change 只涉及受管 HMAC 授权租约。

## Decisions

1. **nonce 持久化位置**:宿主本地状态目录(与 evidence/状态同目录)追加一个 append-only 的已消费 nonce 记录(JSONL,带时间戳),`LeaseGate` 初始化时载入。取舍:不引入数据库依赖、重启后 O(1) 追加;文件按已消费 nonce 数量有界(记录 nonce 前 8 字节摘要 + 过期时间,过期条目可在载入时丢弃,文件仅保留未过期窗口)。**替代方案被拒**:靠"平台只发新租约"保证——重启窗口内的本地回注重放恰好绕过它;靠 TTL——TTL 内回注仍可武装。
2. **update() 安装前验证**:复用 `verify_lease` + 身份检查;验证失败不替换当前租约(保守:坏输入不能清掉好租约)。nonce 已消费的新租约拒绝安装(同 nonce 视为重放)。已安装租约在 check() 时过期属正常生命周期,不影响 update。
3. **等待预算参数化**:`ManagedRunnerProfile`(或装配入口)新增 `lease_refresh_wait_seconds`(默认 5.0,与现值一致);引擎从 profile 取值,不再读模块常量。预算语义写入 README 的受管运行章节(断连窗口声明)。
4. **验收套件**:复用 `tests/scenarios/tools/managed_platform.py` 平台栈与 wheel 安装路径,新增 `tests/scenarios/test_sc06_lease_renewal_policy.py`(命名随 SC 编号实际空位调整);平台侧以可控制的 pull 节奏/停发模拟失联;nonce 重放用例直接构造已消费租约回注(跨重启用例经真实子进程重启)。业务调用计数沿用 SC05 的计数桩。场景清单(manifest)追加 slices,场景整体保持 planned——6b 技术闭环 ≠ SC 场景认证完成,step7 依赖行保留。

## Risks / Trade-offs

- nonce 记录文件成为宿主本地新状态:丢失(删状态目录)即回退到内存语义——与现状一致,不引入更强的错误失败模式;文档注明备份要求与 evidence 同级。
- update() 收紧后,平台签发异常(时钟漂移导致租约"未生效")会拒装:verify_lease 的期限校验含 not-before 语义,漂移窗口与现有 pull 行为一致,不为它放宽。
- 等待预算可配置后,过长预算会拖住派发线程:profile 文档给出上限建议(≤ 一个 pull 周期 × 小倍数),不做硬上限(策略参数属于部署方)。
