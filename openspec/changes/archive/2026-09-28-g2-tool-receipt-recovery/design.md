# Design

## Context

ToolWorker 已有:确定性 `execution_id`(`uuid5(session_id, tool_call_id)`,tool_worker.py:393)、执行前落盘的 `TOOL_CALL` 事件(payload 含完整 arguments)、三态 `TOOL_RESULT` 回执(succeeded/failed/unknown)、`should_auto_retry` 判定矩阵、异常分类兜底(`_is_indeterminate_error`)。缺口:恢复判定只消费 `TOOL_RESULT`(`get_tool_receipt`,tool_worker.py:81),`TOOL_CALL` 被无视;事件存储读取失败返回 None 与"无回执"混淆;成功恢复返回占位文本;回执不含参数摘要;"查询→领取→执行"横跨多个 await,无原子性。

可复用的既有原语:`EventStore.acquire_event_lock`(eventstore.py:221,会话级事件写锁;`PostgresEventStore` 以 `SELECT ... FOR UPDATE` 实现,`InMemoryEventStore` 未覆写、默认 no-op)。EventStore 契约测试在 `tests/test_runtime/contracts/test_event_store_contract.py`,覆盖全部实现。

方案依据:`enterprise-agent-platform-evolution-plan.md` §二 G2 行、§八验收矩阵前四行;`platform-evolution-baseline.md` G2 门槛记录(责任 change 已登记为本文 change 名)。

## Goals / Non-Goals

**Goals:**

- 恢复判定基于记录在案的状态(四态),不基于"记录缺失"推断
- 并发领取原子:同一动作键至多一个执行者首次领取
- 参数摘要冲突显式拒绝;成功恢复回填真实结果
- 写操作在一切不确定状态下安全停止(fail-closed),readonly 保持可用

**Non-Goals:**

- 不建平台 Action 持久表、不受控于本 change 的跨后端回执关联(step6/7 持久化 Action 闭环)
- 不做受 ACL 保护的回执结果引用存储;真实结果恢复仅指通道历史回填
- 不改 Temporal 分发、不改 `execution_id` 的构成规则(仅在其 payload 旁增加摘要)
- 不处理审批等待中的锁持有问题(见 Decisions D2 的边界)

## Decisions

**D1 — TOOL_CALL 升格为领取记录,不新增事件类型。**
领取状态就是"TOOL_CALL 已落盘且无 TOOL_RESULT"。备选:新增 `TOOL_CLAIMED` 事件类型——被否,因为 `TOOL_CALL` 本就先于执行落盘、payload 已含 arguments 与 execution_id,再立一种事件只会产生两种需保持一致的领取表达;直接消费 TOOL_CALL 让"从没开始/已领取"在现有日志上即可区分,零 schema 变更。

**D2 — 原子领取 = 恢复查询 + TOOL_CALL 落盘收进 `acquire_event_lock` 锁区;授权检查留在锁外。**
锁区只做"查询四态 + (never_started 时)落盘 TOOL_CALL",执行在锁外。授权/审批/pre-hook 检查在锁前完成,避免把人工审批等待(`approval_callback`)关进 30 秒 TTL 的会话锁。竞态余量:两个执行者都通过检查后串行进锁,后到者看到 `claimed` 按类别处置——写操作停止、readonly/idempotent 重跑,方向安全;代价是极端并发下可能出现重复审批请求,接受(单会话并发重复派发本身罕见,且不产生未授权副作用)。`InMemoryEventStore` 需补 asyncio 锁实现:基类默认 no-op 只保证单次 append 不互踩,不保证 check-then-act 序列;这是本 change 少数的 store 侧代码变更。

**D3 — 四态判定函数取代裸回执查询。**
新增恢复状态解析(如 `resolve_tool_execution_state(store, session_id, execution_id)`),返回 `never_started / claimed / outcome_unknown / succeeded / failed / store_unavailable` 及关联 payload(claim 与 receipt 的 arguments_digest、result_digest);`store_unavailable` 由读取异常显式产生,不再吞成 None。`get_tool_receipt` 收敛为内部实现细节或删除,调用面只有 tool_worker 与契约测试,无外部消费者(已 grep 确认)。状态命名与方案 G2 行一致。

