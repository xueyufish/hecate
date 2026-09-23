# Tasks

## 1. 数据库迁移与 schema

- [x] 1.1 创建 `episodes` 表迁移(workspace_id / agent_id / actor_id / session_id / task_type / situation / intent / actions JSONB / outcomes JSONB / closed_at / created_at)+ 复合索引 `(workspace_id, agent_id, actor_id, closed_at)`,验证 alembic upgrade head 成功且 alembic downgrade head 回滚干净
- [x] 1.2 创建 `reflections` 表迁移(reflection_id / workspace_id / agent_id / actor_id / session_id / title / use_cases TEXT[] / hints TEXT / confidence FLOAT / source_episode_ids UUID[] / operator / superseded_by / version / status / isrel / issup / isuse / last_confirmed_at / created_at)+ 索引 `(workspace_id, status, use_cases)`,验证 upgrade head 与 downgrade head
- [x] 1.3 创建 `work_context_nodes` 表迁移(node_id / workspace_id / agent_id / node_type / content / success_rate / usage_count / last_used_at / user_correction_count / source_reliability / linked_reflection_id / active / created_at)+ 索引 `(workspace_id, node_type, active)`,验证 upgrade head 与 downgrade head
- [x] 1.4 创建 `work_context_edges` 表迁移(edge_id / workspace_id / source_node_id / target_node_id / edge_type / weight / created_at)+ 索引 `(workspace_id, source_node_id)` 与 `(workspace_id, target_node_id)`,验证 upgrade head 与 downgrade head
- [x] 1.5 扩展 `memories` 表新增 `team_id` 与 `actor_id` 列(actor_id 默认填 user_id),扩展 `knowledge_memories` 同样两列,验证现有查询行为不变(回填后 actor_id 与既有 user_id 一致)
- [x] 1.6 新增 `reflection_runs` 平行审计表(reflection_run_id / trigger / workspace_id / agent_id / actor_id / team_id / candidate_episode_ids UUID[] / adopted_count / rejected_count / confidence_distribution JSONB / rejection_reasons JSONB / status / created_at)+ 索引 `(workspace_id, agent_id, created_at)`,验证 upgrade head 与 downgrade head
- [x] 1.7 扩展 `consolidation_runs` 表新增 `reflection_type` 可空列,验证既有 consolidation 行为不受影响

## 2. MemoryProvider 契约扩展

- [x] 2.1 扩展 `MemoryProvider` Protocol ABC,新增 tier-4 / tier-5 capability 字段(`capability_tier_4: bool`、`capability_tier_5: bool`)与 `end_episode` / `escalate_failure` 钩子声明(`has_end_episode: bool`、`has_escalate_failure: bool`),验证默认实现 `hecate_memory` 在 `REFLECTION_ENABLED=false` 时 capability_tier_4=False(行为不变)
- [x] 2.2 在 provider capability 路由层(组合根)加入 tier-4 / tier-5 路由分支,验证旧 provider(只声明 tier-1/2/3)的 `memory_search(tier='tier_4')` 返回结构化错误而非崩溃
- [x] 2.3 扩展 `sync_turn` 实现,新增 `episode_record` 子动作:把本轮 TOOL event 异步写入当前 active episode;无 active episode 时跳过,验证既有 `sync_turn` 行为在 `REFLECTION_ENABLED=false` 时 byte-identical

## 3. Episode 写入与 sync_turn 集成

- [x] 3.1 在 4.13 processor chain 新增 `ReflectionRecordProcessor`,注册为 `sync_turn` 后的链节,验证 processor chain 现有 4.17 pressure alert 行为不受影响
- [x] 3.2 实现 `episode_record` 服务层函数,把 TOOL event 与对话交互写入 `episodes.actions` / `episodes.outcomes`,验证单轮多 TOOL 全部入 episode、actor 隔离
- [x] 3.3 实现 `episode_close(episode_id)` 显式调用接口,打 `closed_at` 戳并触发反思入口,验证未调用 `episode_close` 的 episode 不进入反思候选池
- [x] 3.4 在 agent runtime 主路径(workflow 节点完成 / 任务失败)接入 `episode_close` 调用,验证 happy path 与 failure path 都触发 close

