# Tasks: memory-consolidation

## 1. 数据层与配置

- [x] 1.1 Alembic migration:新增 `consolidation_runs` 表;`memories`/`knowledge_memories` 增 `superseded_by` 列;`alembic upgrade head` 在本地 PostgreSQL 通过且 downgrade 可逆
- [x] 1.2 `models/memory.py` 增 `ConsolidationRunModel` 与 Pydantic schema,两模型增 `superseded_by` 字段;现有模型测试套件通过
- [x] 1.3 `core/config.py` 新增 D9 全部配置项(默认关/默认值与 design 一致);`python -m pytest tests/ -q -k config` 通过
- [x] 1.4 确认 guardrail/injection 检测的库调用接缝形态(库调用或走 D7 退化路径),结论写回本任务注释;确认压力标记存储位置(`session_state` 侧列或轻量标记表)并落实现
  - 结论:guardrail 走库调用接缝——`runtime/security/llm_guard.py` 的 `LLMGuardScanner.scan_prompt`(llmguard 缺装时自带降级扫描);包代码不可反向导入宿主,故引擎以注入方式接收 scanner,wiring 侧装配,缺省退化为 prompt 级约束
  - 结论:压力标记用轻量表 `consolidation_pressure_flags`(unit 三元组唯一,zero-UUID sentinel 表示 agent 级单元),已随 1.1 迁移落库

## 2. 整合引擎(packages/hecate-memory)

- [x] 2.1 新增 `consolidation` 模块骨架:整合单元模型 `(workspace, agent, user_id|null)`、单元解析、"待处理单元"查询(基于 `recall_messages` 新转录 + `consolidation_runs` 水位);单元查询有单测(mock session + in-memory SQLite)
- [x] 2.2 实现 `_embed()` 封装(复用 recall indexer embedding client)与应用内 cosine 相似度;embedding 不可用时按精确文本去重降级并记录;单测覆盖相似合并与降级分支
- [x] 2.3 实现三段管线的抽取与打分/去重阶段(LLM 调用经 RuntimePort 形态 seam,便于 stub);候选含 confidence/type;单测用 StubXxx LLM 断言候选与去重行为
- [x] 2.4 实现计划应用阶段:操作计划 schema(ADD/UPDATE/SUPERSEDE/NO-OP/UPDATE_BLOCK)、校验器(预算、允许清单、范围、revision)与服务层应用(ADD/UPDATE 走既有服务;新增 SUPERSEDE 服务函数置 `superseded_by` + 软删除 + revision bump + edit_log 记录 `tool_name=consolidation`);单测覆盖 revision 冲突跳过、预算超限终止、单条失败隔离
- [x] 2.5 实现 `consolidation_runs` 记录与 promote-before-checkpoint 水位语义(事务内最后记 SUCCESS);集成测试:双轮运行第二轮窗口不重叠、失败轮不推水位
- [x] 2.6 实现候选安全扫描接入(guardrail 库调用或退化路径)+ 凭证模式拒绝;单测:注入载荷候选被拒、凭证被拒、其余候选不受影响

## 3. 触发总线与调度接线

- [x] 3.1 实现三种调度策略(cron/idle 轮询/压力优先)到"待处理单元"查询的映射,接入 `ops/scheduling` APScheduler manager + advisory lock;wiring 生命周期启停随 `CONSOLIDATION_ENABLED`;单测:压力标记单元排序优先、双实例锁互斥(模拟)
- [x] 3.2 idle 轮询实现(每 `IDLE_CHECK_INTERVAL` 扫"安静 ≥ QUIET_SECONDS 且有新转录"单元);时间推进测试(冻结时钟)验证调度行为
- [x] 3.3 端到端集成测试:启用配置后,注入对话 → 触发整合 → 记忆/`learned_context`/`consolidation_runs`/`memory_edit_log` 状态全部正确;关闭配置后零副作用

## 4. Memory Pressure Alert(runtime)

- [x] 4.1 实现 `MemoryPressureNudgeProcessor`(latched、用量数字注入、保护前缀后注入、计入记账);单测覆盖三场景(首次注入/不重复/回落重跨)+ 缓存前缀不变
- [x] 4.2 实现压力标记写入(消费于 3.1 的优先级)与失败降级(标记失败仅告警);单测覆盖降级路径
- [x] 4.3 context_policy 配置解析接入(平台默认不启用)与 4.13 失败策略兼容;链配置解析测试通过

## 5. 收尾验证与文档

- [x] 5.1 全量验证:`ruff check src/hecate/ packages/ tests/`、`ruff format --check src/ packages/ tests/`、`mypy src/ packages/`(558+31 files 0 errors)、`python -m pytest tests/ -q`:5338 passed / 11 failed——11 个失败经 git stash 基线复现确认为预存环境问题(Windows symlink/路径边界/shell-hook),非本 change 引入
- [x] 5.2 更新 `docs/features/feature-catalog.md` 4.5/4.17 条目与 `docs/features/roadmap.md` Sprint 8 状态(来源指向本 change 归档);检查并修复 feature-catalog 中指向不存在文档的悬空引用(改指本 change 或删除)
- [x] 5.3 `openspec validate --strict` 通过;`openspec status` 全部 done
