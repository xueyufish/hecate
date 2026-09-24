# Proposal: memory-lifecycle-governance

## Why

Hecate 的记忆体系已建成 L1–L4 分层、consolidation 引擎、fusion 检索与 8 个记忆工具,但两条腿仍然缺失:其一,**记忆在层间没有生命周期**——没有 flush 机制保证压缩边界处的重要内容进入持久层(4.5 明确 defer 的 v2 项),没有分层 TTL、容量上限与淘汰,记忆只进不出;其二,**治理面不存在**——所有记忆旋钮(工具开关、整合、反思)冻结在平台级环境变量,无 per-agent/per-workspace 策略,L3/L4/审计数据(`memory_edit_log`、`consolidation_runs`)没有任何治理 REST 或 UI surface,记忆只进不出。本变更对应 feature catalog 的 **4.25(rescope:废除"三层"旧框架,收到 flush + 生命周期机制)与 4.16(收尾:lifecycle UX)**。

## What Changes

- **压缩前 flush 交接(`pre_compaction` 触发)**:L2 压缩(surface replacement)确定将删窗口时,同步做一次轻量登记(best-effort,失败仅告警、压缩照常),由 4.5 trigger bus 异步提取(at-least-once,水位幂等);正确性由 ADR-033 原始事件不因压缩删除兜底,spec 硬约束:事件保留期 ≫ 提取管线延迟 + 重试周期。不做同步静默 flush 回合。
- **记忆生命周期机制(TTL / 容量 / 淘汰 / 晋升)**:分层 TTL(事件侧 `event_retention` 与记忆产物侧分层 TTL 是两个独立旋钮)、容量上限、基于既有 fusion 评分族(衰减 × 重要度 + 访问热度)的淘汰,淘汰一律软删 archive(可恢复),L4 永不衰减维持现状;生命周清扫复用 trigger bus 调度与多实例互斥。
- **记忆策略对象(`memory_policies`)**:workspace 级策略 + agent 级覆盖,生效链 platform → workspace → agent 且必须收敛(不得越权扩权);字段涵盖记忆工具面、整合/flush 触发阈值、分层 TTL、namespace 共享级别、检索与整合预算、淘汰阈值。
- **记忆治理 REST API**:新增治理端点组(`memory_edit_log` 查询、`consolidation_runs`/`reflection_runs` 查询、策略 CRUD 与 resolved view、记忆 archive/恢复、生命周期统计、召回检索);既有记忆管理端点(L1 blocks、L3、L4、压缩状态)已存在于 `hecate-memory` 包的 API 模块,本变更经共享 service 层使其自动获得 archive 过滤。
- **Studio 记忆治理 UI(只读 + archive)**:Memory Center——L3/L4/recall 浏览与搜索、审计视图(edit log + consolidation runs)、策略编辑表单;内容修改走既有 agent 工具路径(UI 不做直接编辑)。
- **策略化工具面**:`agent-memory-tools` 的 seeding 在平台 flag 之上叠加策略收敛——resolved policy 只能收窄(如对某 agent 禁用 `memory_forget`),不能扩出平台 flag 之外的工具。

**Non-Goals**:同步静默 flush 回合;记忆 CDC 流;L3→Qdrant 迁移;人工审批门(write_approval);`event_count` 触发模式;物理删除(合规删除沿用既有软删 + 现有机制)。以上均沿用 4.5 的 v2 缓存或明确不做。

全部新能力默认关闭/为空(`MEMORY_FLUSH_ENABLED`、`MEMORY_LIFECYCLE_ENABLED` 默认 `false`;策略表为空时生效链退化为平台环境变量默认值),关闭时平台行为与本变更合入前 byte-identical。

## Capabilities

### New Capabilities

- `memory-lifecycle`:记忆生命周期语义——分层 TTL 过期、容量上限、淘汰评分与软删 archive/恢复、晋升门、生命周清扫调度、生命周期审计,以及"事件保留期 > 补提周期"的正确性约束。
- `memory-policy`:记忆策略对象——`memory_policies` 模型、platform→workspace→agent 生效链与收敛规则、策略字段语义、执行点(工具 seeding、整合触发、flush、TTL/淘汰、namespace 共享)。
- `memory-governance-ui`:Studio 记忆治理界面——Memory Center 浏览/搜索、审计视图(edit log + consolidation runs)、策略表单、archive 操作与权限。

### Modified Capabilities

- `memory-consolidation`:整合触发总线新增 `(e)` `pre_compaction` 触发——压缩边界将删窗口的轻量登记 + 异步提取语义(best-effort 登记、at-least-once 提取、失败只告警),登记窗口复用压力标记优先机制。
- `memory-api`:新增治理端点组(edit log / consolidation runs / 策略 CRUD / archive 与恢复 / 生命周期统计);既有 REQ-1~6 端点语义不变(本变更使其落地)。
- `agent-memory-tools`:记忆工具注册与开关——在平台 flag 之上叠加策略收敛语义(resolved policy 只能收窄工具子集)。

## Impact

- **数据层**:`src/hecate/models/memory.py` 新增 `memory_policies` 表;`memories` / `knowledge_memories` 增加生命周期字段(`archived_at`、TTL 相关);新增 alembic migration。
- **机制层**:`src/hecate/runtime/compaction.py`(压缩边界 flush 登记钩子)、`src/hecate/core/composition/consolidation.py`(trigger bus `(e)` 触发、生命周清扫)、`src/hecate/core/config.py`(新 flag 与分层 TTL 默认值)。
- **API 层**:`src/hecate/studio/api/` 新增记忆治理路由组(治理端点 + 落地 `memory-api` 既有承诺)。
- **工具层**:`src/hecate/tools/tool/registry.py`(seeding 策略收敛)。
- **前端**:`web/` 新增 Memory Center 页面(浏览、审计、策略表单)。
- **兼容性**:无 breaking;全部新路径默认关闭/为空,byte-identical 保证同 4.5/4.16 先例。
