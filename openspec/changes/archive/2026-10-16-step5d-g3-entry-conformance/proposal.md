# Proposal

## Why

step5d 剩余的 G3 项要求"实际入口的多轮、拒绝、取消、断连/恢复及默认启用证据"。现有证据的缺口在 [G3 默认启用复核](../../../docs/refactor/step5d-g3-default-enable-review.md):HTTP 已有确定性多轮/拒绝/边界取消证据,但 ASGI 缓冲传输的续轮用例不能证明**中途真实断连**;MCP `agent_chat` 与 workflow 入口**无引擎路径真实入口证据**。另有一处恢复语义缺口:跨轮恢复时"已记录成功但结果内容不可用"的工具回执以 `[reconciliation required]` 占位文本返回工具消息,模型会依据占位内容继续推理,而不是恢复真实结果或停止进入对账。

## What Changes

- **真实 HTTP 中途断连与恢复**:以真实 TCP/SSE 传输(uvicorn 真实 socket,非 ASGITransport)在执行中途断开连接,验证 PG 双引擎上的事件、终态与续轮恢复;断言已成功工具不重复产生副作用。
- **跨轮工具回执内容恢复(行为变更)**:恢复优先回填持久记录的真实结果;结果不可恢复时,写类动作停止并进入显式对账类别,不得把占位文本作为推理依据;只读类允许按既有语义安全重执行。真实断连场景的副作用断言依赖本项,先行或同批交付。
- **MCP 与 workflow 入口证据**:确定性模型/工具双打,经实际 MCP `agent_chat` 入口与 workflow 已有部署身份的 Agent 子任务路径(step6e `WorkflowChildTaskAdapter`)验证多轮、拒绝、取消与恢复,事件序列与 HTTP 等价。
- **G3 决策记录更新**:逐入口登记通过/失败/未支持范围;`CHAT_TOOL_LOOP_ENGINE_ENABLED` 默认维持关闭,翻转前置(逐入口真实证据 + 决策记录更新 + 放量记录)不变。
- 顺带修正演进方案 step5d 已关项的复核链接(指向归档后路径)。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `unified-chat-execution`: "引擎路径具备真实入口证据"扩展真实传输断连与 MCP/workflow 入口证据要求;新增"跨轮恢复回执反映持久事实"要求。

## Impact

- 代码:`hecate-runtime` `action_ledger.recovery_decision`(succeeded-无内容的分流:真实回填/停止对账/只读重执行)及 `tool_worker` 调用侧;不新增 Runtime 扩展点。
- 测试:真实 TCP harness(uvicorn in-thread + httpx 实连接,SSE 中途断开)、PG 参数化沿用 `HECATE_STEP6_POSTGRES_URL` 双引擎模式、MCP/ workflow 入口证据测试。
- 不变更:默认开关维持关闭;独立 workflow 的 Run/部署身份另开设计;A2A per-agent 映射、入口安装/升级矩阵不在本轮。
- 文档:G3 复核记录更新为逐入口决策清单;方案 step5d 剩余项按验收结果更新勾选与链接。
