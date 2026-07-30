## 新增的需求

### 需求：协商图模板
系统应在 `engine/templates.py` 中提供 `build_negotiation_graph` 工厂函数，返回实现双智能体协商协议的 `GraphConfig`：提案 → 回应 → 接受/拒绝，并支持可配置的最大轮数。

#### 场景：协商模板结构
- **当** 调用 `build_negotiation_graph(agent_a_model, agent_b_model, max_rounds=5)`
- **则** 图应包含：2 个 AGENT 节点（提案方、回应方）、1 个 CONDITION 节点（check_agreement）、形成协商循环的边，以及用于智能体间提案的 `negotiation_channel` LAST_VALUE 通道

#### 场景：协商轮次往返
- **当** 协商图执行且提案方发送提案
- **则** 回应方应通过共享的协商通道接收提案，并以接受或反提案回应

#### 场景：协商在达成一致时终止
- **当** 回应方接受提案（向通道写入 `agreement_status="accepted"`）
- **则** CONDITION 节点应路由到 `__end__`，不进行进一步轮次

#### 场景：协商在最大轮数时终止
- **当** 协商达到 `max_rounds` 而未达成一致
- **则** 图应以 `agreement_status="max_rounds_reached"` 终止

### 需求：辩论图模板
系统应提供 `build_debate_graph` 工厂函数，返回实现两个智能体之间多轮辩论（带可选法官）的 `GraphConfig`。

#### 场景：辩论模板结构
- **当** 调用 `build_debate_graph(debater_a_model, debater_b_model, judge_model, rounds=3)`
- **则** 图应包含：2 个 AGENT 节点（辩手_a、辩手_b）、1 个可选的 AGENT 节点（法官）、一个轮次计数器 LAST_VALUE 通道、用于交替辩论轮次的边，以及最后的法官评估

#### 场景：辩论轮次执行
- **当** 辩论图执行第 1 轮
- **则** 辩手_a 写入论点，辩手_b 读取它并写入反驳，然后轮次计数器递增

#### 场景：带法官的辩论
- **当** 所有辩论轮次完成且配置了法官
- **则** 法官节点应读取所有论点并产生最终裁决

#### 场景：不带法官的辩论
- **当** 所有辩论轮次完成且未配置法官
- **则** 图应终止，所有论点累积在消息通道中

### 需求：模板遵循现有约定
协商和辩论模板函数均应遵循已建立的模式：接受模型/提示参数、返回 `GraphConfig`、使用 `NodeType.AGENT` 作为智能体节点，并在 `state` 字典中定义通道。

#### 场景：导入模板工厂
- **当** 执行 `from hecate.engine.templates import build_negotiation_graph, build_debate_graph`
- **则** 导入应成功

#### 场景：返回的 GraphConfig 可编译
- **当** `build_negotiation_graph(...)` 生成一个 GraphConfig
- **则** `GraphCompiler.compile(graph_config)` 应无错误地成功
