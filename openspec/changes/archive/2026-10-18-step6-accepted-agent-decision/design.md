# Design

## Context

本切片实现单个模型决策的接受与恢复，不实现工具派发或完整 Controller。既有 Pregel 日志、Channel、checkpoint 与动作台账的支持范围保留；新增能力不据此自动继承认证。Runtime 只依赖自身契约，SQL 实现位于 `hecate_durable.runtime_hook` 可选桥接层。

## Goals / Non-Goals

- 持久接受完整的可见输出、输入/配置版本引用和有序动作集合；恢复原内容而非重新问模型。
- 决策行与 outbox 事件同事务提交，幂等与冲突、过期 owner 拒绝均有确定性断言。
- 不派发工具，不实现审批、Plan Revision、Child Run、多轮循环、入口切流或真实模型质量认证。

## Decisions

### D1: Runtime 拥有接缝，宿主注入实现

`accepted_decision.py` 定义 `RunRef`、版本化 `AcceptedDecisionRecord/AcceptedAction`、`DecisionOwnership` 及 `AcceptedDecisionStore` ABC（lookup/commit 两个方法）。Runtime 不导入 durable。`DecisionStep` 是具名实际消费者，`DecisionResponder` 注入模型能力；测试用脚本替身，后续宿主可接既有 RuntimePort。SQL/InMemory 实现在 `runtime_hook.py`，不使 core `seams.py` 或 `stub.py` 反向依赖 Runtime。

### D2: 内容与版本固定，快照不能被调用方改写

记录版本为 `schema_version=1`。唯一性为 `(issuer_domain, run_id, decision_step_id)`；完整内容散列覆盖版本、Run/步骤身份、输入/配置引用、可见输出及有序动作集合，排除接受时间等服务端元数据。严格 JSON 拒绝 NaN/Infinity、非字符串键、非 JSON 类型；动作名/键非空且键唯一，参数必须是对象，副作用类别复用 `SideEffectClass`。本切片只接受带动作的决策，空动作的最终完成决策仍待后续切片。

提交前先脱离调用方的嵌套对象，再进入线程/事务；返回、lookup 与 to_dict 均产生分离快照。SQL 读取核验已存内容散列与版本；损坏记录显式存储失败而非 miss。`accepted_output` 是提供方适配器选出的可见输出，不应注入完整 vendor 响应或隐藏推理。动作结果仍由既有 Action Ledger 拥有。

### D3: 权威租约是现有 LeaseManager，不是决策行 token

宿主传入现有 durable worker 的 Task dispatch 租约句柄（lease_key、holder、精确 fencing token）。SQL 从权威 Task/Run 关联解析 owning Task，再用与 worker 共用的 `task_dispatch_lease_key` 核对作用域；保持已部署的 dispatch key 格式，不另起决策/Run 租约或第二个 owner。租约获取、续租与接管仍由现有 worker/LeaseManager 负责。

SQL commit 在同一事务里调用 `LeaseManager.assert_valid` 锁定权威租约行并校验 holder、精确 token 与数据库期限，包括首次接受新步骤、相同内容重投及冲突判定前。无 ownership、任意自造 token、更高伪造 token、其他 Run 的租约、已释放/过期或被接管的句柄全部拒绝。InMemory 测试替身注入同一权威校验能力，不配置时拒写；它不授予跨进程持久保证。读取原记录不等于取得派发权限。

### D4: 同事务提交与 outbox

SQL 从既有 TaskState 的权威 Run 关联推导 Task 引用，未登记或歧义的关联不能接受决策。决策与动作集合存为同一行，写入后在同一事务使用 `store.events.emit` 追加 `agent_decision_accepted` envelope；事件仅带步骤、版本、摘要和动作键，不默认外发输出/参数。事件写入失败使决策也不可见；幂等重投返回原记录且不重复事件。唯一约束竞争在新事务内重新校验 ownership 后仲裁，无平行 journal 或跨库事务。

### D5: 单步流程与失败边界

`DecisionStep` 冻结输入引用并先 lookup。存在原记录时先核对身份、内容散列和输入/配置版本，匹配才返回 `resumed=True`，模型零调用；版本改变显式冲突，不返回旧结果或重问。未命中才请求一个 proposal，校验后提交；仅成功后返回持久原记录。提供方只能改其参数副本，不能改变被冻结的引用。存储不可用/损坏与提交失败均不自动请求第二次模型。

并发两个组件都在提交前调用模型是允许的未接受尝试；至多一个内容被接受，另一候选冲突且不自动重试模型。调用方负责这些尝试的用量记录，不能将未知费用视为零；本切片不新增账单或预算体系。

### D6: 验证与迁移

共同契约套件覆盖 SQLite SQL、InMemory，配置 `DURABLE_TEST_POSTGRES_URL` 时再运行真实 PostgreSQL SQL。新 PG 测试各自创建独有 schema，并只清理自己创建的 schema，不清理共享 public 表。恢复测试使用两个真正独立的 Python 子进程，写者提交后 `os._exit`，读者仅凭 PG 找回原记录且模型零调用。并发套件另覆盖相同/不同候选和组件竞争，跳过不计通过。

新迁移镜像包 metadata，含版本列和部署作用域唯一约束；只 expand。应用回退保留表和决策事实，`downgrade` 明确拒绝破坏性删表。只升级/回退本应用不能擦除已接受的决策。PG 验证该 revision、列/类型/nullable/唯一约束及拒绝破坏性回退；不借此宣称平台全链迁移或生产认证完成。

## Risks / Scope

- Runtime 存储新接缝与提供方新接缝均有实际消费者与替身，符合 runtime-pluggability；新表/持久契约按 spec-driven 管理。
- 没有 HTTP 接入和新增业务权限；Run/版本引用与 lease 均来自宿主可信配置，不能直接透传模型自报授权。
- 非完整 Controller；动作的派发、审批、当前授权、稳定动作身份映射和账本结果仍由后续执行切片核验。
