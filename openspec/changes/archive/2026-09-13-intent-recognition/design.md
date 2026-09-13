# Design: intent-recognition

## Context

现状(动机见 proposal.md):

- 2.7c 的 INTENT 路由模式在 `runtime/routing.py`:`IntentPattern` 正则列表 + `routing_prompt` LLM 兜底,单轮、单节点、无状态。
- runtime 有自足性不变量(`tests/test_runtime/test_runtime_self_sufficiency.py` 子进程 probe + AST 层级扫描):studio/enterprise 依赖只能走函数级懒导入桥(有 probe allowlist),或经组合根注入的 Port。
- 动态编排(COORDINATOR)确立了先例:LLM 决策 → executor 物化;决策/评估事件(加性 `EventType`)按 ADR-030 进 EventStore;子图调用有成熟的 child-session 隔离与 channel mapping 语义。
- `SessionState`/`SessionStateStore` 已存在,是会话级状态的既定存放点。
- 7.x 评测机器:named dataset versions(7.3b,canonical 序列化 + content_hash)、publish gate(7.3a,确定性信号 + 409 + force 审计)、标注队列(7.4)、评测任务(7.2c)。

行业调研结论中影响本设计的两条:分类器分栈(plan 模型 ≠ execute 模型)是 AgentCore 官方推荐;few-shot 证据需要防模式锁定(Manus 的经验:固定样例导致 pattern lock)。

## Goals / Non-Goals

**Goals:**

- 一个识别引擎、多个消费者(控制器节点、INTENT 路由模式),引擎对证据来源唯一依赖已发布意图包快照。
- 热路径性能:快路径分栈 + 决策缓存,缓存命中零 LLM 调用。
- 治理复用:版本/门禁/回流全部落在 7.x 既有机器上,不自建闭环。
- 全部契约加性:存量路由行为、存量事件、存量 schema 零破坏。

**Non-Goals:**

- 元意图识别器(L5 仅为策略标记 + 确定性 hooks 执行,无识别过程)。
- 自进化自动闭环(v1 只交付数据通路:纠错回流 + 门禁 + 评测;不做自动 prompt 重写/自动阈值调整)。
- Dify Annotation Reply 式的人工钉死路由(人工 override)——列为后续项。
- 嵌入检索式 few-shot 选择(kNN top-k)——v1 用有界确定性采样 + 轮换,向量检索后续再上。
- 跨 workspace 意图包共享与 marketplace。

## Decisions

### D1. 引擎位置与扩展点形状

引擎放 `runtime/intent/`(识别管线、缓存、types),runtime 内部扩展点按命名规范用普通名词 + ABC(不挂 Port/ABC 后缀标记)。证据跨层(runtime ← studio 的意图包存储)用 `IntentEvidencePort`:这是 runtime↔domain 的六边形接缝,按命名规范用 `Port` 后缀,由组合根接线 studio 侧 provider;若实现需要桥接文件,加入自足性 probe allowlist 并在 runtime/AGENTS.md 登记。

**备选**:塞进既有 `RuntimePort`(否——把无关职责挂到大端口上,违背端口隔离先例);在 studio 保存图时把证据 payload 内嵌进节点配置(否——包发布后配置内嵌证据即刻过期,且配置体积失控)。

### D2. 五级的实现语义

- **L1 原子**(逐轮):有序快路径 —— 决策缓存 → 包内 patterns/关键词 → few-shot(包分类+样例)→ LLM 结构化输出兜底。LLM 输出用 JSON 枚举约束(仅允许包分类 label),校验失败按 fallback 处理。
- **L2 工作流**:会话内滑动窗口上的任务检测器 —— 信号 = 连续原子意图的关联性 + 显式多步线索(祈使句序列、计划性措辞),阈值激活;激活时输出任务级 label。不用独立 LLM 常驻调用,复用 L1 结果 + 轻量规则,仅在信号模糊时追加一次 LLM 判定。
- **L3 会话**:SessionState 新增字段;更新策略 = 显式目标漂移检测(新原子/工作流 label 与存量目标的对比,阈值判定)→ 替换;否则沿用。恢复会话时直接读字段,不从历史重推。
- **L4 领域**:意图包分类上的可选 `domain` 标签字段,识别结果按命中的分类带出领域 label——零额外 LLM 调用。
- **L5 策略门**:分类上的可选 `policy_gated` 标记,引擎仅在结果中标注 `gated`,执行侧由既有 deterministic hooks(1.3.5i)强制审批;识别输出(含缓存命中)不能越过策略门。

**备选**:五个独立分类器(否——成本/延迟不可接受,且元意图没有可路由的消费方,调研确认所有现实对应物都是确定性机制)。

### D3. 证据快照与 few-shot 组装

`IntentEvidencePort.get_evidence(package_id, version) -> EvidencePayload`(分类 + 描述 + 每类样例,来自冻结版本 JSON)。few-shot 组装:每类描述 + 有界样例(默认每类 5 条、总量上限 30),样例选择用确定性轮换(rotor 随识别次数推进)防 pattern lock;嵌入 kNN 选择推迟。识别模型可独立配置,经 `llm_invoke(model=...)` 解析,默认平台默认模型。

**备选**:全量样例进 prompt(否——大包 token 成本失控);首版就上嵌入检索(否——给 runtime 引入向量依赖,边际收益待评测数据验证)。

### D4. 决策缓存

