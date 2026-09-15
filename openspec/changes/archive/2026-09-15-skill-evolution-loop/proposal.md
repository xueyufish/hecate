## Why

1.3.6a–e 的自学习骨架代码（`studio/evolution/`、`runtime/self_improvement/`）是零调用方的孤儿代码：无真实数据源、无持久化、无验证门禁，闭环从未真实存在。业界对标调研（AgentCore Optimization、Salesforce、Manus、Claude Code/Warp、openjiuwen 等）收敛出明确共识：学习产物应为 skill 包形态、frozen-weight（不动模型）、经验证 + 人审后才持久化、产物可 diff 可回滚。平台已具备全部相邻积木（EventStore 轨迹、quality-scoring 信号、评估体系、SKILL.md 解析/存储/注入、MetaAgentScheduler），只缺把它们串成闭环的这一层。对应 roadmap 特性 1.3.6f。

## What Changes

- 新增 **自进化闭环管线**：会话完成事件 + 质量分信号触发轨迹采集 → LLM 失败归因（AgentRx 分类法）→ 生成候选 skill（知识型 SKILL.md 内容包，含 guardrails 分区，structured delta 更新，去重合并）→ eval-gate 验证 → 人审发布 → 注入生效 → 质量分回流度量效果。全链路事件驱动采集 + 调度式综合（MetaAgentScheduler 注册 evolution agent）。
- **修改 skill-loader**：从"绑定 skill 全量注入 system prompt"升级为两级渐进式披露（L1 名称+描述常驻上下文，L2 正文触发时加载）。
- **废弃孤儿骨架代码**：删除 `src/hecate/studio/evolution/`（trajectory_analyzer、policy_evolver、integration、environment_generator）与 `src/hecate/runtime/self_improvement/` 的 constraint_generator、constraint_injector；failure_analyzer 的 AgentRx 失败分类法作为概念在新归因模块中重建。无调用方，非 breaking。
- 新增候选 skill 数据模型（候选、版本、lineage 来源轨迹、评估记录）与 studio 审核发布 API；候选生成后接入既有 content-scanning/DLP 管线（防轨迹污染注入）。
- learned skill 第一期**只允许知识型**（纯 SKILL.md 文本 + 资源引用，禁止可执行脚本），作用域 per-workspace，发布走人工审批（全部候选，无自动晋升）。
- roadmap 上 1.3.6a–e 的完成状态改述延后至本 change 归档阶段处理（`docs/features/roadmap.md`、`feature-catalog.md`）。

## Capabilities

### New Capabilities

- `skill-evolution-pipeline`: 自进化闭环的编排与触发——学习信号源（质量分低于阈值、用户显式纠正）、事件驱动轨迹采集、调度式综合运行（evolution run 生命周期：pending → analyzing → candidates_ready → gated → published/concluded）、run lineage 与归因调用成本预算、skill 使用观测与质量分回流度量。
- `skill-attribution`: 失败归因与候选 skill 生成——LLM 归因（规则启发式仅做预过滤）、AgentRx 失败分类、候选 skill 结构（procedure 与 guardrails 分区、来源轨迹 evidence 引用、delta 格式）、同主题候选聚类合并、候选内容安全扫描。
- `skill-evolution-gate`: 候选 skill 验证门禁与发布——四件套验证（关联数据集回归、golden subset 不退化、触发测试、with/without baseline 对比）、人工审核（批准/驳回/修改后批准）、发布至 skill registry（workspace 隔离、provenance 元数据）、驳回与生效后回滚路径。

### Modified Capabilities

- `skill-loader`: 注入行为从"全部绑定 skill 的完整指令注入 system prompt"改为两级加载——L1（名称+描述）常驻系统上下文，L2（SKILL.md 正文与资源）在模型判定相关并显式请求时加载；保留既有 token 预算约束并扩展至两级。

## Impact

- **代码**：新增 `studio/self_evolution/`（管线编排、归因、候选生成、门禁编排）；修改 `runtime/` skill 注入路径与 `models/skill.py`（候选/lineage 模型）；`core/composition/wiring.py` 注册 evolution meta-agent；废弃 `studio/evolution/` 全部与 `runtime/self_improvement/` 两个 constraint 模块。
- **API**：studio 新增候选 skill 列表/审核/发布/lineage 查询端点。
- **依赖系统（只读消费）**：EventStore/会话事件、quality-scoring、评估框架（数据集与任务）、content-scanning/DLP。
- **依赖注入（新增注册）**：MetaAgentScheduler 增加 evolution agent。
- **无新外部依赖**：LLM 调用走平台既有模型路由。