## 4. ReflectionEngine 与反思提取

- [x] 4.1 抽出 4.5 `ConsolidationEngine` 的 `ExtractFn / PlanFn / ScanFn / EmbedFn` seam 为独立 abstract 或 protocol,验证 consolidation 既有行为不变(回滚 test)
- [x] 4.2 实现 `ReflectFn`,从一组 closed episodes 提取 typed reflection(产出 JSON schema:`title / use_cases / hints / source_episode_ids / confidence`),验证与既有 LLM 路由 / 配额复用
- [x] 4.3 实现 `ReflectionEngine`,整合 `ExtractFn / PlanFn / ScanFn / EmbedFn` + `ReflectFn`,跑四段流水线(`extract → score → plan → apply`),验证与 `ConsolidationEngine` 共享 audit / 调度 / 互斥
- [x] 4.4 在 `ConsolidationScheduler` 注册 `reflection_trigger` trigger 路径,与 consolidation 共享 advisory lock + 调度优先级,验证同单元反思与整合串行执行
- [x] 4.5 实现反思 LLM 路由分流(走 `llm-routing` 反思专用模型 + `REFLECTION_MAX_TOKENS` 上限),验证主对话 LLM 配额不被打满
- [x] 4.6 实现 `hints` 字段硬切分 ≤ 300 词校验,验证超限反思立即被拒(rejection_reason='hints_too_long')
- [x] 4.7 实现 `source_episode_ids >= 2` 校验,验证长度 = 1 的反思立即被拒(rejection_reason='single_episode_hallucination')
- [x] 4.8 实现 `operator='update'` 合并(同 title 已存在 active reflection 时切换),验证 version 字段累加 + 既有 reflection `superseded_by` 指向新条

## 5. 反思质量四道闸

- [x] 5.1 实现闸 1 模型隔离(reflection fork 只读 episode / knowledge_memory,不可调用写文件 / 网络 / secrets 工具),验证反思 run 调用写文件工具被拒
- [x] 5.2 实现闸 2 LLM-as-Judge,产出 `isrel / issup / isuse` 三个 0–1 分(基于 1.3.5e GroundingScorer + 1.3.6f EvolutionGate 模式),验证三者均 > 0.5 才通过
- [x] 5.3 实现闸 3 `source_episode_ids >= 2` 二次校验,验证闸 4 之前的 rejection 已记录
- [x] 5.4 实现闸 4 confidence 评估任务(bound-judge),`confidence < 0.4` 三次评估自动 deprecated,验证 automation 在 reflection 重启后自恢复
- [x] 5.5 实现 injection / guardrail 检测(在 `ReflectFn` 输出后),验证含 prompt injection 的反思被拒(rejection_reason='injection_detected')

## 6. Work Context Graph 节点与边

- [x] 6.1 实现 `work_context_nodes` 入图事务:approved reflection 触发节点创建(按 `use_cases` 与 `hints` 决定 `node_type`),事务原子性保证 reflection 失败回滚不影响节点
- [x] 6.2 实现 `work_context_edges` 边创建(reflection supersession 时切边 + 节点 `active=false`)
- [x] 6.3 实现后台批 job,从 `episodes` 与 `reflections` 聚合更新节点统计字段(`success_rate` / `usage_count` / `last_used_at`),验证批 job 不阻塞工具调用
- [x] 6.4 把 ADR-024 KM6 设计状态从 Proposed 改为 Accepted,更新 `docs/design/adr/024-knowledge-memory-enhancement.md`

## 7. Memory 工具扩展(`reflection_search` / `work_context_query`)

- [x] 7.1 扩展 `memory_search` 工具签名,新增可选 `tier` 参数(默认 `tier_2`,行为 byte-identical),验证 `tier='tier_4'` / `'tier_5'` 走对应 provider 路由
- [x] 7.2 扩展 `memory_add` 工具签名,新增可选 `scope` 参数(`actor_scoped` 默认 / `workspace_shared`),验证 editor 角色调用 `workspace_shared` 返回结构化错误
- [x] 7.3 实现 `reflection_search(query, task_type?, top_k=5)` 内置工具(受 `REFLECTION_ENABLED` 控制),验证只返回 `status='approved'` 反思
- [x] 7.4 实现 `work_context_query(query, node_type?, top_k=5)` 内置工具(受 `REFLECTION_ENABLED` 控制),验证只返回 `active=true` 节点
- [x] 7.5 把 10 个工具(reflection_search + work_context_query + 既有 8 个)的注册条件挂上对应 flag,验证 `REFLECTION_ENABLED=false` 时 reflection_search / work_context_query 不在 seeding 集合

