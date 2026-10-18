# Proposal

## Why

`#269` 交付了 `DecisionStep` 与 `AcceptedDecisionStore`，但 `DecisionResponder` 至今只有抽象契约和测试替身——决策持久化闭环没有真实的模型侧生产者。本 change 补上第一个生产实现：经注入的 `RuntimePort.llm_invoke_structured()` 获取一次结构化响应，转换为可提交的 `DecisionProposal`，由现有 `DecisionStep` 完成接受与持久化。它与并行的工具执行组件、评测证据完整性切片分别准备决策生产、动作执行和发布条件；决策到动作的接线仍待后续切片，不据此宣称端到端链条已交付。

## What Changes

- 新增 `PortDecisionResponder`：`DecisionResponder` 的第一个生产实现，消费注入的 `RuntimePort`，完成一次结构化调用并产出 `DecisionProposal`。
- **固定调用输入**：宿主注入已准备好的 messages、模型配置和工具定义快照，绑定对应的输入/配置引用；引用不匹配时在模型调用前拒绝。不引入 Context/Harness 装配框架。
- **转换结构化响应**：按现有端口契约聚合文本与累计 `tool_calls`，兼容端口的两种块形状（默认实现的单块全文、生产适配器的流式增量 + 末块聚合 `tool_calls`）；严格校验工具名称与 JSON 对象参数。只保留明确选择的可见输出，不保存原始 vendor 响应或隐藏推理。
- **生成动作提案**：动作键绑定 Run、决策步骤及调用位置，不依赖 provider 的临时调用 ID；副作用分类复用 `tool_side_effects.classify()`，未知工具保持 `unknown`。
- **保持单次调用边界**：流中断、取消或响应无效时显式失败并关闭流，不提交部分决策、不自动重问模型；复用端口已有计量路径（完整合法响应的末块 usage 仅作可选观察，端口负责所有尝试的计量），不重复结算。

明确不在本 change 范围（留给后续切片）：纯文本完成决策（当前 `validate_decision` 要求非空动作集合）、多轮 Controller 循环、动作派发执行、真实模型质量认证（本 change 使用确定性端口替身验证）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `accepted-agent-decision`: 新增端口支撑的决策响应方（`PortDecisionResponder`）转换语义——调用前输入固定与引用校验、结构化响应到提案的严格转换、动作键派生规则、单次调用边界与失败行为。现有 `DecisionStep` 的先查后问、原子提交与恢复要求不变。

## Impact

- **代码**：`packages/hecate-runtime` 新增 responder 模块（`PortDecisionResponder`）及其单元/集成测试；不修改 `decision_step.py`、`accepted_decision.py` 的现有语义。
- **规范**：`openspec/specs/accepted-agent-decision/` 增加 delta requirements。
- **不受影响**：工具执行链（step7 在途 change 的范围）、评测门禁（step11 在途 change 的范围）、数据库 schema、durable 存储实现、入口路由。
- **扩展点合规**：`DecisionResponder` 已是既有扩展点，本 change 提供其第一个生产实现，满足"第二实现或具名消费者"要求。
