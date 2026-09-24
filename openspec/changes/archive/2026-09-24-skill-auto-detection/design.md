# Design

## Context

渐进披露地基已在位：`SkillLoader`（`src/hecate/tools/skill/loader.py`）双级服务（L1 catalog / L2 `load_skill`）、`model_invocable` 治理、provider 优先级归并（`resolve_precedence_map`）、plugin 启用门控、`SkillUsageEventModel` 遥测。当前候选池仅有两个来源：`agent.skills`（显式绑定）与 workspace `auto_load`；`load_skill_content` 的 advertised 检查把 L2 严格限制在绑定集合内。调用点两处（`runtime/agent_execution_port.py`、`studio/workflows/execution_service.py`），均以 `SkillLoader(self._db)` 构造。

## Goals / Non-Goals

**Goals:**

- 拆掉绑定墙：符合条件的 workspace/bundled skill 无需关联即可被模型发现与按需加载。
- 三层可治理开关，全局默认关闭，升级零行为变化。
- 超预算时的确定性选择策略，同一数据下 catalog 稳定。
- 遥测来源标记（`bound` / `auto_detected`），为后续 promote 闭环与审计供数。

**Non-Goals:**

- `search_skills` 长尾词法检索工具（defer，接口方向在 spec 叙事中预留）。
- description 触发质量 lint / 评估线束；skill 上下文成本与使用率观测；suggest-binding 闭环。
- `user` provider 的成员级隔离（独立安全 feature，本 change 仅做池排除 + 语义文档化）。
- 激活作用域变更（维持 run-scoped）；5.9e 运行时依赖求解器（继续不存在）。

## Decisions

### D1: 检测机制 = 纯 LLM catalog 驱动，不引入检索

发现池元数据直接进 L1 catalog，模型自主决定是否 `load_skill`。**备选**：embedding top-k——为触发判定引入每 run 的 embedding 与向量库依赖；而 skill 的量级（数十至数百）下 catalog 直出即可覆盖，长尾场景未来以 `search_skills` 词法检索工具（模型自调、确定性、零基建）承接。选 catalog 直出的决定性理由：零新增热路径依赖，行为确定性可测，规模问题由 D4 的预算与排序兜底。

### D2: 三层开关的存储位

- 全局：`settings.SKILL_DISCOVERY_ENABLED`（默认 `false`），镜像 `SKILL_PROGRESSIVE_DISCLOSURE` 的 rollout-escape 模式。
- Workspace：`WorkspaceModel.settings` JSON 内新键（如 `skill_discovery.enabled` / `skill_discovery.min_trust_tier`），免迁移。
- Agent：`agents.skill_discovery_enabled` 新列（`Boolean, nullable=True`；`None`=跟随 workspace，`true`/`false`=显式覆盖，但显式开启仅在 workspace 已开启时生效——层级只能收窄不能放大）。

**备选**：独立 workspace policy 表（为两个布尔/枚举键建表过重）；agent 级并入现有 JSON 列（agent Pydantic schema `extra="forbid"`，加显式字段更清晰且可校验）。

### D3: `provider='user'` skill 排除在发现池外

数据层无 owner/created_by 列，`user` provider 的"成员个人"语义在 workspace 内实际共享（skills API 与 loader 均仅按 workspace_id 过滤，已验证）。发现会把这个语义矛盾放大成默认行为，故 v1 排除；显式绑定与 `auto_load` 不受影响（绑定是有人显式选择的动作）。**备选**：a) 加 owner 列 + 过滤——正确的长期解但属独立安全 feature，不搭车；b) 仅文档化共享语义、池照收——语义矛盾直接暴露给所有 agent，否决。

### D4: 预算与选择策略

`DEFAULT_CATALOG_TOKEN_BUDGET` 1000 → 2000（按单条目 50–100 tokens 量级可容 40+ 条，覆盖绝大多数 workspace；条目格式与 `DESCRIPTION_CHAR_BUDGET=400` 不动，待真实用量数据再调）。溢出排序：绑定/auto_load 恒优先 → trust tier → provider rank → 使用次数降序 → 名称字典序。使用次数取 `SkillUsageEventModel` 按 `(workspace_id, skill_name)` 的 `COUNT` GROUP BY（单条聚合查询，事件表按既有索引可承受；`catalog_served` 不计入，避免自我强化——只统计真实加载事件 `skill_loaded`）。若后续观测到聚合延迟，降级路径是砍掉 usage 维度（tier → rank → name 已确定），无需改 spec。

