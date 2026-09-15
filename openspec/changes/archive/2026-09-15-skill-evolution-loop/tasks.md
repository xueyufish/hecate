## 1. 数据模型与迁移

- [x] 1.1 新增 `EvolutionRunModel`（run 状态机、类型、错误原因、token/调用计数）与 `SkillCandidateModel`（候选、版本、delta 列表、evidence 引用、验证报告、审核状态、provenance）及学习输入队列表，Alembic 迁移
- [x] 1.2 `SkillModel` 增加 learned skill provenance 字段（source、来源 run ID、失败类别），含迁移与 Read/Update schema 同步
- [x] 1.3 workspace 隔离的 repository 层与单测（跨 workspace 不可见）

## 2. skill-loader 两级加载（skill-loader spec）

- [x] 2.1 `SkillLoader` 重构：L1 目录格式化（name + description，XML block）、L2 按需加载入口、`auto_load=True` 保持全量注入
- [x] 2.2 两级 token 预算实现：L1 目录预算、L2 per-skill `max_tokens` 截断、总预算淘汰（最低优先级先出）并记日志
- [x] 2.3 未广告 skill 的 L2 请求拒绝（错误信息含 skill 名）
- [x] 2.4 更新 chat graph / `agent_execute` 的 system prompt 组装；`load_skill` 以内置工具实现（注册进 `BUILTIN_TOOL_DEFINITIONS` 与 `BuiltInToolExecutor`，见 design D9）
- [x] 2.5 加载行为埋点（触发次数、L2 加载次数）供效果回流统计
- [x] 2.6 按工作空间/agent 灰度开关：可回退全量注入旧行为；更新 `tests/test_services` 下 skill-loader 既有用例并补两级用例

## 3. 信号采集与学习输入（skill-evolution-pipeline spec）

- [x] 3.1 会话完成事件后的异步信号检查（质量分阈值 + 用户显式纠正），创建学习输入记录；失败降级告警不阻塞会话
- [x] 3.2 学习输入状态与查询 API（studio 侧内部使用）
- [x] 3.3 采集链路单测（阈值命中/纠正命中/正常跳过/采集失败降级）

## 4. 归因与候选生成（skill-attribution spec）

- [x] 4.1 规则预过滤器（超时、工具连续报错、循环重复、用户纠正），无模式标 skipped
- [x] 4.2 LLM 归因服务：AgentRx 十类 taxonomy、置信度、根因、证据引用；调用走平台模型路由；system_failure / inconclusive / guardrails_triggered 不产候选
- [x] 4.3 候选生成服务：procedure + guardrails 分区、evidence 附着、structured delta 更新、知识型校验（含脚本段即拒绝）
- [x] 4.4 同主题候选去重合并（失败类别 + 主题相似度），合并依据入 lineage
- [x] 4.5 候选内容接 content-scanning/DLP 扫描，命中标 blocked
- [x] 4.6 归因/生成服务单测（stub LLM，覆盖各失败类别与拒绝路径）

## 5. 验证门禁（skill-evolution-gate spec）

- [x] 5.1 golden subset 固化：从 agent 历史轨迹生成黄金子集并持久化，不足时标 insufficient_data
- [x] 5.2 数据集回归对接评估框架（命名数据集版本 + 阈值基线）
- [x] 5.3 触发测试：judge 模型按 L1 目录 + 轨迹上下文输出 would-load 判定（design D10）——相关轨迹应触发、无关轨迹不应触发
- [x] 5.4 with/without baseline 对比跑批与结构化验证报告（逐项 pass/fail/skipped + 证据数据）
- [x] 5.5 门禁编排：四项执行、汇总、阻断/挂起路由，单测覆盖全通过/单项失败/数据不足三条路径

## 6. 审核、发布与 lineage（skill-evolution-gate spec）

- [x] 6.1 studio API：候选列表/详情/验证报告查询/批准/驳回/修改后批准（编辑 diff 留痕）
- [x] 6.2 发布路径：published 写入 workspace skill registry（`SkillModel` + provenance），遵循既有绑定模型
- [x] 6.3 unpublish 下架：立即退出 L1 目录与 L2 可加载范围，lineage 保留
- [x] 6.4 lineage 全链查询 API（轨迹 → 归因 → 验证 → 审核）与权限（workspace 隔离）
- [x] 6.5 API 集成测试（批准/驳回/修改后批准/下架/跨 workspace 不可见）

## 7. 管线编排与调度（skill-evolution-pipeline spec）

- [x] 7.1 `studio/self_evolution/` run 编排器：状态机推进、异常终态、lineage 记录
- [x] 7.2 evolution agent 实现并在 `core/composition/wiring.py` 注册 MetaAgentScheduler（配置开关控制，关闭时不注册）
- [x] 7.3 run 级成本预算（LLM 次数/token 上限，超限 budget_exceeded，已产候选继续进门禁）
- [x] 7.4 效果回流统计查询（触发/L2 次数、使用/未使用会话质量对比）
- [x] 7.5 端到端闭环测试：种子失败轨迹 → run → 候选 → 门禁（stub 评估）→ stub 人审 → published → loader 可触发 → 统计可见

## 8. 骨架清理与文档

- [x] 8.1 删除 `src/hecate/studio/evolution/`（trajectory_analyzer、policy_evolver、integration、environment_generator）及其测试
- [x] 8.2 删除 `src/hecate/runtime/self_improvement/` 的 constraint_generator、constraint_injector；确认 failure_analyzer 无残余引用后一并处理
- [x] 8.3 全量验证：`ruff check`、`ruff format --check`、`mypy src/`、`python -m pytest tests/ -q`
- [x] 8.4 docs 增补：`docs/gotchas.md` 记录两级加载语义与 `auto_load` 逃生门
