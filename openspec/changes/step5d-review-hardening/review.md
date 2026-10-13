# step5d 代码与证据复核

## 基线与范围

复核 origin/main `ed91585b16c88113cb9448a50b0b8c2d6ddddb96`（#250）与 `73382ca72310ec75d709b0a8219947ce9236b121`（#249），按演进方案 step5d 的错误/产物、Task/Run、事件、定时入口、A2A 与 G3 要求逐项追踪。用户选择先修确定性缺陷，独立 workflow 的部署/Run 身份保留为未完成，不引入强制执行 Agent 配置。

## 发现与修正

| 原问题 | 修正 | 验证 |
|---|---|---|
| 定时 workflow 丢弃调用方 workspace，直接装载目标 workflow | 在装配/派发前按调度 workspace 拒绝外部目标，失败与缺失目标一致 | `test_scheduled_workflow_rejects_foreign_workspace_before_dispatch` |
| input_data 序列化成 user 文本，业务 channel 和 messages 丢失 | 平台 workflow adapter 接受显式 input_data，保留业务 channel，拒绝 sys/内部身份覆盖 | `test_real_scheduled_workflow_preserves_business_channels` 用真实图的条件分支；`test_workflow_input_cannot_override_runtime_context` |
| 定时 workflow 传 tool_registry=None，工具节点缺实际执行面 | 复用共享、workspace scoped tool registry | 执行器回归；工具治理完整认证保持开放 |
| 非流式停止转换新建异常，丢失 platform_correlation | 转换保留引用；原异常在缺 Run 时也带 missing 标记 | `test_non_streaming_cancel_preserves_correlation`：完整/缺部署两种状态 |
| 引擎返回后先记 succeeded，再在 HTTP 校验/修复输出 | 把最终结果处理纳入入口 execute 的异常/成功边界 | `test_output_validation_controls_run_projection`：流式/非流式 × 修复成功/失败；工具恰好一次 |
| 结构化流式包装丢失同步结果的 hecate_correlation | 输出结构化答案前附关联帧 | 同上真实 HTTP 回归 |
| 关联首帧只有 choices，缺少标准 chunk 的 id/object/created/model | 使用相同的 OpenAI chunk schema 构造关联帧 | 真实 HTTP 流式回归断言标准字段 |
| 流式耗尽写成功但 summary=None；只有 HTTP mapper 负责信封落库 | 共享入口服务逐事件落库并保存有界最终摘要；HTTP mapper 只渲染 | `test_streamed_run_has_summary_and_persisted_envelopes`、`test_non_http_stream_persists_envelopes_and_bounded_summary` |
| 无 Run 时先 return，未 pop 内部 _engine_events | 先剥离再判断是否可持久化；Task-only 状态明确 missing | 缺关联服务测试及真实定时 workflow 回归 |
| 安装矩阵用功能单测宣称受支持，且测试路径/类名错误 | 安装/升级状态改为未认证，保留功能证据，修正引用 | 路径/符号与实际测试核对 |
| G3 client.stream 基于 ASGITransport，在首段读取前服务端已耗尽 | 证据改为完成后的快照/续轮；中途真实断连仍未认证 | httpx ASGITransport 行为与测试调用链核对 |
| 主规格保留 delta 标题，后续要求不可解析/归档 | 去掉主规格中的 ADDED Requirements 标题，原要求内容保留 | 主规格 strict validate |
| A2A 默认部署解析已变化，配置说明仍称必须只有一个 Agent | 同步 docstring、how-to、env-vars，说明多个默认 Agent 仍拒绝 | A2A 回归及文档核对 |
| 宽回归中 callback 测试后台 wake 越过执行桩 teardown，干扰后续共享数据库并调用真实 LLM | callback fixture 在撤桩前等待本轮后台派发；不改生产代码 | 部署模型单测及其前序 channel 序列独立重跑通过；完整宽回归再次核验 |
| studio timing 测试要求 duration_ms > 0，但整数毫秒对正常亚毫秒运行可为零 | 用局部确定性时钟验证总耗时与逐节点毫秒换算，不依赖机器快慢 | studio 测试运行器回归 |

## 当前保留缺口

- **独立 workflow 的 Run/部署身份**：RunModel.deployment_id 非空且引用 AgentDeployment，当前定时 workflow 未选 Agent。真实执行仅创建 Task，缺少 Run 和按 Run 键的平台信封。未创建虚拟身份；演进计划保持未完成。
- **失败登记持久性**：savepoint 仍位于调用方事务中。未处理内部异常触发外层 rollback 时，Task/Run 和投影可能被一并回滚。本轮不擅自提交调用方业务事务。
- **同步失败信封与产物**：同步排空异常时事件集合尚未返回，只有引擎日志；停止错误未形成完整终态信封。声明的产物引用也未建立完整映射。本轮不把成功内容摘要当成全部产物契约。
- **G3 与安装认证**：真实中途断连、MCP/workflow 的完整引擎入口证据、逐入口干净安装/升级、PostgreSQL 入口信封端到端均未补齐；全局默认继续关闭。确定性 provider 不证明真实模型质量。
- **定时 workflow 治理**：共享工具面已接入，独立 workflow 的身份与治理完整认证仍开放。

## 验证

- 原交付定向基线：50 passed。
- 入口/服务定向回归：60 passed；最终协议/状态摘要/callback 回归：34 passed。
- ruff check `src/hecate/ tests/`、ruff format --check `src/ tests/`：通过。
- mypy `src/`：634 source files，零错误。
- OpenSpec change 与 platform-entry-execution 主规格 strict validate：通过。
- 最终宽回归：798 passed、30 skipped、0 failed。范围为 channel、execution、A2A、scheduling、workflow、MCP agent_chat、IM provider/routing 和领域/入口分层。
- 评测消费与 HTTP/事件装配追加回归：278 passed。与宽回归存在重叠，不累计计数。
- 条件跳过：JS 试点依赖未安装（13）、无 PostgreSQL 并发环境（5）、由专门 binding 测试覆盖的多 schema 样例（12）；不当作已验证。保留收集警告、Starlette 弃用和测试桩未 await coroutine 警告，共 11 项；没有执行失败。

可复现宽回归命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_channel/ tests/test_execution/ tests/test_a2a/ tests/test_services/test_scheduling/ tests/test_services/test_workflow/ tests/test_mcp/test_agent_chat_entry_parity.py tests/test_core/test_im_providers.py tests/test_gateway/test_im_routing.py tests/test_layering_domain.py tests/test_layering_entry_imports.py -q -rs
```

环境使用 worktree 的 Python 3.12 venv；已安装仓库声明的 Runner、Feishu、Slack workspace 包，以消除收集缺依赖。首次宽回归失败已调查并修正 callback fixture 竞态与 timing 测试的不确定性，最终重新执行全范围通过。

GitHub Actions 后续发现 S12 流式场景解析了入口新增的 metadata-only correlation frame；该帧合法地带有 `choices=[]`，旧断言直接访问 `choices[0]`。测试现仅消费有内容的 choice 帧，并在 tasks.md 登记为 3.3。

修正位于 `feat/step5d-review-hardening`，主修正 commit 已推送；OpenSpec change 尚未归档。
