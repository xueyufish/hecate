# Tasks

## 6. Codex 复核修正

- [x] 6.1 快照在 session lock 内读取并合并，保留 intent/event_position 等全部非 agent 字段；读失败不写空快照，补并发与读故障反例。
- [x] 6.2 收紧真实 TCP 验收：持久事件/公开终态/停止类别及真实回执内容断言；隔离全局装配并可靠关闭 socket/server，PG 不回退内存故障存储。
- [x] 6.3 补齐 MCP 注册工具调用的多轮、策略/审批拒绝、取消恢复及 Workflow 子任务多轮/拒绝证据；接入 PG CI。
- [x] 6.4 运行受影响回归与静态检查，按真实证据更新决策记录、verification 和 review。

## 0. 范围扩展(用户确认,选项 C):断连恢复链产品修复

实现中发现恢复链三处产品缺陷,经用户确认纳入本 change 一并修复(否则任务 2 的验收标准不成立):

- [x] 0.1 断连时的 session-state 保存无取消 shield:取消展开在第一个 await 再抛,checkpoint 静默丢失;改为 `CancelScope(shield=True)` 保护(`_stream_execute`)。
- [x] 0.2 保存改为合并而非覆盖:agent_state 快照整对象覆盖会清掉引擎 materializer 按超步存的 channel_state(恢复源);合并保留 channel_state 与 metadata(`_persist_session_state`)。
- [x] 0.3 回执真实内容从会话日志回填:恢复决策在 SUCCEEDED 且通道历史无结果时,从事件日志 messages 通道的 CHANNEL_WRITE(携带完整工具结果消息)恢复真实内容(`tool_worker._log_recorded_content`);占位标记只留给真正不可恢复的情形,跨轮续接不再依赖标记推理。

## 1. 回执恢复行为修复(先行:断连断言依赖本组)

- [x] 1.1 `recovery_decision` SUCCEEDED-无内容分支按副作用类别分流:只读返回 None(允许安全重执行),写类返回带 `reconciliation_required` 类别的停止决策;`_checked_recovery_result` 将其升级为 `ExecutionStoppedError`,占位文本不再作为工具消息进入模型;`_stop_category` 词汇表登记新类别。
- [x] 1.2 单测:内容已持久→真实回填且零重复执行;内容缺失+写类→对账停止且 Run 终态/信封类别可查;内容缺失+只读→重执行恰好一次获得真实结果;`[conflict]`/`[needs review]` 既有语义回归不变。

## 2. 真实 TCP 中途断连与恢复

- [x] 2.1 真实传输 harness:in-thread uvicorn serve 平台 app(随机端口),依赖覆盖与还原沿用 #257 装配;确定性 provider gate 保证断开前已有成功的工具结果;httpx 实连接 SSE 中途断开。
- [x] 2.2 断连恢复验收(SQLite + PG 参数化):断开后新会话查询事件/终态/投影;续轮恢复零重复副作用;回执为持久真实内容回填(依赖 1.x);PG 变体未配置 URL 时显式 skip。
- [x] 2.3 恢复中内容不可恢复的注入用例:断连 + 内容丢失(清空账本内容)后恢复,断言写类对账停止、无第二次副作用、模型未收到占位文本。

## 3. MCP 入口引擎路径证据

- [x] 3.1 经真实 MCP `agent_chat` 会话入口,以确定性双打执行多轮工具、策略拒绝、取消恢复;断言工具事件序列与 HTTP 入口等价(配对、审批记录、结束原因),差异仅协议层。

## 4. Workflow 子任务入口引擎路径证据

- [x] 4.1 经平台任务控制面提交 `workflow.steps` 声明任务(子任务为有部署身份的 Agent),执行多轮工具与拒绝场景;断言子任务工具事件、终态与父任务收敛经持久事实可查,执行语义与 HTTP 一致;独立 workflow Run 身份不在此列。

## 5. 决策记录、文档与验证收尾

- [x] 5.1 更新 `docs/refactor/step5d-g3-default-enable-review.md`:逐入口证据表(HTTP 真实断连/MCP/workflow)登记通过、失败与未支持项及测试引用;结论维持默认关闭,如实登记翻转前置剩余缺口。
- [x] 5.2 更新演进方案:step5d G3 剩余项按实际验收结果处理(全部证据齐备才勾选,否则保留并登记剩余缺口);顺带把 345 行复核链接改为归档后路径。
- [x] 5.3 全量受影响回归(channel/execution/runtime/A2A/scheduling/workflow/MCP/S12 + hecate-runtime/hecate-durable 包)+ ruff + mypy;记录实际 skip 范围;`openspec validate --strict` 通过,完成 verification 记录。
