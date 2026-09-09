# Design: 1.3.21② Time-Travel Resume + update_state

## Context

1.3.19(log-as-truth)后,事件日志是执行态唯一事实源:checkpoint 是可弃缓存,`fold_session` 用与执行期相同的 `ChannelBehavior.write` 在任意版本重建状态。①(声明式中断)交付了 INTERRUPT 描述符的日志推导恢复——未闭合 `INTERRUPT` 事件的 payload 决定续跑节点集。现状缺口:恢复路径只折叠到**尾部**(最新态续跑),没有执行路径消费"折叠到任意历史版本";没有状态修改入口。生产环境 `SessionStateMaterializer` 无 checkpoint 历史(`list_checkpoints` 恒空、`load(checkpoint_id)` 伪造回显 ID),`docs/concepts/sessions.md` 记载的 `GET /checkpoints` 端点不存在(文档漂移)。

2026-09-09 行业调研(23 系统)的关键结论为本设计提供先例与反例:纯日志家族(Temporal event-id reset、dsh seed/end-seed、Pi entry 树、DBOS forkWorkflow)全部以**日志位置**为锚;所有 fork 系统(11 家)均为"新线程 + 原始不可变 + lineage 元数据",无人以破坏性回退为主机制;可审计修改(LangGraph `update_state`→`source="update"` 新 checkpoint、Temporal Update handler、dsh 全量替换事件)殊途同归于**追加记录**;deer-flow 因 LangGraph 同线程多分支 checkpoint 被迫引入 lineage-first 解析(按时间序无法区分兄弟分支)——Hecate 的"每会话日志保持线性"从根上规避该类问题。

## Goals / Non-Goals

Goals(specs 已定义行为,此处仅列设计层):

- 引擎获得三个正交原语:FORK 快照事件的 fold 分支、续跑节点集的日志推导器、`resume_from` 的 tail-only 守卫。
- 服务层获得两个编排:fork(建子会话 + 落 FORK + 可选 updates + 续跑)、update_state(门控 + 追加修改批次)。
- 子会话日志自包含:不依赖读取父日志即可重建状态、过投影等价与 LogInvariants。

Non-Goals(proposal 已列,补充设计层边界):

- 不引入 checkpoint 历史存储、不做日志分支标签、不做 `as_node`、不做副作用回滚/工作区快照。
- FORK 载荷不做大小压缩(完整快照,压缩策略见 Open Questions)。
- 不做深度图版本绑定(冻结锚点时的图定义),仅记录引用。

## Decisions

### D1: 锚点 = log_version 对齐提交点,非 checkpoint_id

提交点(`STEP_END`/`INTERRUPT`/新增 `FORK`)从日志派生;`at_version` 非提交点时向下对齐(复用 replay `inspect_at_version` 的 fallback 语义)。checkpoint 缓存仅可选作折叠加速起点(缓存 log_version ≤ 目标版本时,从缓存态折叠增量)。

**备选与否决**:建 checkpoint 历史存储以满足 roadmap 原文 `CheckpointStore.load(checkpoint_id)`——与 ADR-030"缓存可弃"直接矛盾,且为 1.3.19 刚降级的东西重新建表;纯日志家族(Temporal/dsh/Pi/DBOS)无一以快照 ID 为锚。此为对 roadmap 措辞的刻意偏离,已在 proposal 声明。

### D2: fork = 新子会话,fork-and-run 语义

`POST /fork` 创建子会话并**立即**从续跑节点集继续执行(`updates` 先落盘)。不提供"fork 后停在锚点"的暂停形态——HITL re-plan 的暂停-修改-恢复走**同会话** update_state + 既有 resume 端点,不需要 fork。这砍掉了一整类状态(child 停在非中断的中间态需要新状态机、resume 门扩展)。

**备选与否决**:同会话 rewind(破坏审计事件,6.26 E5 明确要求"不影响原会话");branch-tagged 单日志(所有日志消费者——replay UI、invariants、GC、backup——都要分支感知;deer-flow 的 lineage 伤疤即此代价,Hecate 每会话线性日志天然免疫)。

### D3: 子会话引导 = 单条 FORK 快照事件(日志内快照)

子会话日志首事件 `FORK`,payload 含 `parent_session_id`、`parent_log_version`、`channel_state`(fold(parent[:V]) 的投影,剔除 `_` 前缀与 `sys.` 前缀临时通道)、`next_nodes`、`superstep`、`agent_id`、`log_schema_version`。fold 遇 FORK 做整体水合(`ChannelManager.restore` 语义),其后 CHANNEL_WRITE 增量应用。lineage 同时写入子会话 `SessionModel.metadata`(支持 `GET /sessions?parent_session_id=` 过滤,免日志扫描)。

