# Proposal: p1-audit-cost-a2a

## Why

外部安全评审 P1 批次中的三个确定缺陷：

- **#6 HTTP 审计身份断线**：`AuditMiddleware`（`core/middleware/audit.py:90`）从 `request.state.auth_context` 读取身份，但全仓库没有任何写入点——所有 HTTP 审计记录都是匿名哨兵值（`user_id=0`），"有日志但不知道是谁"。`audit-logs` spec 早已假设中间件能拿到 AuthContext。
- **#7 模型参数丢失 + 成本失真**：`runtime_port_adapter.py` 的 `llm_invoke`/`llm_invoke_structured` 只向 `chat_stream` 传 `messages/model/tools`，`temperature`/`max_tokens`/`timeout`/`num_retries` 全部丢失（而 LLM 服务签名支持它们）；成本按每个流片段 `len(content)//4` 累加再乘固定单价——单字符片段计零、输入与工具参数不计、纯工具调用计零，分块方式直接改变总费用。
- **#8 A2A 验签形同虚设**：`a2a/client/discovery.py` 在 `verify_signature=True` 时仅打印警告并照常返回 Agent Card；仓库已有完整的 ES256 JWS 验签实现（`a2a/signing.py`）但未接线。要求验签的调用方得到的是"已验证"的假象。

## What Changes

- **#6**：`get_auth_context` 认证成功后写入 `request.state.auth_context`（中间件已读取该键，零中间件改动）；认证失败路径在 `request.state` 记录失败类型（如 `invalid_credentials`），审计事件经既有 `metadata` 字段承载——不记录原始凭据，无表结构变更。MCP 传输层（上一 change 的 `MCPAuthMiddleware`）已写入同一 `scope["state"]` 键，HTTP 审计中间件天然可见；A2A 服务端在认证接线时复用同一结构。
- **#7**：适配器建立类型化参数透传（`temperature`/`max_tokens`/`timeout`/`num_retries` 从 config 到 provider）；`hecate_llm.chat_stream` 启用 LiteLLM usage 回传并产出终止 usage 块；成本核算改为——provider usage 可用时按输入/输出/缓存分项计费，缺失时以"累计全文 `ceil(len/4)` + 输入估算"一次性结算（分块不变）并标记估算；纯工具调用也有输入侧用量。**配额预留/结算/失败对账不在本 change**（QuotaService 设计工作，另行立项）。
- **#8**：`discover_agent_card(verify_signature=True)` 接入 `verify_agent_card_signature` + 可信 JWKS 来源（新增 `A2A_TRUSTED_JWKS` 配置，JSON 内联或文件路径）：无签名、验签失败、未知签发者一律 `ValueError` 拒绝，不再返回卡片；`verify_signature=False` 保持现状并在返回值上区分"未验证"。功能目录 Signed Cards 状态按客户端/服务端分别标注。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `audit-logs`: MODIFIED "AuditMiddleware for automatic API capture"——中间件 SHALL 获得真实认证身份（依赖统一写入请求状态）；认证失败 SHALL 记录失败类型且不含原始凭据；保留原三个场景名。
- `model-cost-management`: ADDED "LLM 调用参数透传与用量核算" requirement——模型限制参数到达 provider、usage 分项核算、分块不变性、缺失 usage 标记估算、纯工具调用有用量。
- `a2a-protocol`: ADDED "客户端卡片验签失败关闭" requirement——强制验签时拒绝无签名/篡改/未知签发者，可信密钥来源可配置。

## Impact

- **代码**：`core/deps_workspace.py`（写入 request.state + 失败类型）、`core/middleware/audit.py`（失败类型进 metadata）、`core/composition/runtime_port_adapter.py`（参数透传 + usage 核算）、`packages/hecate-llm/.../service.py`（chat_stream usage 回传）、`channel/a2a/client/discovery.py`（验签接线）、`core/config.py`（`A2A_TRUSTED_JWKS`）、`docs/features/feature-catalog.md`（Signed Cards 状态标注）。
- **行为**：审计日志从此有真实操作者；agent 的 `temperature`/`max_tokens` 配置首次真正生效；成本随 usage 而非流片段波动；`verify_signature=True` 的 A2A 发现可能开始抛 `ValueError`（此前静默放行）。
- **无数据库迁移**（复用 AuditEvent.metadata / AuditLogModel.metadata_ JSONB）。
- **无 BREAKING API 形状变更**；`llm_invoke` 的 config 消费方（engine worker）不受影响（只增不删）。
