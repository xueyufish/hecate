# Proposal: intent-recognition (6.23 ⊕ 1.3.10 + 6.49, 前置 2.6a / 1.1.21)

## Why

Sprint 8 Opening Queue 的控制器家族(2.6a + 1.1.21 + 1.3.10⊕6.23 + 6.49)中,意图识别是控制器的地基:中央控制器要把用户请求路由到子工作流,前提是有一个可配置、可评测、可缓存的识别引擎。现状是 2.7c 交付的 INTENT 路由模式只是单节点、单轮的正则 + LLM 兜底(`runtime/routing.py`),没有层级、没有会话累积、没有 few-shot 证据来源,也没有可复用的意图资产。行业调研(2026-09:Bedrock AgentCore Routing Classifier、Agentforce Topic、ADK transfer、Dify/Coze 意图节点、openJiuwen controller 三件套)确认了"意图识别作为显式独立组件 + 分类器分栈(plan 模型 ≠ execute 模型)+ 默认工作流兜底"是收敛方向;同时确认两个空白:**可版本化、可治理的意图包资产**没有任何平台做出产品形态,**路由决策的反馈回流闭环**只有 AgentCore 一家有完整实现。Hecate 刚交付的 7.x 评测机器(named versions + publish gate + 标注队列)恰好是这两个空白可以复用的基建——现在做,边际成本最低。

范围裁决(探索阶段已定):v1 交付**三级识别过程**(L1 原子 → L2 工作流 → L3 会话)+ L4 领域作为可选分类标签;L5 元意图不建识别器,重定义为确定性策略边界(复用 deterministic hooks / 工具权限机制),"五级"作为架构名保留。意图包为 workspace 级一等资产,在版本/门禁/回流三个接缝复用 7.x 机器,不并入评测数据集实体。

## What Changes

**6.23 ⊕ 1.3.10 — 意图识别引擎(runtime 域)**

- 新模块 `runtime/intent/`:分层识别管线,L1 原子意图(逐轮分类,快路径:决策缓存 → 正则/关键词 → few-shot 证据 → LLM 分类兜底),L2 工作流意图(多轮任务检测累积),L3 会话意图(会话级目标跟踪,持久化进 SessionState);L4 领域标签可选输出。
- 识别模型与业务模型分离(可分别配置,plan 模型 ≠ execute 模式的先例来自 AgentCore 官方建议)。
- 意图决策缓存:键 = (规范化话术指纹, 意图包版本, 上下文指纹),包发布即失效(版本自然轮换);默认进程内 LRU。
- 识别证据来源:运行时通过注入式扩展点(`IntentEvidencePort`)读取**已发布**意图包快照;runtime 域零 studio 依赖(自足性不变量不破)。
- 识别决策作为事件进 EventStore(新增加性 `EventType`,沿 ADR-030 先例),携带层级结果、证据引用、缓存命中标记——这是自进化回流的数据源。

**6.49 — 意图包资产(studio 域)**

- 新一等资产:意图包 = 命名分类集合 + 每类样例话术 + 分类描述;workspace 隔离,CSV/JSON 批量导入导出。
- 版本与发布:命名版本冻结(复用 7.3b 的 content-hash 快照模式)+ 发布门禁(复用 7.3a 的确定性信号模式);运行时只读已发布版本。
- 评测与回流:每个包可生成 held-out 评测数据集接 7.2c 跑识别准确率;误路由纠错样本以可追溯 provenance 落入包草稿,走标注 → 门禁 → 发布闭环(7.4 机器)。

**2.6a — 多智能体中央控制器(runtime 域)**

- 新 `NodeType.CONTROLLER`:配置 = 意图包引用 + 分类→子工作流映射 + start/default/end 工作流指派 + 全局意图(会话级)开关;控制器 worker 逐轮调用识别引擎,原子/工作流意图命中即路由子工作流,未命中走 default 工作流兜底;会话意图在意图漂移时重路由。
- 子工作流执行复用既有 agent-node 子图调用与 channel mapping 隔离语义。
- 编排可视化数据面:路由决策与意图层级结果作为事件流暴露(画布/trace 消费)。

**1.1.21 — 控制器画布(studio 域)**

- 画布新增 CONTROLLER 节点:意图→工作流映射可视化(直接从意图包分类渲染)、全局意图配置面板、start/default/end 工作流指派。

**2.7c 升级(向后兼容)**

- `advanced-routing-modes` 的 INTENT 模式升级:配置了意图包引用时委托新引擎;未配置时现有 intent_patterns + routing_prompt 行为逐字节保留。

## Capabilities

### New Capabilities

- `intent-recognition`:runtime 识别引擎的行为契约——三级识别管线与各级输出、快路径分栈与 LLM 兜底、决策缓存语义、意图证据读取(仅已发布快照)、事件产出、SessionState 会话意图持久化、L5 策略边界(确定性,非识别)。
- `intent-packages`:意图包资产生命周期——CRUD、分类+样例话术模型、CSV/JSON 批量导入导出、命名版本冻结与不可变、发布门禁(确定性信号)、纠错样本回流与 provenance、评测数据集联动。
- `multi-agent-controller`:CONTROLLER 节点行为——意图包引用与分类→子工作流映射、start/default/end 工作流指派、逐轮识别与路由、意图漂移重路由、子工作流执行隔离、路由事件。

### Modified Capabilities

- `advanced-routing-modes`:INTENT 路由模式 requirement 扩展——新增意图包引用配置;有引用时委托识别引擎(层级结果、缓存、few-shot 证据生效),无引用时保留既有 intent_patterns + routing_prompt 行为不变。
- `multi-agent-canvas`:新增控制器节点 requirement——CONTROLLER 节点渲染、意图→工作流映射可视化、全局意图配置面板、start/default/end 工作流指派配置。

## Impact

- **Schema/迁移**:新表 `intent_packages` + `intent_package_versions`(分类与样例以 JSON 冻结,含 content_hash、created_by、发布状态);SessionState 模型新增会话意图字段;一个加性 Alembic 迁移,存量数据零影响。
- **runtime 域**:新模块 `runtime/intent/`(管线、缓存、types、策略门接缝)、`runtime/ports.py` 新增 `IntentEvidencePort`(组合根接线)、`runtime/types.py` 新增 `NodeType.CONTROLLER` 与加性 `EventType`、`runtime/workers/controller_worker.py` 新增、`runtime/routing.py` INTENT 模式委托、`runtime/session_state.py` 会话意图字段。自足性不变量:studio 依赖仅经 `IntentEvidencePort` 注入,新增 probe allowlist 项(如有桥接文件)。
- **studio 域**:新 `studio/intent_packages/`(service + API + CSV/JSON 导入导出)、`studio/workflows/` DSL/canvas 支持 CONTROLLER 节点与意图包引用解析、组合根接线 evidence provider。
- **契约**:全部加性——新 API 端点、新节点类型、新事件类型;无破坏性变更;INTENT 模式无包引用时行为不变。
- **评测体系**:意图包评测数据集生成与 7.2c run 的薄集成;标注队列消费纠错样本(7.4 既有能力,不加新机制)。
- **测试**:`tests/test_runtime/test_intent/`、`tests/test_services/test_intent_packages/`、`tests/test_api/`(意图包端点、控制器执行)、DSL/canvas 测试、runtime 自足性 probe 更新。