**备选与否决**:
- *A1 逐字拷贝父前缀事件*:自包含且子回放可逐步单步调试,但 N 个 what-if fork × O(prefix) 存储,且子回放单步父历史价值低(父会话 replay 一直在)。dsh 的 seed 事件 + `{inherited:true}` 标记证明可行,但在 Hecate 的多租户服务端场景下存储与拷贝成本不划算。
- *A2 lineage 指针(child 只记 parent+version,折叠时跨会话读)*:O(1) 事件,但 EventStore 需要跨会话读接口,GC/backup/restore(`ops/backup/restore.py`)、invariants 全部变成两跳 join,父会话删除策略与子会话生命周期耦合。为存储优化引入面状复杂度,不值。
- *用物化 checkpoint 缓存引导*:不可靠——缓存可弃,丢失后子会话无法自重建,违反 log-as-truth 恢复不变式。

A3 的先例组合:LangGraph 同线程 fork = "在祖先上挂新 checkpoint"(快照即引导),dsh 的 seed/end-seedy 边界标记,deer-flow 从选中轮次 checkpoint 分支。FORK 事件在日志**内**,快照即事实源的一部分,不违反"无日志外缓存"。

### D4: update_state = 追加记录式修改批次,STEP_END 收尾

修改批次结构:`TURN_START(reason="update_state")` → `CHANNEL_WRITE×N`(payload 带 `channel`/`value`/`log_schema_version`/`source="update_state"`/`actor`)→ `STEP_END(source="update_state", superstep=<沿日志尾推导的当前值>)` → `TURN_END`。

- **STEP_END 收尾是正确性要求而非风格**:撕裂尾部规则规定"CHANNEL_WRITE 后无提交点 = 恢复时丢弃"。没有收尾,修改会被下一次恢复无声回滚。
- **TURN 对包裹**:满足既有 TURN 配对不变式(异常路径故意不发 TURN_END 的语义不受影响——修改批次自己配对);同时使"执行中"门控的日志推导干净(未闭合 TURN = 执行中)。
- **reducer 语义**:值经 `ChannelManager.write` 应用,累加通道追加、覆盖通道替换——与 LangGraph `update_state` 走 channel reducers 一致。LogPolicy 排除的临时通道(`_resume_value` 等)拒绝作为修改目标(不可落日志的通道改了也活不过恢复)。
- **审计**:事件即审计(payload 含 actor/source),与 LangGraph `metadata.source=="update"` 过滤同构,无需旁路审计表。

**门控(日志推导,无进程注册表)**:存在未闭合 TURN 且其后无 ERROR → 409。ERROR 逃逸覆盖"崩溃会话"(引擎错误路径先发 ERROR 再抛出)。残余盲区:硬杀进程(无 ERROR 无 TURN_END)会话将一直 409,直到有崩溃清扫机制介入——记为 Open Question,不阻塞 ②。

### D5: 续跑节点集推导器,置于 runtime/replay/

新模块 `runtime/replay/continuation.py`(与 logfold 同层,纯"日志→计划"函数,不依赖引擎实例):输入事件流(至目标版本)+ 编译图,输出节点集。规则:最后一个提交点为未闭合 `INTERRUPT` → ① phase-aware 逻辑;`FORK` → payload `next_nodes`;`STEP_END` → 自上一提交点以来的 `NODE_END` 集 → 出边并集(条件边读恢复态 `_route`);无提交点 → 图入口。推导结果在 fork 时**算一次写入 FORK payload**(child 恢复读 payload,不重算——与 ① "描述符由日志落盘"同构)。

**分层**:引擎持原语(fold 分支、推导器、守卫),`studio/workflows` 持编排(建会话、落事件、拉起执行),API 层持契约。引擎不感知 HTTP/SessionModel。

### D6: execute(resume_from=V) tail-only 守卫

引擎执行入口新增 `resume_from`:V == 当前尾部版本时折叠至 V 并按 D5 续跑(崩溃恢复 + fork 编排都走此路径:服务层落完 FORK(+updates)后以 `resume_from=尾部` 拉起子会话执行);V < 尾部时抛错并指引用 fork。这把"双时间线不可能"编码为引擎不变式,而非服务层约定。

### D7: 副作用重执行——如实声明,不做补偿

