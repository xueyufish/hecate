# Proposal

## Why

step6 交付了持久任务、动作租约、等待唤醒与命令链,但派发边界仍缺治理判定:平台侧 `PolicyContext` 仅携带 `workspace_id`(无 principal、委派链、预算字段),审批硬件分裂为聊天路径 `FailingClosedApprovalCallback` 与 Runner `wait_token` 两套且互不衔接,配额只做事后计量、无预留与结算,G1/G2 复核均点名"step7 统一 Action 应用服务"为收口前置。演进方案将本步列为独立产品闭环(I-Ca)的关键路径:没有本地身份、动作授权、审批、凭据与预算闭环,内置执行与独立宿主不能执行受保护动作,车商 App 场景(本地批准写入、重启恢复)也无法通过。

## What Changes

按演进方案"本地与受管分别验收"的约定,本 change 交付 step7 的**本地治理切片**(I-Ca 路径),受管 profile 完整认证另立 change:

- 新增**统一受控动作应用服务**(可独立依赖的应用组件):平台 builtin、聊天执行、工具 Gateway 与独立宿主 Runner 的每次工具调用(含 readonly)都经同一服务包装为受控 Action——固定 `invocation_id`(逻辑调用级身份,跨 attempt/重启保持,同 ID 异参数/异主体判冲突)、参数摘要、目标资源、副作用分类、授权结果、审批引用与执行回执;REST/MCP/A2A 入口与内置 Worker 禁止自行构造无上下文 executor。
- 身份上下文补齐:复用已加固认证入口,在服务端验证后的上下文中补 `actor / agent_principal / on_behalf_of / delegation_id`;子委派权限取父授权、团队/组织策略、目标资源策略与任务范围的交集,默认拒绝。
- 受控动作边界做工具参数及输出 schema 校验:schema 不匹配不发送到业务目标;业务失败、技术故障、状态未知与策略拒绝分别记录;仅对显式声明并授权的补偿操作记录 `compensates_action_id`,不按工具名自动生成逆操作。
- 审批闭环:审批绑定主体、参数与资源、部署/数据域及任务范围,支持期限与一次性消费(消费原子绑定动作身份,消费后-领取前崩溃可凭持久记录重领);审批后参数变化、权限撤销或策略硬拒绝时重新判定;并发审批只允许一个合法状态转换;唤醒 `wait_token` 不等于审批权限。独立宿主拥有本地审批权威(本地登记审批主体签发、本地 durable 存储持久化消费/撤销状态,不依赖平台审批服务)。
- 本地 policy profile:独立宿主允许业务 App 的身份/权限 adapter 提供可信上下文与本地策略,不强制安装平台 IAM、组织目录或审批 UI;无平台 tenant 时使用客户配置的不可混淆部署/数据域标识;本地撤销(如移除 Agent 授权)对新工具调用即时生效。
- 凭据治理:adapter 配置只存 secret 引用;凭据代理优先(on-behalf-of 上下文随调用传递给业务 API),确需直发时使用短期、限定作用域令牌;事件与 trace 不存明文。
- 预算闭环(最小 G4):为可计量调用设置预算预留和结算,按 `invocation_id` 幂等记账;预留为同步原子判定(并发超额只放行预算内数量,未派发释放、未知用量保留);远程未知用量标记 `estimated/unverified`,未知不记零。
- 安全底线与启用门槛:已知撤销/硬拒绝、策略要求审批而缺少有效审批绑定、身份缺失、证据不可写 → 停止新受保护动作,该底线在审计/强制/回退模式均不可关闭,也不扩大审批范围(策略放行的无审批动作不误拒);依赖强制路径保证的受保护写动作须旁路封闭验证通过才可启用,运维留痕不构成启用依据,显式豁免仅归入较低治理等级的实验范围(只覆盖安全不依赖 Hecate 旁路封闭、由业务 API 独立强制授权的动作,不计入认证)——完整旁路封闭验证仍归后续切片。

**本切片不含**(step7 内后续 change 承接,不在此静默丢弃):受管授权租约的完整 policy profile 认证(签发者/受众/策略版本/防重放全量校验、断连最大陈旧窗口、真实时钟偏移、重连重验证,随 I-Cb);平台 EventStore 迟到追加 fencing 缺口的修复;Sandbox adapter 包装、出口策略绑定与旁路攻击演练;MCP/A2A 入站协议专项验证扩展(已有 G1 加固路径保持);供应商账单对账与完整 reported/estimated/reconciled 拆分(step10)。**本 change 归档只代表 step7 本地治理切片完成,不代表 step7 整体完成**;演进方案中 step7 的状态更新以其剩余切片的交付为准。

## Capabilities

### New Capabilities

- `execution-action-governance`: 统一受控动作应用服务——principal 信封、invocation 标识、策略判定绑定、schema 校验、错误分类、决策与执行回执,所有执行入口共用同一语义与测试;委派交集与默认拒绝。
- `execution-approval-binding`: 审批与参数/资源/期限/一次性消费的绑定语义,重新判定条件,并发单合法转换,唤醒 token 与审批权限的区分,聊天审批回调与持久等待的桥接。
- `execution-credential-proxying`: adapter 配置的 secret 引用规则、代理优先的凭据注入、短期限定作用域直发凭据、业务 API 的可核验服务身份/on-be-half-of 上下文、明文禁入事件与 trace。
- `execution-usage-accounting`: 按 `invocation_id` 的幂等预算预留/结算,未知用量 `estimated/unverified` 标记,未知不记零。

### Modified Capabilities

- `execution-security`: ToolWorker 审批流从"回调即放行"升级为经 `execution-approval-binding` 的绑定判定;SESSION/PROJECT 作用域缓存不得绕过参数变更后的重新判定;`PolicyContext` 增补 principal/委派/预算字段(以纯数据传入,`tool_access.py` 保持零外部依赖)。
- `standalone-runner-host`: Runner 派发边界接入统一受控动作服务与本地 policy profile;本地业务身份/权限 adapter、不可混淆部署/数据域标识、本地撤销即时生效;受管租约门语义(已交付)不回退。

## Impact

- 代码:`enterprise/`(身份信封、策略协调)、`tools/gateway/` 与 `tools/policy/`(上下文增补、执行点接入)、`models/approval.py`(审批绑定字段)、`ops/`(用量记账)、`core/composition/`(平台装配)、`packages/hecate-runner`(宿主装配本地 adapter)、`packages/hecate-durable`(contracts 引用,不改存储语义);`runtime/tool_access.py` 保持零外部依赖。
- 兼容:既有 G1 回归(MCP 变更动作、匿名模式拒绝)必须保持;step6 执行不变量(幂等键、fencing、动作台账、等待/待对账不自动重跑)不被削弱;无 `access_policy` 配置时的既有 ToolWorker 行为按 `execution-security` 既有需求处理,迁移期先以审计模式对比新旧判定、再开强制。
- 依赖:不新增第三方运行时依赖;判定引擎复用现有策略流水线,稳定后才允许外部替换。
- 验收基线:平台与独立宿主执行同一审批规则;旧批准不能用于新参数;委派权限不能扩大;撤销 Agent 后新工具调用被拒绝;重复执行请求不重复记账、不盲目重做外部动作(对应演进方案 step7 验收与 SC"本地批准写入与重启"、"审批等待期间尝试派发工具"、"审批后修改参数或撤销身份")。