进程内 LRU,键 = (normalize(utterance) 的 sha256, package_version_id, context_fingerprint),TTL 与容量可配。版本进键意味着包发布 = 键空间自然轮换,无需显式失效广播。多副本冷启动可接受;store-backed 缓存(Redis)列为后续项。

**备选**:跨副本 Redis 缓存(否——v1 延迟收益不抵运维成本);人工钉死路由(见 Non-Goals)。

### D5. 事件

加性 `EventType`:`INTENT_RECOGNIZED`(引擎:层级结果、决策来源、cache hit、证据引用、延迟)与 `CONTROLLER_ROUTED`(控制器:目标、触发层级/label、会话意图状态)。均不含 few-shot payload。LogPolicy 不排除(与 ORCHESTRATOR_* 同待遇)。

### D6. 数据模型

- `intent_packages`(workspace 隔离,name per workspace 唯一,软删)。
- `intent_package_categories`(package_id, name, description, domain?, policy_gated?)与 `intent_package_samples`(category_id, utterance, provenance JSON:source_type/source_session_id/source_turn/reason/created_by)——draft 走规范化行存储,使逐行 provenance 与标注队列集成可行。
- `intent_package_versions`(package_id, name per package 唯一,冻结内容 JSON + content_hash(与 dataset 快照同一 canonical 序列化)、created_by、published_at nullable、gate_report JSON、软删)。

**备选**:draft 也存 JSON 列(否——provenance 逐行追溯与 7.4 标注队列集成需要行身份;dataset 家族已是行存储 + JSON 快照的成熟范式)。

### D7. 控制器实现形状

`NodeType.CONTROLLER` + `runtime/workers/controller_worker.py`:以用户轮消息(TOPIC 通道)为触发,逐轮调引擎 → 写 `_route`;出边按命名目标解析(mapped category 目标 + `default`/`start`/`end`)。子工作流派发复用 agent-node 子图调用路径(child session、channel mapping、WorkerResult 失败契约)。全局意图 = 控制器持有 SessionState 会话目标,仅在漂移时重路由。

**备选**:控制器做成 CONDITION+intent 模式的语法糖(否——需要会话状态、start/default/end 语义与逐轮触发,契约形状不同);复用 COORDINATOR(否——planner 与 router 是不同契约:任务 DAG vs 分类路由)。

### D8. 编译期校验的职责切分

包引用/分类 key 的**存在性校验放在 studio 保存路径**(它拥有 DB 访问;先例:coordinator 的 fail-closed 校验);runtime 编译器只做形状校验(category_targets 非空、target 已声明)。运行时证据读取失败按 spec 降级为 LLM 兜底,不把 studio 故障传染成执行失败。这样 runtime 保持零 DB 依赖,不需要新的懒导入桥。

### D9. 范围分期(tasks 的分组依据)

1. **引擎 + 意图包**(D1-D6):runtime/intent、数据模型与迁移、studio CRUD/导入导出/版本/门禁、`IntentEvidencePort` 接线。
2. **消费者**(D7-D8):CONTROLLER 节点与 worker、INTENT 模式委托、DSL/canvas schema。
3. **画布 + 评测联动**(D5 事件消费端):1.1.21 控制器画布、意图包评测数据集生成与 7.2c 集成、纠错回流端到端。

## Risks / Trade-offs

- [缓存 miss 冷路径的 LLM 延迟] → 快路径分栈 + 模型分离(可配小模型)+ 事件带延迟指标;上限受包规模约束。
- [大包 few-shot prompt 膨胀] → 每类有界样例 + 总量上限 + 轮换;嵌入检索为后续项。
- [多副本缓存冷启动] → v1 接受;store-backed 缓存为后续项。
- [包版本演进导致映射失效] → 版本 pin 可用、compile 校验拦未知分类、画布对被移除分类显式标红要求重指派。
- [会话意图漂移误判(抖动或黏滞)] → 漂移阈值可配 + 全局意图 goal hint 可人工覆盖;CONTROLLER_ROUTED 事件可观测,为调参留数据。
- [自足性 probe 新增桥接项] → 证据只经 Port 注入,设计上无新懒导入桥;若实现意外需要,必须登记 allowlist 并在 runtime/AGENTS.md 说明。
- [评测联动范围膨胀] → 只做薄集成(样例→数据集生成 + 触发 7.2c run + 回读结果),不新增评测引擎代码;超范围需求记 follow-up。

## Migration Plan

- 单个加性 Alembic 迁移(三张新表);存量表零改动(SessionState 为 runtime 状态结构,非 DB schema 变更;若 store 实现持久化会话意图,随该 store 的既有序列化演进)。
- INTENT 模式旧配置(`intent_patterns`/`routing_prompt`)行为逐字节保留,无数据迁移。
- 回滚 = 回退发版;新表成为孤儿表,无破坏。
- 发布顺序:单 release 交付;无需 feature flag(全部消费面 opt-in:新节点类型、显式 `intent_package` 引用)。

## Open Questions

- L1 few-shot 的每类样例预算(默认 5/类、总 30)与漂移检测阈值:默认值已固化于 D2/D3,实现期用意图包评测数据集校准——属调参项,非待决决策。

已定案(原 open question):`domain` 标签粒度取**分类级**,与 D2、D6 及 tasks 1.1 的字段设计一致;如后续需要包级默认值,作为便利性扩展追加,不改行为契约。
