## Context

7.2c（`openspec/changes/archive/2026-09-11-online-offline-evaluation-tasks/`）已经把评估体系带到"agent 被测对象（`answer_source=agent`）+ baseline 回归（`baseline_run_id`）+ 异步 run 生命周期 + target-typed 评分表"的位置，并显式把"工作流被测对象""session 级多轮""历史回填"推到后续特性（见 archived proposal Non-goals 与 `EvaluationEngine._generate_answer_via_agent` 既有懒导入模式）。`WorkflowExecutionService`（`src/hecate/studio/workflows/execution_service.py`）是 chat API 真实使用的生产执行路径，但 `RuntimePort.workflow_execute` 没有生产实现；评估引擎只能按"懒导入 studio 服务"模式复用它。

`WorkflowVersionModel`（`src/hecate/models/workflow.py`）已经提供 immutable 不可变版本 + `published_version` 字段，`studio/api/workflows.py` 的 `POST /workflows/{id}/publish/{version}` 已经是稳定契约。`tests/test_layering_domain.py` 的 AST 扫描规则要求 ops 域仅可对 studio 做函数级懒导入（既有先例：`ops/scheduling/executors.py → studio.workflows.test_runner`、`ops/evaluation/tasks/online_worker.py → studio.event_state`）。

外部约束：
- 不动 runtime 请求路径（7.2c 已被 CI 锁定为约束，本 change 同样遵守）。
- 一轮 PR 一个目的（仓库 AGENTS.md）；publish API 契约变更应独立评审——故 v1 不引入 publish 阻断，留 7.3a。
- `AnswerSource` 是 7.2a/7.2c 已交付的 StrEnum，新增值是向后兼容的枚举扩展。

## Goals / Non-Goals

**Goals:**
- 把 workflow 提升为评估引擎的一等 answer source，复用 7.2c 全部既有产物（run 生命周期、summary、baseline_run_id、threshold、scores 表）。
- 提供跨工作流版本回归对比的可观察输出（per-metric + token/latency/cost + 数据集漂移 + 节点漂移）。
- 让 publish API 把评估结果以建议式报告形式带回（不阻断），为 7.3a publish 阻断留接口（仅加挂载字段、不动语义）。
- 把"运行成本"作为护栏固化在任务配置层，避免不知情的工作流重复执行把账单打穿。

**Non-Goals:**
- 不引入 publish 阻断开关（→ 7.3a）。
- 不引入命名 dataset 版本管理 / checkout / diff UI（→ 7.3b）。
- 不引入 known-bad 豁免标注（→ 7.3c）。
- 不做节点级打分；节点轨迹只落档不进评估器（数据就位留给 7.9 Testing Center）。
- 不做 session 级多轮评估、user simulation、persona（→ 7.2d/7.2e）。
- 不做 conformance 录-放-比（golden recording vs replay）；独立特性，Hecate 已有 EventStore + time-travel 底子但工程量大。
- 不改 runtime chat API 路径；不动 `_TestWorker`（`studio/workflows/test_runner.py` 的 mock-only 路径保持原样，结构化冒烟测试与质量评估分两个出口）。

## Decisions

### D1. Workflow 执行复用 `WorkflowExecutionService.execute`，不实现 `RuntimePort.workflow_execute`
7.2c 把 agent 被测调用走 `RuntimePort.agent_execute` 是因为生产 adapter 已有实现。`workflow_execute` 没有生产实现（`core/composition/` 内无 override，只有 port 签名与 runtime 内的 `WorkflowTool` 包装器）；强行实现 adapter 会引入与 chat API 并行的第二条生产路径，重复 `CompositeWorker` + 锁 + 证据持久化 + session state 同步逻辑。直接懒导入 `WorkflowExecutionService` 既与既有 `ops → studio` 跨域规则一致（先例：`ops/scheduling/executors.py` 懒导入 `studio/workflows/test_runner`），又零成本复用 chat API 已经验过的真实生产路径。

**评估**：adapter 双路径方案——代码量大、回归面广、且会越过 `_ProductionRuntimePort` 已固化的"不引入平行生产路径"边界。

