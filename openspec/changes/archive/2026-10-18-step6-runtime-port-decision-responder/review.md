# 复核结论

在指定 worktree 完成检查和直接修正。当前切片通过本地复核，无未处理的阻断问题；没有提交、推送或归档。CI 和 PR review 仍由后续流程完成。

## 已修正问题

- **P1：构造输入未冻结。** 原实现直接保存 messages、config、tool_definitions 和 expected_refs 的引用。宿主可在构造后改版本、参数或工具集合，端口也可修改后续调用输入。现以严格 JSON 固定快照、保存不可变引用串/名字集合，并向每次调用分离副本。
- **P1：实际模型工具定义与校验集合分离。** 原实现只用 tool_definitions 查名字，却直接把另一份 config.tools 发给模型，且缺省时不提供任何工具。现使用完整 type/function 定义校验快照键名；config.tools 缺省时补齐，已提供时须逐定义一致，冲突在模型调用前拒绝。工具可见性仍不是派发授权。
- **P2：无效响应可返回提案。** 原实现接受空列表/空对象为零动作提案，JSON 字符串中的 NaN/Infinity/溢出数未经有限性复核，错误 content 类型被静默丢弃。现严格检查块、累计列表、非空最终动作、有限 JSON 和步骤身份，错误均为显式 DecisionValidationError。
- **P2：已产出缓冲仍可被端口修改。** 原实现保留 tool_calls/usage 的可变引用。现读取时选取并冻结必要字段，避免后续迭代改写先前动作或用量，并排除 vendor 元数据与隐藏推理。
- **设计描述修正。** 同一 Run/步骤/位置的动作键跨候选尝试稳定，候选内容由提交摘要仲裁；DecisionStep 不自动重问。用量回调是合法响应的可选观察，失败尝试费用仍属端口责任。

## 证据

- 原有 responder/decision 测试：33 passed。
- 添加回归后对原实现运行：23 failed、28 passed，证明上述缺口可复现。
- 修正后相关 package、生产适配器、费用、LLM worker、自足性与分层回归：143 passed，无跳过。
- 新增真实 task.cancel、已产出动作后流异常的无提交断言；独立进程阻断图、Worker、平台及其他 workspace wheel 后仍完成响应转换。
- 生产 `_ProductionRuntimePort` 消费 provider 形状的碎片并完成原有累计过程，验证转换及单次费用记录；底层模型服务为确定性替身，没有外部模型请求。
- 共享检查与严格 OpenSpec 校验通过，详见 verification.md。

## 支持边界

仅新增端口响应转换及其契约证据，不改 DecisionStep、持久化 schema、Action Ledger、工具执行链、评测门禁或入口装配。不支持空动作完成决策，不交付多轮 Controller 或动作派发；不宣称真实模型质量、生产部署或新的跨进程存储认证。