**D4 — 参数摘要记录在事件上,而不是揉进 execution_id。**
若摘要进 key,改参数即成"新动作"直接执行,恰绕过冲突检测;记录在案 + 同 key 比对才能实现"参数变化冲突拒绝"。canonical JSON:`json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` 的 SHA-256;arguments 解析失败(JSONDecodeError→{})时摘要按空参计算并与落盘 payload 一致,冲突检查自然失配拒绝。TOOL_CALL 与 TOOL_RESULT 均记录,二者由同一次执行的同一 arguments 生成、必然一致。

**D5 — `failed` 矩阵收紧:仅 readonly/idempotent_write 自动重跑。**
现矩阵对 `non_idempotent_write + failed` 放行,依据"异常类型可判定未生效";但工具实现完全可能副作用生效后才抛异常,方案 G2 明确禁止凭异常类型作此推断。收紧后 `failed` 对非幂等/外部/unknown 类进人工核对。代价:确证失败(如参数校验拒绝)的非幂等调用也需人工放行——保守方向的代价,与方案"只能在明确幂等保证或已对账后重试"一致。`test_retry_decision_matrix` 中 `NON_IDEMPOTENT_WRITE+FAILED→True` 的断言翻案为 False。

**D6 — `store_unavailable` 对写操作 fail-closed(含 idempotent_write)。**
存储不可用时无法落领取、无法留回执,此时执行写操作会制造新的"无回执副作用",正是本 change 要消灭的形态。readonly 放行(无副作用,损失仅是日志)。备选"维持 idempotent 可重跑"被否:幂等保证依赖"同一目标状态可重放",而目标系统状态在无法记录期间可能已被他人改变,放行收益(少一次人工核对)不抵语义破坏。

**D7 — 成功恢复回填通道历史真实结果;不可得时显式待对账,不对 succeeded 重跑。**
通道历史(messages 中同 `tool_call_id` 的 role=tool 消息)是当前唯一记录结果内容的位置;回执只有 digest。备选"succeeded 但内容丢失时对 idempotent 类重跑一次取回真实结果"被否:违反既有 spec 的"succeeded SHALL NOT 重新执行"不变式,且在 step6/7 引入受 ACL 保护的回执结果引用后此窗口自然闭合。待对账标记附 `result_digest`,人工核对/对账时可据以验证。

## Risks / Trade-offs

- [审批/授权检查在锁外,两执行者可能重复请求审批] → 接受:无未授权副作用,后到者最终停止;风险记录于 D2,step6 平台命令记录落地后自然消解
- [会话级锁为整段会话的事件写串行化,锁区含一次 get_events 全量扫描] → 恢复查询随会话事件数线性增长;step1 会话规模可控;若成为瓶颈,后续为 get_tool_receipt 引入按 execution_id 的索引查询(不改变语义)
- [failed 收紧使确证失败的非幂等调用需人工放行,运维介入增多] → 方向即"宁停勿重";方案 G2 关闭证据以此为验收项
- [TOOL_RESULT 与通道写入非同一事务,checkpoint 提交前崩溃仍会丢结果内容] → 本 change 只保证"不伪造、不重跑";真实结果持久引用留给 step6/7,待对账标记附 digest 已可核验
- [digest 对非 dict 结果的 arguments 规范化边界] → arguments 在 worker 入口已统一 json 解析为 dict;解析失败路径按空参摘要并与冲突拒绝语义一致,测试覆盖

## Migration Plan

纯行为收紧 + 新增判定,无数据迁移、无 schema 变更。事件日志向后兼容:旧 TOOL_CALL/TOOL_RESULT 无 `arguments_digest` 字段时,恢复查询按"摘要未知"处理——claimed/succeeded 态照常处置,仅跳过冲突比对(不因缺字段拒绝历史回放)。回滚即还原代码,事件日志无残留状态。灰度无需开关:受影响路径为恢复/重放分支,新入口默认走新语义,行为变化即修复本身。

## Open Questions

(无——探索期三个悬问已按方案 G2 原文裁决:claimed+readonly 放行(D3/D5 类别矩阵)、succeeded 结果丢失不重跑(D7)、failed 收紧(D5);如对 D6/D7 的保守度有异议,在 review 时提出,不影响 spec 结构。)