### D2. Run 绑定 workflow_version 用"启动时锁定"，不用"每次解析 latest"
任务配置可以省略 `workflow_version`，但 run 一旦启动就**冻结**当时解析到的版本号并写入 run 记录。理由：repetitions 和 baseline_run_id 比较都要求可复现，"每次解析 latest"会让同一个 run 在半路撞上 publish 或新版本，diff 不可解释。

**评估**：完全强制必填版本号——更安全但增加 UX 摩擦；完全省略——多租户忘记钉版本会拿到莫名 diff。锁定时点折中。

### D3. 主体绑定字段用专用 `workflow_id`/`workflow_version`，不用通用 `subject_type/subject_id/subject_version`
`EvaluationTaskScoreModel` 已有 `target_type/target_id`（用于 trace/session/span 粒度，7.2c 交付），那是评分维度的 target；run 主体绑定的语义不同（"这次 run 是为谁跑的"），强通用化会混入"score target"与"subject"两个意图，schema 含义不清。Run 主体用专用字段、score target 仍用 `target_type` 各司其职；将来真有 agent 版本回归需求时再单独建表或独立列，不在 v1 把 schema 强行折成一张。

**评估**：通用列——短期看 schema 简洁，但 score target 与 subject 语义混在一起会让 7.2c 的 `target_type=trace` 取值空间爆炸。专门列——短期多两列，长期清晰。

### D4. Dataset 快照 = 启动时拷贝（content-hash + 全条目 JSON），不引入命名版本对象
回归对比的健全性只需"两次 run 用同一份数据"。命名 dataset 版本（v3.0 字符串、版本对象树、checkout、browse）是独立 UI 与权限话题（谁能升版本、能否回滚），会让 v1 同时造两套机制（快照 + 版本），大概率互相打架。先让快照机制跑，看真实需求方是否自然形成版本心智，再决定 7.3b 的范围。

**评估**：直接用 dataset id + 当前 hash 区分漂移——便宜但没有可重放锚点；命名版本对象——UI、权限、并发编辑问题不进 v1。

### D5. Pass-rate / consistency-rate 暴露在 summary，不引入新的 regression 输出对象
7.2c 的 `summary` 已是回归判定的 contract；把 `pass_rate`/`consistency_rate` 直接挂上去，新增字段语义一致，diff 端点不引入新结构。复用了 7.2c 的 `regression_threshold`（默认 5%）作为 `is_regression` 判定门槛，避免第二套阈值配置。

**评估**：引入独立的 `regression_summary` 子对象——更结构化但和 7.2c 的 summary 并行，新旧两套字段共存一段时间会被消费者同时读两份，违背"一个目的一个来源"。

### D6. 评估执行成本护栏作为任务配置字段，不用全局 feature flag
7.2c 在线侧用 `sampling_rate` + `max_traces_per_cycle` 做护栏；离线侧对应的 cost 量级是"items × repetitions"，不同工作流能承受的边界不同（一个轻量工作流可以跑 5000 条、一个重型工作流跑 50 条就贵）。把 `max_total_executions`/`max_in_flight` 放到 task config，多租户自调；超阈值拒绝触发并附 400，不静默截断（截断会让用户拿不到完整 diff 但不知情）。

**评估**：全局 feature flag——失去 multi-tenant 弹性；静默截断——拿到部分结果却不知道是漏的；这两条都是 7.2c 故意避开的反模式。

### D7. CLI 退出码三态，与 PR comment 渲染解耦
退出码 0/2/3 对应 Braintrust action 的"action 只管 PR comment 渲染、阈值判定放用户脚本"原则——评估执行与"渲染给人看的报告"分层。CI 系统用退出码接 branch protection，用 PR comment 由调用方独立组件生成。**不内置 PR comment**——避免把 markdown 模板与 CI 平台耦合进产品本体。

**评估**：内置 PR comment action——短期看起来方便，长期被用户在 GitLab CI / Jenkins 里替换时变成负担。

### D8. Publish 报告附加而非阻断；评估数据存在性不影响 publish HTTP 状态
Publish 是已发布的稳定契约，叠加 `evaluation_report` 是字段增量；不存在/失败时 `evaluation_report: null`，publish 仍 200。这一规则让 publish API 不因为评估体系而新增失败模式，7.3a 才能在不破坏既有用户的前提下引入阻断。

