## Context

平台已有的相邻积木（本设计只消费、不重建）：EventStore 轨迹与 evidence 快照（`WorkflowExecutionService` 落库）、conversation-quality-scoring（LLM-as-judge 逐轮评分、会话完成异步触发）、评估框架（命名数据集版本、评估任务、发布门禁、known-bad 豁免）、SKILL.md 基础设施（`SkillModel`、`tools/skill/parser.py`、skill-api 导入、skill-loader XML 注入）、MetaAgentScheduler（已在 `core/composition/wiring.py` 注册 gc 与 compliance 两个 agent）、content-scanning/DLP 管线。

分层约束：`runtime/` 自足性不变量禁止依赖 `studio/`/`ops/`；闭环编排需要读会话、质量分、评估数据集（全部在上层），因此编排器放 `studio/`，runtime 只保留"按目录广告 + 按需加载"的注入机制。

业界对标结论（2026-09 调研，详见探索记录）：学习产物收敛为 skill 包形态（Manus / Anthropic / openjiuwen / DeerFlow / hermes-agent）；frozen-weight；产物必须 structured delta、经验证 + 人审后持久化；AWS AgentCore Optimization 的"trace → 建议 → 版本化 bundle → A/B → promote"是最完整的工程蓝本。

## Goals / Non-Goals

**Goals:**

- 一条**端到端走通**的闭环：真实轨迹 → 归因 → 候选 skill → 四项验证 → 人审 → 发布 → 注入 → 效果回流。每个环节 v1 可以简陋，但链路必须闭合。
- 学习产物与人写 skill 走同一 registry / 同一绑定模型 / 同一 loader，不建平行体系。
- 全程 lineage 可审计：轨迹 → 归因 → 验证 → 人审 → 发布 → 使用统计。

**Non-Goals:**

- 工具描述/优先级优化的第二通道（后置，参考 AgentCore tool-description recommendation）。
- 合成环境（1.3.6d 遗留 `environment_generator.py` 随骨架一并废弃，方向单独再议）。
- Team/官方 skill 市场分层（v1 仅 per-workspace）。
- 自动晋升门禁（v1 全人审）；prompt 全文优化（GEPA 型）；从轨迹自动合成新测试用例。
- 评估器自校准 / drift 监控（记录为后续方向，v1 不做）。

## Decisions

