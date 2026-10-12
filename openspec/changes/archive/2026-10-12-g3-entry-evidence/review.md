# g3-entry-evidence 实现复核记录

## 交付范围

- 四个新增真实入口证据用例(引擎路径,`tests/test_channel/test_chat_engine_g3_entry.py`):会话中取消(模型边界)、跨请求多轮会话连续、确定性策略拒绝、回退路径钉住(开关关闭)。
- 新增 `direct_path` fixture(显式依赖 autouse `engine_path` 后置回 False),供关态钉住用例;不改既有 fixture 与路由。
- 决策记录 `docs/refactor/step5d-g3-default-enable-review.md`:逐入口证据清单、放量核对、决策(全局默认维持关闭;翻转前置为 MCP/workflow 入口证据齐备并更新决策记录)、旧循环限制成文。
- 演进计划 step5d 剩余项行更新。
- 生产代码零改动(提案边界第 6 条成立:证据未暴露需要修复的缺陷)。

## 实现中的发现与取舍

1. **关态下无会话行**:`record_session_path` 尽力而为,直连路径不创建 SessionModel 行,关态钉住用例不断言会话元数据,以"零引擎事件 + 响应语义"作为路由证据。
2. **跨轮回执内容为对账标记**:引擎路径续轮恢复的前轮工具回执内容是运行时账本的 `[reconciliation required]` 标记(记录成功但结果内容不可用,G2 既有边界)。多轮连续性用例按存在性断言该回执并注释说明;该边界已登记进决策记录,作为默认翻转评估的先行收敛项。
3. **ASGI 测试传输不投递中途断连**:取消证据在传输接缝注入断连信号(与 S12 取消场景同构);入口接线、边界检查、SSE `execution_stopped`/`cancelled` 渲染、恢复语义全部真实。首轮实现曾尝试"工具门 + 客户端提前断开"结构,因 httpx 流关闭会等待 app 任务而死锁,已重构为信号注入。
4. **stub 流式工具调用形状**:直连流的 `parse_tool_calls` 经 `model_dump` 扁平化,文件内 `_stream_tool_delta` 补齐 `model_dump()`(与 scenarios conftest 的 StubToolCallDelta 同构;生产 litellm chunk 为 pydantic 对象)。该修改对引擎路径消费者透明。

## 验证

| 验证 | 结果 |
|---|---|
| `test_chat_engine_g3_entry.py` 全量 | 10 passed(6 既有 + 4 新增) |
| 定向回归(tests/test_channel + S12 + 事件存储接线) | 107 passed |
| ruff check / format(tests + src) | 通过 |
| mypy src | 634 files 零错误 |

## 未验证项

- MCP `agent_chat` 与 workflow 入口的引擎路径真实入口证据仍缺(默认翻转前置条件,已登记决策记录)。
- 真实服务器(非 ASGI 测试传输)上的断连投递语义未在本轮复验;生产由 starlette 断连监听呈现,语义一致。
- 跨轮回执对账标记的内容收敛不在本轮范围(运行时 G2 边界)。
- 真实模型维度不在本轮范围(归 step5e 已登记的 skipped 真实轨)。