fold 只重建通道态;重派节点会再次执行工具。what-if 场景这是特性(改状态→看不同结果),但必须在 API 契约层显式(response 含声明字段 + OpenAPI description)。不做工具调用去重(与 LangGraph 同立场);Temporal 的"已完成 activity 不重放"依赖其命令-事件架构,对"改状态重跑"场景反而是错误语义(要重跑才有 what-if)。

### D8: 版本引用记录,深度绑定挂 1.3.20

FORK payload 记 `agent_id`(执行时用当前图定义,lineage 可追溯)。锚点图版本与执行图版本不一致时的行为(按当前图执行 next_nodes,节点缺失则报错)如实文档化。冻结历史图执行属 8.20 Phase 2 版本绑定范畴。

### D11(实现期发现): initial_input 写入必须入日志

实现 ② 时放宽 fold 的 marker 检查(仅对状态携带事件 CHANNEL_WRITE/EVICTION/FORK 检查,簿记事件跳过)后,投影等价校验首次在真实引擎日志上真正运行,暴露 1.3.19 的潜在缺陷:**冷启动 initial_input 的通道写入不经 WAL**(直接 `channel_manager.write`,日志无对应 CHANNEL_WRITE)。后果:fold(log) 丢失全部用户输入——resume 等价校验失败(此前被 NonReplayablePrefix 早退掩盖,所有投影等价测试空转通过),② 的 fork 快照会丢失用户消息。修复:initial_input 中通过 LogPolicy 的通道以 CHANNEL_WRITE 批量落日志,并以 `STEP_END(source="initial_input")` 作为提交点收尾(崩溃恢复回退到该锚点 = 该 turn 从 entry 重启,语义正确;推导器对 executed 为空的 STEP_END 锚点回退 entry,行为一致)。受影响测试基线(事件计数)同步更新。

## Risks / Trade-offs

- [FORK 载荷随会话历史增长(messages 通道无界)] → ② 接受全量快照;EventStore 无载荷大小约束(实测仅有 JSONB/TOAST 物理上限),超大会话的 fork 事件较重但功能可用——以监控观测真实分布,压缩策略见 Open Questions。不为 ② 加拒绝式大小门(长会话恰是最需要 what-if 的场景,拒绝比变重更糟)。
- [fork 读取父日志时父会话正在执行,尾部在移动] → 锚点是不可变前缀,读 `[:V]` 无竞态;`at_version > 读时尾部` → 422。同一父的并发 fork 互不影响(各自建子会话)。
- [旧版本节点读到 FORK 事件(滚动部署窗口)] → PostgresEventStore 未知枚举回落 CUSTOM,fold 对其中立——读侧安全降级;FORK 语义(可 fork 的会话)仅在升级后可见。部署顺序无约束。
- [LogInvariants 对新事件类型/FORK 形状的未知反应] → 任务含对 FORK 事件流跑全量 invariants 的用例;注册表按事件类型筛选,预期中立,以测试证伪。
- [硬杀进程会话永久 409 update_state] → 记 Open Questions(崩溃清扫与 TURN 悬挂治理),ERROR 逃逸已覆盖引擎内错误。
- [同会话大量 update_state 稀释"执行历史"信噪比(replay UI)] → 修改批次自带 `source` 标记,replay UI 可过滤/折叠渲染(8.20 消费侧改进,不阻塞)。

## Migration Plan

无数据库迁移(FORK 是事件载荷,无新表;lineage 走既有 `SessionModel.metadata` JSON)。部署:正常发布即可;枚举新增对旧读端安全(回落 CUSTOM)。回滚:镜像回滚后,已产生的 FORK 事件被旧代码读为 CUSTOM(fold 中立),子会话日志仍完整、状态可重建,仅新端点消失——无数据损伤。文档同步:`docs/concepts/sessions.md` 的 checkpoints curl 示例替换为 `GET /commit-points`;`docs/how-to/replay-debug-guide.md` 增补 fork 用法。

## Open Questions

1. FORK 载荷压缩策略:超阈值时对 messages 等大通道做 eviction 感知裁剪(dsh cropped projection)还是拆存储(LangGraph blobs 表)——待真实载荷分布出现后再定,不影响契约。
2. 悬挂 TURN(硬杀进程)的清扫策略与 update_state 门控的联动——需要崩溃检测/liveness 机制配合,独立于 ②。
3. commit-points 列表的 UI 富化(消息预览、token 消耗)——8.20 消费侧。
4. fork 配额/速率限制(同父会话最大分支数)——ops 策略,平台层统一做。
