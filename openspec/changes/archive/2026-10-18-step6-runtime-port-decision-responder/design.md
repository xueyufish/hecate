# Design

## Context

`#269` 在 `packages/hecate-runtime` 交付了 `DecisionStep`（先查后问、原子提交）与 `AcceptedDecisionStore`，`DecisionResponder` 仅有 ABC 和测试替身。`RuntimePort.llm_invoke_structured()` 已由生产适配器 `_ProductionRuntimePort` 实现：流式产出 `{"content": 增量, "tool_calls": None}` 块，末块携带聚合后的 `tool_calls`（默认端口实现则产出单块全文）。`AcceptedAction` 要求非空动作集合、键唯一、参数为 JSON 对象、`side_effect_class` 取自 `SideEffectClass`。本设计不改动上述任何既有语义，只新增一个转换模块。

## Goals / Non-Goals

**Goals:**

- 提供第一个生产 `DecisionResponder` 实现，把一次端口结构化响应转换为可提交的 `DecisionProposal`。
- 全部行为可用确定性端口替身验证（单元 + 与 `DecisionStep`/内存 store 的集成）。

**Non-Goals:**

- 不引入 Context/Harness 装配、多轮循环、动作派发；不做 composition root 生产装配（归后续"恢复原决策并派发动作"闭环切片）。
- 不修改 `decision_step.py`、`accepted_decision.py`、durable 存储或任何 schema。
- 不做真实模型质量验收（归 step5e 真实模型切片）。

## Decisions

### D1：模块落点与依赖方向

新模块 `packages/hecate-runtime/src/hecate_runtime/port_decision_responder.py`，与 `decision_step.py` 同包。`RuntimePort` 定义于同包 `ports.py`，responder 消费它是引擎内部对既有端口的正向使用，不引入新依赖，也不违反 Runtime 自足约束。备选是放在 `core/composition/`——被否决：转换逻辑属引擎语义而非装配，且 Runner 侧未来要复用时不应依赖平台 composition。

### D2：输入在构造时固定，引用经规范 JSON 比对

构造签名固定为 `PortDecisionResponder(port, *, messages, config, tool_definitions, expected_refs, on_usage=None)`；`respond(run_ref, decision_step_id, input_refs)` 按 ABC 契约接收每次调用的引用。模型调用前先做 `canonical_json(input_refs) == canonical_json(expected_refs)` 比对，不匹配抛 `DecisionValidationError`——复用既有错误分类，`DecisionStep` 无需感知新异常。备选是从 messages/config 内容摘要自动派生引用——被否决：引用语义归宿主，切片内做确定性比对即可，自动派生会把版本语义偷运进引擎。

`messages/config/expected_refs` 在构造时以严格 JSON 编码保存不可变快照，每次调用向端口传入新副本。`tool_definitions` 为名称到完整 function 定义（`type/function`）的映射，键必须与 `function.name` 一致、非空且有界；工具参数 schema 的业务校验仍属派发边界。`config.tools` 缺省时由此快照生成；已提供时须与快照逐定义一致（列表顺序可保留），冲突在模型调用前拒绝。空映射在 respond 中、模型调用前拒绝。模型输出只与冻结的名字集合核验，外部或端口修改不得改变绑定。

### D3：聚合规则——文本拼接，动作取最终聚合块

遍历端口流：`content` 为字符串则按序拼接（单块全文与流式增量两种形状自然兼容）；`tool_calls` 非 None 时以"最后一份"为准（替换而非追加），与端口契约"末块携带聚合结果"一致，防止防御性重复。流结束后无任何工具调用 → `DecisionValidationError`（当前切片仅支持带动作决策）。

源码核对：`_ProductionRuntimePort` 向下消费 provider 的 `index` 分片，向上末块产出嵌套 `function.name/function.arguments` 的完整条目；responder 不二次拼接 provider 分片。每个非空聚合块在读取时只快照选定的 name/arguments，避免端口在下一次迭代修改已交付缓冲；最终块可以替换之前累计列表。工具列表必须是 list，最终非空；chunk 必须是对象、content 只接受字符串或 null，不静默丢弃错误类型。`arguments` 为 dict 或 JSON 字符串解析后的对象，两条路径都再经 `canonical_json` 校验有限且无损的 JSON 并深拷贝；非有限数（包括字符串中的 NaN/Infinity/1e999）、非字符串键、数组及标量均失败。

### D4：动作键由身份与位置派生

`action_key = f"decision:{sha256(canonical_json({'run': run_ref.to_dict(), 'step': decision_step_id}))[:40]}:{position}"`，`position` 为聚合列表中的序号。实现期修正（相对初稿的可读拼接形式）：`issuer_domain/run_id/decision_step_id` 各自可达 256 字符，直接拼接会超出 `validate_decision` 的键长上限、产生无法提交的提案；摘要前缀使键长度有界且形状统一，"由 Run 引用、决策步骤标识与调用位置派生"的语义不变（记录本身携带完整可读身份字段）。同一 Run/步骤/位置的键跨未接受尝试保持稳定；它不证明候选响应内容相同。`DecisionStep` 不自动重问，宿主对未接受尝试的合法重试须另行声明；内容差异由既有提交摘要/冲突仲裁，提交成功后恢复原动作集。不使用 provider 临时调用 ID（两次调用间不稳定）。

### D5：可见输出与副作用分类

`accepted_output = {"text": <聚合文本>}`，无额外字段；不把 vendor 形状的 tool_call 条目（含临时 ID）复制进可见输出，动作意图完全由 `actions` 元组承载。`side_effect_class` 逐动作调用 `tool_side_effects.classify(tool)`，未登记工具按既有分类函数保持 `"unknown"`，不接受模型自报分类，也不吞掉分类函数的其他异常。

### D6：单次调用边界与用量透传

响应方不重试、不重问。流的消费包在 `try/finally` 中，任何异常（含 `asyncio.CancelledError`，取消必须向上传播不得吞掉）先 `aclose()` 底层异步生成器再抛出；无效响应在流耗尽后判定。计量：仅在完整、合法响应携带 usage 时，向可选同步 `on_usage` 观察回调传递原值副本一次；收到块时就冻结，防止端口复用缓冲。回调不承担结算，也不承诺为失败尝试补齐用量；所有模型尝试的费用/预算仍由注入端口负责，失败费用不得记零。真实 `_ProductionRuntimePort` 适配测试验证正常及响应转换失败两条路径仍各记录一次费用。usage 不进入提案或决策记录。备选是扩展 `DecisionProposal` 增加 usage 字段——被否决：`DecisionStep` 会把提案冻结进记录，用量进记录会污染 `content_digest` 语义。

## Risks / Trade-offs

- [生产适配器累计条目的字段形状与 D3 假设不符] → 实现首步核对 `runtime_port_adapter.py` 的累计逻辑与既有测试，差异收敛在新模块内部，不外溢。
- [provider 在一次响应中对同一工具发起多次调用] → 合法场景（并行调用），键按位置区分；`validate_decision` 只拒绝键重复，不限制同工具多动作。
- [取消与流关闭的交互在 Windows/Proactor 下的生成器清理] → 测试覆盖中途中断与取消两条路径，`finally` 中 `aclose()` 保证不悬挂。
- [本切片无生产装配，responder 暂无线上消费者] → 具名消费者为后续"决策恢复 + 动作派发"闭环切片与测试；`DecisionResponder` 是既有扩展点，本切片补第一个生产实现，不新增扩展点。

## Migration Plan

纯新增模块与测试，无 schema、无入口、无装配变更；回滚即移除模块与测试。无数据迁移。

## Open Questions

（无）