**评估**：publish 失败时也附加报告——成功/失败路径分叉会让客户端处理变复杂；publish 阻断——跨特性契约变更，应独立评审（7.3a）。

## Risks / Trade-offs

- **R1: 真实工作流执行成本不可控** → 任务级 `max_total_executions`/`max_in_flight` 必填校验 + 默认保守值；CI 触发时先打印预期执行数与估算 token 成本（基于上一 run 的 `cost_delta`），让用户审完再继续。
- **R2: LLM 非确定性让 pass/fail 信号噪声** → `repetitions > 1` 暴露 `consistency_rate`，让"看似稳定但有概率失败"的 agent 在 summary 里显形；不把 LLM judge 的 regression signal 直接喂进 publish 阻断（即便 7.3a 也只允许确定性评估器参与阻断）。
- **R3: 一次性 session 暴增撑爆会话表** → 评估 session 在 metadata 上加 `purpose: "workflow_evaluation"`，让会话列表/保留策略（事件保留 `ops/retention/event_retention_service.py`）按 purpose 过滤/缩短 TTL，避免评估 noise 影响租户会话视图。
- **R4: 锁竞争（in-flight turn 409）** → 评估用全新 session_id 触发，无并发 turn，理论上不会撞锁；但 `WorkflowExecutionService._sync_event_position` 仍会触碰 event_store 与 session_state——写完一个端到端 smoke 后用 100 并发模拟撞一次确认（参见 tasks.md 验收条件）。
- **R5: 嵌套 agent / 嵌套 workflow 递归** → `WorkflowExecutionService.execute` 已支持递归（参见既有 `coordinator_worker` 用例）；evaluation run 一次性 timeout 与 chat API 默认一致，递归深度超限由上游 guardrail 处理，本 change 不引入新边界。
- **R6: `dataset_snapshot` 把大 dataset 序列化进 run 行** → Alembic 落 `JSONB`（参照既有 `node_results` JSON 列已落地），单 run 几万条条目的 size 上限以单条 ≤ 8KB + 总行 ≤ 64MB 兜底，超限任务拒绝触发（任务级 `max_total_executions` 顺带约束）。
- **R7: 命名 dataset 版本（v3.0 字符串）容易误用** → v1 任务配置不接受任意版本字符串，只接受"不指定 / 指定数字"二选一；命名版本管理整体推迟到 7.3b，等真实需求方出现。
- **R8: diff API 误对齐不可比 run** → 与 7.2c 同款前置校验（同一 dataset 或快照 hash 可对齐、同一评估器集），不静默产出误导 diff；spec 已声明 422 拒绝路径。
- **R9: 调研文档中受限站点的证据不可一手验证**（华为 AgentArts、阿里云百炼、Salesforce Help、IBM Docs、Arize Docs、OpenAI Platform Docs、Anthropic Console）→ `docs/research/2026-09-workflow-evaluation-competitive-analysis.md` 的"待人工复核"段落作为提案评审前的二次核查项；采纳的模式（Bedrock/ADK/LangSmith/Langfuse/Braintrust）已有一手 URL，不依赖受限站点。

## Migration Plan

- 部署：单次 Alembic 迁移加列（`workflow_id`/`workflow_version`/`dataset_snapshot`/`repetitions` 全 nullable，旧 run 行不受影响），跟随 PR；其余为代码变更，无需顺序协调。
- 回滚：Alembic `downgrade()` 删列；评估路径不影响 runtime chat API，回滚面仅限评估特性本身。
- 启用方式：无需 feature flag（7.2c 已声明"tasks 默认 active"，本 change 沿用）；新增 `answer_source=workflow` 是按需显式选择，存量任务零行为变化。

## Open Questions

（无可推迟到实现阶段的开放决策。已决断项：subject 字段专用化见 D3、快照 vs 命名版本见 D4、护栏放在 task config 见 D6、退出码三态 + PR comment 解耦见 D7、publish 不阻断见 D8。）