### D5: Discovery 状态在 loader 内自解析，不改调用方签名

`SkillLoader` 构造签名保持 `(db, *, progressive, ref_manifest)`；discovery 上下文（agent 三态值 + workspace 策略）在 `format_skills` / `load_skill_content` 内部惰性解析一次并缓存。理由：两个调用点（runtime port、studio execution service）零改动，`load_skill` 的 tool 路径（`tool_worker` → builtin → loader）同样零改动；`_load_agent` 本来就查询 agent 行，顺带读列；workspace settings 一条额外查询。**备选**：调用方解析后传入——把策略知识泄漏到两个 domain 的调用点，违背 loader 自包含。

### D6: 遥测与依赖告警的事件形态

`skill_usage_events.detected_via` 新列（`String(20), nullable=True`；旧行 NULL 按 `bound` 解释，读写路径做 NULL 容错）。依赖告警复用同表，`event_type='dependency_warning'`，`metadata`（既有 JSON 列——若事件表无该列则并入 `skill_name` 旁的 detail 字段，实现时按 `SkillUsageEventModel` 实际列定）携带缺失依赖名列表。判定时机：L2 加载成功后对 `requires` 做一次存在性检查（名称在 workspace/bundled 可解析即满足；不做闭包递归——绑定契约属 5.9e，这里只是告警）。

### D7: Promote 端点落在既有 association 端点旁

`src/hecate/studio/api/agents.py` 新增 `POST /agents/{agent_id}/skills/promote`（与既有 `POST /agents/{agent_id}/skills` 同文件同 router）。与既有端点的差异：做解析校验（存在、未删、`model_invocable`、plugin 启用），失败 404 不动 agent；成功响应附 `frozen: false` + `next_step`（提示走 1.3.20 版本 commit 冻结进 ref_manifest）。**备选**：不加新端点、复用盲目 append——promote 的语义（只允许把可发现且可调用的 skill 转为绑定）会失守，且 studio 后续 UI 闭环需要这个受控入口。路由冲突检查：既有参数路由仅 `DELETE /skills/{skill_name}`，`POST /skills/promote` 无冲突。

### D8: 活面叙事落文档

`docs/`（skill 概念页）与 `docs/features/feature-catalog.md` 5.9c 行在 archive 阶段更新。绑定 = 冻结面 / 发现 = 活面的框架写入概念文档。

## Risks / Trade-offs

- [发现池放大 description 注入面（跨 agent 传播载体）] → 三层开关默认关；workspace trust 下限可配；`model_invocable=false` 走 hide-not-block；5.13a 扫描为文档标注的后续门禁依赖；`user` provider 池排除收窄一档。
- [catalog 膨胀挤占 persona 上下文] → 硬预算 2000 tokens + 确定性选择策略；绑定/auto_load 恒优先保证存量行为不回退。
- [usage 聚合查询延迟] → 单条索引聚合，仅 discovery 生效路径执行；降级路径已定义（D4）。
- [同名遮蔽交互] → 发现池与绑定路径共用 `resolve_precedence_map`，同名行归并后至多一条入池；bundled 被任意 workspace 同名行遮蔽，规则与既有语义一致。
- [升级后行为不变性] → 全局默认 false 短路所有新查询路径（lazy：关闭时连 workspace settings 查询都不发）；回滚 = 关 settings，无需迁移回滚。
- [studio 与 runtime 两条执行路径行为漂移] → 逻辑全部内聚在 loader（D5），两调用点零改动即同构生效。

## Migration Plan

1. 单个 alembic 迁移：`agents.skill_discovery_enabled`（nullable boolean）+ `skill_usage_events.detected_via`（nullable string）。
2. 部署后全局默认关闭——行为与本 change 之前逐字节一致。
3. 灰度：目标 workspace 的 `settings.skill_discovery.enabled=true` 验证 catalog 内容、预算日志、遥测标记。
4. 回滚：`SKILL_DISCOVERY_ENABLED=false`（或删 workspace 键）即时生效；两列保留无害。

## Open Questions

无——早前讨论的开放问题（默认开关、trust 下限、开关粒度、预算数字、search_skills 取舍、promote 形态、`user` provider 处置）均已在本设计定案；defer 项（检索工具、lint、观测、闭环、成员隔离）见 Non-Goals。
