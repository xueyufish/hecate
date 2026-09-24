# Design

## Context

本变更生长在已建成的记忆底座上:L1–L4 分层(`memory_blocks` / `memories` / `knowledge_memories` / recall),4.5 consolidation 引擎(plan-then-apply、revision 守卫、`memory_edit_log` 审计、`consolidation_runs` 水位、advisory lock 多实例互斥),trigger bus 已有 cron / idle / 压力标记 / reflection 四种触发来源,L2 压缩是 ADR-033 surface replacement(原始事件不因压缩删除),4.14/4.15 fusion 排序(余弦主导 × 有界衰减 × 重要度,锚点 `last_confirmed_at`)。

关键既有事实,本设计的地基:

- 压缩只改模型所见投影,`events` 表有独立保留期清理(与压缩无关);
- consolidation 水位天然覆盖"水位之上的一切内容"——任何未提取窗口都会被巡扫最终覆盖,登记不是覆盖的唯一入口;
- `memory_edit_log` + `consolidation_runs` 审计数据层已完备,治理面只缺 surface;
- 全部记忆旋钮目前是平台环境变量,agent 模型无任何记忆配置字段。

## Goals / Non-Goals

**Goals:**

- 压缩边界的重要内容以异步交接方式进入整合管线,不新增热路径 LLM 调用;
- 记忆产物获得有界生命周期:TTL、容量、淘汰、晋升,全部软删可恢复;
- 记忆治理旋钮从环境变量上升到 workspace/agent 粒度策略对象;
- 审计数据与管理面(REST + Studio UI)落地,使治理可操作。

**Non-Goals:**

同步静默 flush 回合;记忆 CDC 流;L3→Qdrant 迁移;人工审批门(write_approval);`event_count` 触发;物理删除;`event_retention` 迁入策略对象(维持部署级配置,策略对象只在约束上引用它)。

## Decisions

### D1. flush 是压缩边界的异步交接,不是同步静默回合

同步静默回合需要在上下文已满、用户等待的时刻插入一次阻塞的 LLM 调用,而其失败处理(不阻塞也不回滚压缩)本质上仍会放弃强保证——收益与代价不成比例。ADR-033 保证压缩不删原始事件,水位巡扫最终覆盖一切未提取窗口,"轻量登记 + 异步提取 + 原始留档兜底"与同步回合在实际效果上等价,而热路径只增加一次单行写。本设计在压缩确定将删窗口时做一次**轻量同步登记**(单行写),提取完全异步。

**替代方案**:同步静默回合(否,见上);不做事前登记、仅靠巡扫(否,失去"内容最新鲜时提取"的质量窗口与优先级语义)。

### D2. flush 的投递语义:best-effort 登记 + at-least-once 提取 + 失败只告警

正确性锚点是 ADR-033(压缩不删原始事件)+ 水位巡扫的最终一致覆盖。flush 因此是**优化而非正确性关键**:登记失败 → 压缩照常,窗口仍在水位之上,巡扫兜底;提取失败 → 重试耗尽后告警。唯一的真丢失场景("反复失败 + 事件撑过整个保留期被 prune")以 spec 硬约束排除:事件保留期 ≫ 提取管线延迟 + 重试周期,并以告警保证可见性。这要求 `pre_compaction` 钩子位于 compaction 的 surface-replacement 决策点(`runtime/compaction.py`),将删窗口边界映射到会话所属整合单元 `(workspace_id, agent_id, actor_id | null, team_id | null)` 后登记。

**替代方案**:强一致(压缩前必须提取成功,否——热路径阻塞,且失败时仍须放行压缩,保证名存实亡);仅告警不登记(否,见 D1)。

### D3. 策略对象用独立 `memory_policies` 表,不用 agent config JSON

策略需要独立 CRUD、校验与审计,塞进 agent config JSON 会让校验逻辑寄生在 agent 生命周期里;独立资源形态也让"workspace 级策略作用于空间内全部 agent"的语义自然成立。表设计:workspace 级(workspace_id 唯一)与 agent 级(workspace_id + agent_id 唯一)两类行,策略字段为结构化列(工具子集、共享上限)与 JSON(参数组)组合。解析为独立 service,带进程内缓存 + 写路径失效(即时生效语义),空表退化为平台默认值,因此**策略不设独立 flag**——空表即 no-op,与 byte-identical 兼容。

**替代方案**:agent config JSON 列(否,见上);仅 workspace 级(否,per-agent 工具面是 4.16 的明确诉求,如对客服 agent 禁用 `memory_forget`)。

### D4. 生效链收敛规则分两类字段

权限面(工具子集、共享上限)单调收窄——agent 级只能是父级子集,这是"必须收敛于"语义,防止 workspace 策略被下级扩权;数值面(TTL、容量、预算、阈值)可自由配置但受平台硬上限钳制。比"任意覆盖"安全,比"只能整体继承"灵活。

**替代方案**:任意覆盖(否,权限面可被越权扩权);只能整体继承(否,per-agent 数值调优无意义地被禁)。

### D5. 淘汰评分复用 fusion 信号族,不建第二套评分