- **D1 · 学习产物 = 知识型 skill 包，取代约束注入路线。** 业界收敛信号一致；约束往 system prompt 累积正是 ACE 论文的 context collapse 死穴。失败教训作为相关技能 guardrails 分区沉淀（"约束随能力绑定"，DeerFlow `allowed-tools` 同型）。备选（否决）：继续约束/规则路线——与业界方向相悖且上下文预算不可控。
- **D2 · 归因与候选生成用 LLM，规则只做预过滤。** GEPA/ACE/Manus 均为 LLM 反思形态；规则启发式（旧骨架的 ±0.1、正则解析）无法从自然语言轨迹归纳可复用知识。归因调用走平台模型路由（复用 llm-routing 与 circuit breaker），模型可配置。AgentRx 十类分类法保留为归因 taxonomy（旧 `failure_analyzer.py` 的概念重建）。
- **D3 · 触发模型 = 事件驱动采集 + 调度式综合。** 采集挂在会话完成事件后（对齐 quality-scoring 的 `asyncio.create_task` 模式），综合归因批量跑在 MetaAgentScheduler 上（Warp 双 skill 范式：improver 不占业务路径）。备选（否决）：纯 inline 学习——业务路径延迟与成本不可控。
- **D4 · 验证门禁四件套。** 数据集回归复用评估框架既有任务/数据集版本；golden subset 从该 agent 历史轨迹固化（首版可直接取轨迹回放断言，不追求评测题形态）；触发测试验证 L1→L2 加载行为；with/without baseline 用评估框架跑两遍对比。备选（否决）：只跑单一数据集——无法发现触发失灵与技能无关化。
- **D5 · v1 全人审 + 知识型 only + 内容扫描三重防线。** 自动产物默认不可信（Salesforce 白皮书："未经外部环境验证不应成为持久 policy"）；learned skill 禁脚本消除代码注入面；候选过 content-scanning/DLP 防轨迹投毒（恶意用户制造失败轨迹向学习产物注入内容——企业多租户平台特有的威胁模型）。
- **D6 · skill-loader 升级为两级渐进式披露。** L1 目录（name+description）常驻、L2 正文按需加载；`auto_load=True` 保留"全量注入"旧语义作为兼容逃生门。learned skill 数量增长下不升级必然重演 context collapse。这是对 `skill-loader` spec 的行为级修改，需要同步更新其消费方（chat graph / agent_execute 的 system prompt 组装）。
- **D7 · 模块落位与数据模型。** 编排器、归因、候选生成、门禁编排放 `studio/self_evolution/`；新增表：`evolution_run`（run 状态机与 lineage）、`skill_candidate`（候选、版本、delta、evidence 引用、验证报告、审核状态）与学习输入队列表；published learned skill 即普通 `SkillModel` 行 + provenance 字段（source=learned、来源 run ID），不新建第二套 skill 实体。runtime 不感知学习，只通过 loader 的两级机制消费。
- **D8 · 效果回流为观测而非控制。** v1 只统计触发/L2 加载次数与使用会话质量分布对比，不据此自动调整 skill；防止反馈回路自我强化（reward hacking 防线的最简形态）。
- **D9 · `load_skill` 承载为平台内置工具。** 复用 `tools/tool/builtin.py` 的既有机制：JSON Schema 定义进 `BUILTIN_TOOL_DEFINITIONS`、`seed_builtin_tools()` 落库、function calling 暴露、`BuiltInToolExecutor` 分发。工具结果即 tool message，天然满足 spec 的 run-scoped context；tool-decision-log / tool-execution-analytics 免费提供加载埋点；tool-permission-control 统一管控。备选（否决）：context 注入指令——需在 runtime loop 自行解析标记、绕过决策日志与权限管控，与 MCP-first 方向相悖。
- **D10 · 触发测试用小模型模拟加载决策。** with/without baseline（任务 5.4）已覆盖带候选 skill 的真实端到端行为，触发测试的独立职责是验证 description 质量：judge 模型看 L1 目录 + 轨迹上下文输出 would-load 判定，确定性高、成本低、走平台模型路由。备选（否决）：轨迹回放断言——L1 目录变更后轨迹分叉，属反事实推断，每个候选需 N 次完整 agent 重跑且 LLM 非确定性使断言抖动；降级为人工抽查手段。

## Risks / Trade-offs

- [LLM 归因质量不稳定，产出泛化无用的候选] → 验证门禁四件套兜底（尤其触发测试与 baseline 对比）；候选默认挂起而非自动发布；后续可加双 judge。
- [轨迹投毒向学习产物注入恶意内容] → content-scanning/DLP 扫描 + 知识型 only + 人审三道闸；lineage 可追溯到具体轨迹便于事后审计。
- [两级加载改变现有 agent 的 prompt 形态，可能影响既有会话质量] → `auto_load=True` 保留全量注入逃生门；发布前对存量绑定 agent 跑评估回归；skill-api 侧提供按 agent 灰度开启两级加载的配置。
- [golden subset 质量依赖历史轨迹数量，冷启动 agent 验证不充分] → insufficient_data 挂起态明确暴露，不静默放行；文档提示先积累轨迹或人工补充数据集。
- [归因 LLM 成本随失败会话量线性增长] → 规则预过滤削减 + run 级 token/次数预算 + 阈值与间隔可配置。
- [废弃骨架代码涉及 1.3.6a–e 已标 ✅ 的特性] → roadmap/catalog 改述延后到归档阶段统一处理（含 positioning.md 检查），本 change 不动 roadmap。

## Migration Plan

1. 新表迁移（`evolution_run`、`skill_candidate`、学习输入队列）+ `SkillModel` provenance 字段——纯增量。
2. skill-loader 两级加载上线：默认行为对 `auto_load=True` 与无技能 agent 不变；对普通绑定 skill 的形态变化通过配置开关灰度（先测试 workspace，后全量），出现质量回退可切回全量注入。
3. 闭环管线按任务序上线：采集 → 归因 → 门禁 → 审核 API → 调度注册；每步独立可回滚（scheduler 不注册则管线不运转，无副作用）。
4. 孤儿骨架删除放在闭环验证走通之后（同一 change 内最后一批任务），删除无调用方代码不影响运行时。
5. 整体回滚：关闭 evolution 配置开关（不注册 scheduler、采集挂钩短路），存量 published learned skill 走 unpublish。
