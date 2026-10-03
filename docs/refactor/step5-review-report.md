# step5 复核与修正记录

## 复核范围与证据

本次复核 step5 全部五个交付：`runtime-shared-assembly`（PR #206）、`runtime-standalone-distribution`（#207）、`hecate-runner-preview`（#208）、`platform-entry-migration`（#209）、`entry-tail-migration`（#210），基线为 `9f9d367`。依据为[演进方案](enterprise-agent-platform-evolution-plan.md) step5 与 G1—G5 门禁、[platform-entry-execution 规格](../specs/platform-entry-execution/spec.md)、[unified-chat-execution 规格](../specs/unified-chat-execution/spec.md)、五个 change 的归档工件与实际代码。

证据类型是静态代码走查、规格逐条对照与真实入口回归。修正由 [step5-review-hardening](../changes/step5-review-hardening/proposal.md) 承接。既有交付事实（5a/5b/5c 勾选、SC01/SC02 翻转、双 wheel CI）经复核属实，本次修正不改变其结论。

## 发现与处置

| 发现 | 证据 | 修正 |
|---|---|---|
| 入口事件存储三实例分片 | `wiring.attach_state_stores` 写 `app.state`；`im_entry._shared_event_store` 与 `mcp/server._shared_stores` 各持模块级单例；默认 `EVENT_STORE_BACKEND=memory` 下跨入口事件互不可见；HTTP DI 兜底每请求新建实例，无 lifespan 时连 HTTP 自身也碎片化 | 新增 `core/composition/entry_assembly.py`：lifespan 注册 + 进程级唯一访问器（事件与 session-state 各一）；DI 兜底改走共享访问器；新增跨入口共享实例行为测试（`tests/test_execution/test_entry_assembly.py`） |
| 入口装配块四处复制、跨入口导入私有函数 | a2a executor、scheduling executors、im_entry、mcp server 均 `from hecate.channel.api.v1.chat import _build_tool_registry, _load_agent_tools` | 工具装配函数迁至 `entry_assembly`（公开名），chat 保留薄转发别名；分层测试新增"禁自带存储单例 + 禁跨入口私有装配导入"两个扫描及注入负例（`tests/test_layering_entry_imports.py`） |
| A2A 全局选取第一个非删除 agent | `executor.py` 原文 `select(AgentModel).where(deleted.is_(False)).limit(1)`，无 workspace 过滤；方案注记仅称"per-agent 身份缺口"，低估了跨租户执行面 | 新增 `A2A_AGENT_WORKSPACE_ID` 作用域：未配置、非法、零命中、多命中一律协议内 FAILED 拒绝，不存在全局回退；文档（how-to、env-vars）标注行为变化与迁移动作 |
| A2A 失败响应泄露内部错误 | `f"Execution failed: {e!s}"` 将异常文本直返协议客户端 | 统一稳定文案 `Execution failed`，内部细节只进服务端日志；测试断言不含异常文本与路径 |
| G3 断线/恢复无真实入口证据 | 门禁要求"断线/恢复"，既有 5 项 G3 测试未覆盖；且发现流式路径从不持久化会话状态：`execute` 流式分支未传 `org_id`/`user_id`，chat 入口也未传 `user_id`，`_persist_session_state` 因租户键为 None 静默跳过 | 补传 user_id（chat 双模式）与 org/user/agent_id（execution_service 流式分支）；新增两段式断线/恢复测试：流式中途断连后快照持久化、恢复后不重复派发工具（从恢复的 tool result 直接作答）、全库 TOOL_CALL/TOOL_RESULT 配对完整 |

## 剩余登记项处置映射

| 登记项 | 处置 | 归属 |
|---|---|---|
| 调度器 `manager._execute_task` 接线 executor registry | 保持未勾选；真实调度执行属持久化任务体系 | step6 |
| 同步/流式 API 成为 Task/Run 订阅视图；事件按 run 引用+游标读取 | 保持注记；依赖 step6 事件持久化存储 | step6 |
| Pregel 错误/产物映射尾项（事件映射层已落，错误/产物待续） | 保持注记 | step6 |
| A2A per-caller/per-agent 身份（调用方→agent 映射） | 过渡契约已落（workspace 作用域唯一解析+拒绝）；完整方案待 A2A 协议鉴权设计 | 后续条目（建议 step16 认证前完成） |
| 定时任务 WorkflowExecutor 经 studio `WorkflowTestRunner` 测试入口 | 保持显式绕过登记，规格已钉住"未迁移链显式登记"场景 | 后续条目 |

## step5 完整性核对

| step5 要求 | 交付 / 结论 |
|---|---|
| 5a 共享装配行为一致 | `execution_assembly.py` + `HecateExecutionBackend`，契约测试三实现参数化（#206 复核属实） |
| 5b 干净安装与依赖闭包 | hecate-runtime 双 wheel CI + 冒烟，硬依赖降级 extras（#207 复核属实） |
| 5c 无控制面冷启动 + 只读 SC 场景 | SC01/SC02 `implemented`，runner 启动期拒绝 write/approval 工具（#208 复核属实） |
| 5d 入口迁移 + G3 | 六条链经入口服务，`llm_service.chat` 直连清除；G3 真实入口测试含断线/恢复（#209/#210 + 本次修正） |
| 升级支持窗口矩阵 | `docs/design/execution-stack-compatibility-matrix.md` + CI 校验脚本（#209 复核属实） |

## 边界与限制

- **memory 后端仍是进程内状态**：单实例收敛修复跨入口可见性；多 worker 部署下事件与会话状态仍不共享，属既有事实，由 `EVENT_STORE_BACKEND=postgres` / 分布式 session-state store 解决。本复核未扩大战线。
- **静态分层扫描存在绕过面**（动态 import、exec）：与既有分层测试同限，不追求完备。
- **A2A 拒绝语义是破坏性行为变化**：升级方必须显式配置 `A2A_AGENT_WORKSPACE_ID`，否则 A2A 任务从"总能执行第一个 agent"变为协议内失败。迁移动作见 [enable-a2a-server](../how-to/enable-a2a-server.md)。
- **断连测试经 ASGITransport**：不模拟 TCP 层断连，断连点为 SSE 消费方主动放弃；`http.disconnect` 路径与真实网络断连的等价性属 step6 持久化任务体系的验证范围。
- 本报告的静态核对与测试证据不构成生产认证：SC01—SC10 的故障集组合认证仍归 step16，生产权限归 step7。

## 验证记录

- 新增/受影响测试：`test_entry_assembly`（8）、`test_layering_entry_imports`（扩展 4 断言 + 2 负例）、`test_executor_entry`（4 新用例）、`test_chat_engine_g3_entry`（断线/恢复）、`test_agent_chat_entry_parity` 夹具迁移——全部通过；`test_services/test_workflow` 回归 71 项通过。
- `ruff check` / `ruff format --check` / `mypy src/` 零错误；`openspec validate step5-review-hardening --strict` 与 `openspec validate --specs` 通过。
- 修复登记：流式路径会话状态从不持久化（user_id/org_id 未传）作为 G3 断线/恢复证据的一部分一并交付，非独立缺陷单。
