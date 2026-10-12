# Design: g3-entry-evidence

## D1 路径强制复用现有 fixture,不新增开关面

`tests/test_channel/test_chat_engine_g3_entry.py` 的 autouse `engine_path` fixture(全局开关置开)已使全文件默认走引擎路径,本 change 全部新增证据用例直接受益。关态钉住用例不关闭 autouse,而是新增显式 `direct_path` fixture **依赖** `engine_path`(保证 monkeypatch 覆盖顺序)后再把 `CHAT_TOOL_LOOP_ENGINE_ENABLED` 置回 False——路由解析、workspace 覆盖、会话亲缘机制零改动。

## D2 取消证据的注入与断言点

复用 step5e 已交付的链路:入口层 `_disconnect_check(http_request)` 作为 `cancel_check` 下传,引擎路径经 `_CheckedModelService` 在每个模型边界检查。测试采用与既有断连用例同构的流式中断模式:客户端在答案流出前 abort 连接。断言点按可观察事实选:

- 进行中的工具轮恰好完成一次(服务端不受断连影响);
- 会话快照持久(InMemory store 轮询等待,复用既有模式);
- 恢复后同会话工具不重复执行;
- 取消终止类别以 step5e 终止事件(`execution_stopped`/`cancelled`)呈现——连接已断时客户端不可达,改由事件/恢复行为佐证终止路径被走到,不断言客户端必然收到终止帧。

不新增取消 API;断连即取消,与 step5e 语义一致。

## D3 跨请求多轮连续性

同一 `session_id` 发起第 1、2 轮请求(引擎路径)。stub LLM 脚本按"消息历史中是否已见前轮助手回答"分支,使断言落在真实传递的历史上:第 2 轮模型消息包含第 1 轮答案;会话路径亲缘保持(无二次解析);第 1 轮已决工具回执在第 2 轮不重复执行(step5e 重放身份 + 回执先回放的既有语义);`validate_tool_pairing` 跨两轮完整。历史载体为引擎会话 checkpoint(`session_state_store`),与既有断连恢复用例同构。

## D4 确定性拒绝的注入

不走审批回调:经 guardrail bundle 的 `tool_rules` 注入一条确定性 DENY 规则(工具名匹配即拒)。断言:拒绝产生可查询记录(ToolDecision 审计事件/拒绝追踪),对话不终止(stub LLM 收到拒绝结果后给出最终回答),响应语义与直连等价(拒绝结果以工具错误消息呈现,而非 needs_review 类终止——确定性拒绝不是未知结果)。若证据暴露引擎与直连呈现不等价,按 Proposal 边界做最小修复并登记。

## D5 默认启用决策记录

新增 `docs/refactor/step5d-g3-default-enable-review.md`,内容:

1. **逐入口证据清单**:HTTP 入口各项(多轮流式/非流式、审批拒绝、断连恢复、取消、多轮连续性、确定性拒绝、关态钉住)指向具体测试;MCP `agent_chat` 与 workflow 入口的引擎路径证据如实标注缺口。
2. **放量审计核对**:workspace 覆盖变更写 `CHAT_ENGINE_ROLLOUT_CHANGED` 放量记录的既有证据引用。
3. **决策**:维持全局默认关闭;命名翻转前置条件(逐入口证据齐备 + 决策记录更新),不预设时间。
4. **旧循环限制显式保留**:直连循环不经引擎事件日志、无持久回放保证——在决策记录中成文,并与 `docs/design/execution-mechanism-matrix.md` 及 S12 场景交叉引用。

文档遵守 `docs/design/writing-style.md`(不用具体数字/日期作描述性标记)。

## D6 不改动清单

路由解析(`entry_routing.py`)、开关机制(feature flag + workspace 覆盖)、入口装配、step5e 停止语义/重放身份/结构化输出实现均不动。生产代码仅在证据测试暴露行为缺陷时做最小修复,并在 change 复核记录中登记缺陷与修复。

## Risks

- 取消断言的异步时序(引擎生成器在客户端 abort 后异步 finalize)——沿用既有断连用例的轮询等待模式,不引入 sleep 墙钟。
- 多轮历史依赖测试 `app.state.session_state_store` 的进程内持久——与既有恢复用例同构,不引入新存储。
- 关态钉住与 autouse fixture 的顺序——`direct_path` 显式依赖 `engine_path` 解决。
