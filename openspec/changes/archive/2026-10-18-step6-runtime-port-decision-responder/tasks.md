# Tasks

## 1. 实现前核对

- [x] 1.1 核对 `_ProductionRuntimePort.llm_invoke_structured` 聚合条目的实际字段形状（`name`/`arguments` 键名、`arguments` 是否为 JSON 字符串、多个 `tool_calls` 块的出现次数），与 design D3 假设对齐；以 `runtime_port_adapter` 既有测试或源码为证，结论记录在实现备注
- [x] 1.2 复查 `tool_side_effects.classify` 的登记范围与 `SideEffectClass` 值域，确认未知工具返回值即 `unknown` 路径

## 2. PortDecisionResponder 实现

- [x] 2.1 新建 `packages/hecate-runtime/src/hecate_runtime/port_decision_responder.py`：按 design D2 实现构造签名与调用前校验（`canonical_json` 引用比对不匹配抛 `DecisionValidationError`、空 `tool_definitions` 拒绝），以单元测试"引用不匹配零调用""空快照零调用"通过为证
- [x] 2.2 按 design D3 实现流聚合：文本按序拼接、`tool_calls` 取最后一份、流末无工具调用抛 `DecisionValidationError`、`arguments` 严格解析（dict 深拷贝 / str `json.loads` 后必须 dict / 其余失败），以单块全文与流式增量两条形状的测试通过为证
- [x] 2.3 按 design D4/D5 生成动作提案：`action_key` 由身份摘要与调用位置派生（不读 provider 临时 ID）、逐动作 `classify` 且未登记回落 `unknown`、`accepted_output` 仅 `{"text": 聚合文本}`，以动作键稳定性与记录无 vendor 字段的断言通过为证
- [x] 2.4 按 design D6 实现单次调用边界：`contextlib.aclosing` 保证底层生成器关闭、`CancelledError` 原样传播、`on_usage` 仅在流成功结束后以原样字典调用一次，以中途中断/取消两条路径的测试通过为证

## 3. 测试

- [x] 3.1 新建 `packages/hecate-runtime/tests/test_port_decision_responder.py`，用确定性 stub port 覆盖：多动作互不重复、文本分块聚合、非法参数显式失败、未知工具名显式失败；全部断言通过
- [x] 3.2 补充流中断与取消用例：中断后无提案、无第二次模型调用、生成器已关闭；断言通过
- [x] 3.3 集成用例：`PortDecisionResponder → DecisionStep → 内存 AcceptedDecisionStore` 全链路——恢复命中时模型零调用并返回原动作键与实际参数、提交失败不触发第二次模型调用、旧输入版本冲突零调用；断言通过
- [x] 3.4 计量断言：`on_usage` 收到端口报告的原样用量且仅一次、无回调时安全丢弃、`accepted_output`/`content_digest` 不含用量字段；断言通过

## 4. 验证与收尾

- [x] 4.1 运行 `bash scripts/dev.sh test packages/hecate-runtime/tests/test_port_decision_responder.py packages/hecate-runtime/tests/test_decision_step.py -q`，确认新旧测试全部通过且无跳过遗漏
- [x] 4.2 运行 `bash scripts/dev.sh check all`（ruff + 类型检查）达到 0 错误
- [x] 4.3 运行 `openspec validate step6-runtime-port-decision-responder --strict` 通过，并逐条核对 delta spec 场景均有对应测试


## 5. 复核修正与证据

- [x] 5.1 冻结构造输入和引用、每次调用分离副本，绑定真实工具定义；补充宿主/端口修改、工具集合扩张和配置冲突测试。
- [x] 5.2 严格校验流块与最终非空列表、参数有限 JSON、Run/步骤身份；读取时快照累计动作及用量，补充实际 task.cancel 和完整动作后流异常无提交测试。
- [x] 5.3 增加真实生产适配器的 provider 分片转换与费用单次记录测试（含无效参数），以及独立进程禁止图/平台/其他 workspace 依赖的运行证据。
- [x] 5.4 完成受影响回归、共享检查、严格 OpenSpec 校验并记录实际结果；不提交、推送或归档。
