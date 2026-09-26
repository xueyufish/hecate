# Tasks: p1-audit-cost-a2a

## 1. P0 承接：#6 审计身份接通

- [x] 1.1 `core/deps_workspace.py`：`get_auth_context` 增加 `request: Request` 参数，认证成功写 `request.state.auth_context = ctx`；401 前按失败种类写 `request.state.auth_failure`（缺失凭据 `missing_credentials`、其余 `invalid_credentials`）。验证：`ruff` + `mypy` 通过。
- [x] 1.2 `tools/mcp/auth_middleware.py`：401 返回前写 `scope["state"]["auth_failure"]`。验证：模块自查。
- [x] 1.3 `core/middleware/audit.py`：事件 `metadata` 写入 `auth_method`（有身份时）与 `auth_failure`（失败时）；不记录任何凭据原文。验证：单测（1.4）。
- [x] 1.4 测试：JWT 与 DB API key 各发起一次管理操作 → 审计事件（经队列捕获或 writer stub）`user_id/org_id` 正确且 `metadata.auth_method` 正确；无效凭据请求 → 事件含 `auth_failure` 且全文不含凭据字符串；MCP 401 路径同样可观测。验证：新增用例全绿。

## 2. #7 参数透传与用量核算

- [x] 2.1 `packages/hecate-llm/.../service.py`：`chat_stream` 传入 `stream_options={"include_usage": True}` 并在流终止 yield `{"content": None, "usage": {...}}` 终止块（provider 不支持时块缺省）。验证：包内单测。
- [x] 2.2 `core/composition/runtime_port_adapter.py`：`llm_invoke`/`llm_invoke_structured` 按白名单透传 `temperature/max_tokens/timeout/num_retries`；消费 usage 终止块；无 usage 时以累计全文 + 输入整体估算（`ceil(len/4)`）一次性结算并携带 `estimated=true`；`_record_cost` 改收 usage dict。验证：`mypy` 通过。
- [x] 2.3 测试：mock LLM 流——大/小片段两 streams 内容相同 → 用量一致（分块不变）；usage 终止块 → 分项采用 usage；纯 tool_calls 流 → 输入侧用量非零；temperature/max_tokens 到达 chat_stream 调用参数。验证：新增用例全绿。

## 3. #8 A2A 验签失败关闭

- [x] 3.1 `core/config.py`：新增 `A2A_TRUSTED_JWKS` / `A2A_TRUSTED_JWKS_PATH`（+ 解析 helper，两处皆空返回 None）。验证：单测。
- [x] 3.2 `channel/a2a/types.py`：`AgentCard` 增加 `verified: bool = False`。`channel/a2a/client/discovery.py`：`verify_signature=True` 时——无受信 JWKS、卡片无 `signatures`、kid 不在受信集合、验签失败 → `ValueError`（含原因，不返回卡片）；成功 → `verified=True` 返回。删除 TODO 警告分支。验证：`rg "not yet implemented" src/hecate/channel/a2a/` 无结果。
- [x] 3.3 测试：无签名拒绝 / 篡改拒绝（用 `sign_agent_card` 签真卡后改内容）/ 未知签发者拒绝 / 正确签名通过且 `verified=True` / `verify_signature=False` 返回 `verified=False` / 受信 JWKS 未配置时 fail-closed。验证：新增用例全绿。

## 4. 文档与门禁

- [x] 4.1 `docs/features/feature-catalog.md`：A2A Signed Cards 相关条目按服务端签名/客户端验签分别标注状态；`.env.example` 补 `A2A_TRUSTED_JWKS(_PATH)` 注释。验证：文档自查。
- [x] 4.2 门禁：`ruff check src/hecate/ packages/ tests/`、`ruff format --check`、`mypy src/`、`python -m pytest tests/test_api tests/test_services tests/test_enterprise packages -q`（packages 目录含 hecate_llm 测试则纳入，否则按实际布局调整）。验证：全部 0 错误。
- [ ] 4.3 `openspec validate p1-audit-cost-a2a` 通过。