淘汰与检索排序服务同一批信号(时间衰减 × 重要度 + 访问热度),是同一信号族的两个消费方。淘汰评分直接取 fusion 规范化分数(不含查询相关性的部分),淘汰低分者;阈值与预算来自生效策略。避免两套评分漂移,也使"为什么淘汰它"可用既有分信号分解解释。代码级复用形态已定:查询无关信号(确认新鲜度 × log 压缩访问热度)在 4.15 中已实现为整合产出的 value score,淘汰评分直接复用该计算并叠加 `decay_mult × importance_mult` 乘子——即共享同一信号计算函数,fusion 取其与相关性的乘积,淘汰取其查询无关投影;本变更同时把 value score 从 4.15 的"仅观测"边界扩展为淘汰输入(规格由 `memory-lifecycle` capability 定义)。

**替代方案**:独立的 LRU/LFU 淘汰(否,与语义排序脱节,不可解释)。

### D6. 生命周期操作 = `archived_at` 软删标记 + `memory_edit_log` 生命周期来源

`memories` / `knowledge_memories` 增加 `archived_at`(nullable);全部检索路径(service 层 scope 过滤处)统一排除非空行——过滤收敛在 service 层而非散在调用方。archive/恢复/淘汰/晋升记 `memory_edit_log`,来源标识与 agent 工具、consolidation 并列。与 SUPERSEDE 软删谱系兼容(archive 不破坏 `superseded_by` 链)。

### D7. 清扫挂在 trigger bus,独立于"待处理内容"判定

TTL/容量/晋升不依赖新转录,因此清扫是 bus 上的一种定期 pass(独立调度周期),与整合 run 共享 advisory lock 互斥与预算控制。受 `MEMORY_LIFECYCLE_ENABLED` 控制,默认关闭。`pre_compaction` 受 `MEMORY_FLUSH_ENABLED` 控制,默认关闭。两个 flag + 空策略表 = 合入前 byte-identical。

### D8. 治理 REST 落在 `studio/api`,同 change 落地 `memory-api` 既有承诺

记忆治理是管理面(Studio 域),非会话数据面(channel 域);路由组挂 `studio/api`,复用 `get_auth_context()` 依赖与 workspace 隔离语义。路由与分页遵循 studio 既有约定(`agents.py` / `conversations.py` / `sessions.py` 同款):模块内 `APIRouter()` + 相对路径声明,在 `main.py` 以 `include_router(..., prefix="/api")` 挂载;列表端点分页沿用 offset/limit 风格。**既有记忆管理端点不重复实现**:`hecate_memory.api.memory` 模块已覆盖 memory-api spec 的全部既有承诺(L1 blocks、L3、L4、压缩状态),本变更只在 `studio/api` 新增治理端点组;既有端点的 archive 过滤经共享 service 层自动生效。旧 spec 的 `REQ-n` 标题格式保持不动(delta 只 ADDED,sync 后允许新旧格式并存,先例:`memory-block-management`)。

### D9. UI 只读浏览 + archive,编辑走 agent 工具路径

只读浏览、搜索、archive、恢复、审计、策略表单;不提供内容直接编辑——agent 工具写路径已带 revision 守卫与审计,UI 直接编辑会形成绕过守卫的第二写入口。

### D10. Memory Center 是 workspace 级一级入口

L3 用户事实跨 agent(actor 级)、recall 挂在会话、策略与审计作用于整个 workspace——不是任何单个 agent 的子域。因此挂载为 `web/src/app/(dashboard)/` 下与 `knowledge`、`ops-center` 并列的一级路由(`/memory`),而非 agent 详情子页;agent 维度的记忆视图后续如需,从 agent 详情页链接跳转即可。

## Risks / Trade-offs

- [登记写位于压缩热路径] → 单行轻量 insert + 失败即返回(best-effort),不重试不阻塞;可观测性靠告警指标。
- [淘汰误删有价值记忆] → 软删可恢复 + 保护窗口(近期确认不淘汰)+ 单轮预算 + 评分可解释(复用 fusion 分信号分解);最坏情况是"降级为未记忆",非数据损坏。
- [策略解析开销进 seeding/调度路径] → 进程内缓存 + 写失效;seeding 是启动期、调度是低频路径,缓存命中率天然高。
- [检索路径遗漏 archive 过滤] → 过滤收敛在 service 层单点(spec 强制全部检索路径排除),以路径清单测试覆盖(工具/fusion/prefetch/REST 四入口)。
- [旧 `memory-api` spec 从未实现,可能存在签名过时] → 实现时以现状对齐(端点路径/语义按 spec,参数细节允许随实现修正并在 delta 中不动旧 REQ);若发现 spec 与现实根本冲突,回到变更流程修订。
- [TTL 锚点 `last_confirmed_at` 在存量行上可能为空] → 迁移回填(取 `updated_at` 兜底),与 4.14 回填先例同法。

## Migration Plan

1. Alembic migration:`memory_policies` 表、`memories` / `knowledge_memories` 增加 `archived_at`、`memory_edit_log` 来源枚举扩展、TTL 锚点回填;
2. 全部新能力 flag 默认关闭、策略表为空 → 合入后平台行为 byte-identical;
3. 灰度顺序:先开 `MEMORY_FLUSH_ENABLED`(观测登记/提取指标与告警)→ 再开 `MEMORY_LIFECYCLE_ENABLED`(TTL 先于容量淘汰)→ 最后按 workspace 推策略对象;
4. 回滚:关闭 flag + 清空策略表即回到基线,软删数据不受影响。