## 8. Cross-Thread namespace 扩展

- [x] 8.1 实现 `memories` / `knowledge_memories` 表 namespace 维度查询(`workspace_id AND (team_id OR null) AND (actor_id OR null) AND (session_id OR null)`),验证既有 workspace_id 隔离行为不变
- [x] 8.2 实现 `scope` 参数校验:workspace_shared 仅 admin 角色可写,editor 角色收到结构化错误
- [x] 8.3 在 `(workspace_id, team_id, actor_id)` 上加复合索引,验证查询性能不退化(vacuum 后查询计划命中索引)
- [x] 8.4 实现 workspace_id 必传校验,缺失时抛错

## 9. 反思消费侧三路集成

- [x] 9.1 路径 a — 任务开始检索:`reflection_summary` block 注入到 L1(`UPDATE_BLOCK` 允许清单新增此类型),验证消费受 4.13 预算轴协同(token 超限时被裁剪)
- [x] 9.2 路径 b — 失败重试召回:4.13 processor chain 新增 `escalate_failure` 钩子,任务 confidence < 阈值时检索匹配 `use_cases` 的 approved reflection 注入重试上下文,验证召回失败不阻塞重试发起
- [x] 9.3 路径 c — EvolutionGate 评分输入:扩展 `EvolutionGate` 增加 `grounding_regression` 与 `reflection_relevance` 检,反思持续参与 `golden_subset_regression` 与 `trigger_test` 评估
- [x] 9.4 把 `reflection_summary` 加入 `DEFAULT_BLOCK_ALLOWLIST`,验证其他 block(personta 等)仍走原有允许清单

## 10. 默认 flag 与运行时切换

- [x] 10.1 在配置层加 `REFLECTION_ENABLED` flag(默认 `false`),验证默认关闭时平台行为与变更合入前 byte-identical
- [x] 10.2 实现运行时 flag 切换(无需重启),验证 `false` → `true` 后 ReflectionEngine 在下一调度周期启动,工具出现在下一个 seeding 周期
- [x] 10.3 在文档中明确 flag 切换的影响面,验证 opsx 切换文档可读

## 11. 文档与 OpenSpec archive 准备

- [x] 11.1 更新 `docs/design/memory-design.md`,新增 Task Memory / Tool Memory / Cross-Thread 章节,验证文档索引更新
- [x] 11.2 更新 `docs/concepts/memory.md`,扩展 L1–L4 + 反思层 + namespace 概念,验证 sample 示例与代码语义一致
- [x] 11.3 更新 `docs/features/roadmap.md` 把 4.21 / 4.22 / 4.23 / KM6 标记为已交付,验证与 spec 一致

## 12. 验证与回归

- [x] 12.1 单元测试覆盖 ReflectionEngine 四段流水线 + 闸 1-4 + Work Context Graph 入图事务 + namespace 校验,验证覆盖率 ≥ 80%
- [x] 12.2 集成测试覆盖反思污染防御(stealth memory 注入:反思 fork 试图调用写工具 → 闸 1 拒绝),验证不留垃圾反思
- [x] 12.3 集成测试覆盖 supersession 自愈(同 title 反思 → operator='update' → 旧条 superseded_by 指向新条),验证既有反思可查询
- [x] 12.4 回归测试覆盖既有 4.5 / 4.14 / 4.15 / 4.17 / 4.18-4.20 行为不变,验证升级后既有部署 byte-identical
- [x] 12.5 运行 `openspec validate memory-storage-family --strict`,验证 change 验证通过且 strict mode 无 warning
- [x] 12.6 跑 `python -m pytest tests/ -q` 与 `ruff check src/ tests/` 与 `mypy src/`,验证四个 verification 检查全过