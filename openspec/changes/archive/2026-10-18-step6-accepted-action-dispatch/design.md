# Design

## Context

`AcceptedDecisionStore` 拥有已接受原记录，`ToolCallExecutor` 拥有单次工具治理链，`SqlActionLedgerHook` 拥有一个 Run 的持久动作关联。普通 executor 允许无账本的兼容调用；这不构成新组件可恢复派发的保证。本切片增加强制受限的新入口，保留旧入口行为。

## Goals / Non-Goals

只派发或恢复一个指定的已接受动作，真实记录执行与停止事实。没有新模型调用或 Task 终态迁移；不做完整 Controller、批次推进或平台入口装配。

## Decisions

### D1: 直接读取已接受事实

`AcceptedActionStep(store, executor)` 不接受可选 ownership 绕过开关。dispatch 先校验 Run/step/action 标识，再 lookup；不调用 DecisionStep 的 miss 分支。记录经 detached snapshot、schema、Run/步骤身份、摘要和 accepted_at 核验。无记录或无动作是 AcceptedActionLookupError；损坏、未知版本及未提交记录是显式存储故障。工具名/参数不接受额外覆盖。

### D2: 有界、无歧义的稳定映射

- `session_id = uuid5(NAMESPACE_URL, "hecate:accepted-run:" + canonical_json(run_ref.to_dict()))`
- `tool_call_id = uuid5(NAMESPACE_URL, "hecate:accepted-action:" + canonical_json({"run": run_ref.to_dict(), "step": decision_step_id, "action": action_key}))`
- `execution_id = stable_execution_id(session_id, tool_call_id)`

三者均为稳定 UUID 字符串，适合既有 EventStore 的 UUID session 字段。action_key 仅在单个已接受步骤内唯一，所以决策步骤必须进入映射；不得假定 provider 或自定义 responder 全局唯一。分隔符只是被 JSON 编码的数据，不会混淆发行域和 Run。派发覆盖输入上下文中的保留身份字段，成功和停止结果都返回 execution_id；原始动作键仍在决策事实中，可确定性关联，无额外映射真源。此映射固定，不在本片为旧 Run 批量迁移身份。

### D3: 同一实际账本验证 ownership

`ToolCallExecutor.execute_accepted_call` 必须有 DecisionOwnership 和 ActionLedgerHook。既有 hook 增加非 abstract 的 `validate_accepted_dispatch` 默认拒绝实现，新具名消费者为 AcceptedActionStep；旧 hook/旧入口不受影响。SQL hook 在线程中复用既有 LeaseManager 权威校验，并核对：

- hook Run 与请求 Run 完全一致、Task 确为该 Run 的 owning Task；
- hook 使用的真实 lease key/holder/token 与请求 ownership 一致；
- 权威租约的 Task 作用域、holder、精确 token 和数据库期限有效；
- 已有 action intent 的 Task/Run 归属与当前绑定相同。

新入口在恢复读取前先验证；需要新派发时，审批/hook 之后在领取前重新验证，再由 record_intent_ex/claim_ex 本地事务检查实际 lease，业务调用前再验证。没有仅检查一次后允许 readonly 绕过 stale-claim 的路径。每次数据库事务在返回前结束，不跨审批等待或远程工具调用持有事务。不承诺数据库事务能使外部业务达到 exactly-once；执行中失联继续依既有回执/对账边界处理。

### D4: 复用治理链，保持已接受参数

新旧入口共享一个内部执行实现；权限、审批、中间件和 sandbox 装配沿用宿主注入。接受不构成授权，普通未配置策略的测试 port 不代表生产治理认证。已接受路径冻结参数规范 JSON，pre hook 后及业务调用前核对，静默改写被拒绝，若要改动作应走另一个被接受决策。

恢复新路径不从调用方传入的 messages 回填真实结果，避免伪造历史盖过持久结果；真实账本/事件恢复继续使用既有决策表。SQL 领取竞争和 outcome token 仍由现有存储机制负责，不复制一套账本或全局锁。

### D5: 从控制流生成结果状态

`AcceptedToolCallOutcome` 是具体结果数据类，不是新增扩展点。未进入业务调用时状态为 withheld；进入后已知异常且失败回执被确认时为 failed；成功或真实完成回填为 succeeded。不因工具错误文本或 is_error 标志猜测是否调用过业务。

未知结果抛既有 ExecutionStoppedError；新路径回执落盘失败也抛 reconciliation_required，不能返回 succeeded。Step 将其转换为 stopped，保留停止类别与 execution_id。调用后的其他无法确认错误保守进入对账。取消原样传播，已领取事实保留供下次安全恢复。旧 execute_call 仍保留原有 warning/通道回执兼容语义。

### D6: 验证

Runtime doubles 验证身份覆盖/碰撞、不同步骤复用键、原参数、拒绝、未知结果及回执失败。SQL 矩阵使用真实决策存储、Task 关联、租约和动作台账，SQLite 为独有文件，PG 为每例独有 schema，仅清理自建 schema。

独立进程测试：父测试先提交原决策和租约；写进程执行一次并完成 SQL outcome 后 os._exit；另一个新 Python 进程仅使用相同数据库恢复，模型和工具调用均被禁止。外部效果用 fsync 文件计数，仅一行。组件重建用例仅称为组件重建，不冒充跨进程证据。

## Migration / Scope

无 schema 或入口变更；新增派发行为尚未挂接生产路由。旧 hooks 的可选验证默认拒绝仅影响新消费者。没有删除持久事实或重写旧任务身份；业务补偿、跨批次推进及完整治理认证留待所属切片。
