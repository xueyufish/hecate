# Design: p1-audit-cost-a2a

## Context

- **#6**：`AuditMiddleware.dispatch`（`core/middleware/audit.py:88-104`）读 `request.state.auth_context`，无写入点。`AuditEvent` 已有 `metadata: dict` 与 `error_code` 字段，`AuditLogModel.metadata_` 为 JSONB——承载失败类型无需迁移。`audit-logs` 主 spec 的 "Unauthenticated request excluded from audit" 场景本身就把未认证请求记为 `api.unauthenticated`（与评审验收的"记录失败类型"兼容，是扩展而非冲突）。上一 change 的 `MCPAuthMiddleware` 已把 AuthContext 写入 `scope["state"]`——父应用上的 AuditMiddleware（BaseHTTPMiddleware 共享 scope dict）可读到它。
- **#7**：`LLMService.chat_stream` 签名已支持 `temperature/max_tokens/routing_config/timeout/num_retries`（`service.py:215-225`），适配器只传了 3 个参数。`chat`（非流式）已提取 usage（`service.py:200-203`），`chat_stream` 未回传 usage（LiteLLM 需要 `stream_options={"include_usage": True}`）。`_record_cost` 以 `token_count * 0.00001` 结算，`QuotaService.record_usage` 只收 `amount: float`（无 metadata 列）。适配器收到的 `config` 即 agent 的 `model_config_db`。
- **#8**：`a2a/signing.py` 具备完整 ES256 JWS 能力（`sign_agent_card` / `verify_agent_card_signature(card_data, public_key_jwk)` / `generate_jwks`）；`discovery.py` 的验签分支是 TODO 警告。配置已有服务端签名项（`A2A_SIGNING_ENABLED/KEY_PATH/JWKS_CACHE_TTL`），无客户端受信密钥源。`discover_agent_card` 当前无生产调用方（仅 client 包 re-export）——接线风险低。`AgentCard`（`a2a/types.py`）无已验证标记字段。

## Goals / Non-Goals

**Goals:**

- 审计事件有真实操作者（REST + MCP），认证失败有类型、无凭据。
- 模型限制参数到达 provider；用量核算分块不变、分项、可标记估算；纯工具调用非零。
- A2A 客户端验签失败关闭；`AgentCard.verified` 区分已验证/未验证。

**Non-Goals:**

- 配额预留-结算-失败对账（QuotaService 设计工作，另行立项）——本批只修核算准确性。
- 按模型定价表计费（`model_pricing` 已有表，接入属成本域后续 change；本批保持固定单价，但单价的输入从"流片段估算"变为"usage/整体估算"）。
- A2A 服务端认证接线（A2A server 目前无认证依赖；其身份映射等服务端认证落地时一并做）。
- `audit-logs` 既有 requirement/model 不动（metadata 复用，无迁移）。
- `chat_stream` 的 usage 分项中缓存 token 仅在 provider 提供时透传，不做缓存定价策略。

## Decisions

### D1: 身份写入放在 `get_auth_context` 依赖内，而非中间件重复认证

`get_auth_context` 增加 `request: Request` 参数，认证成功后 `request.state.auth_context = ctx`。理由：依赖是认证解析的唯一入口，中间件无需二次解析；BaseHTTPMiddleware 与下游共享 scope dict，写入对中间件可见。`authenticate_bearer` 保持纯函数（无 Request 依赖），MCP 中间件已自行写 scope state。失败类型：`get_auth_context` 在 401 前置 `request.state.auth_failure = "invalid_credentials"`（缺失凭据为 `"missing_credentials"`）；`MCPAuthMiddleware` 的 401 路径同样写 `scope["state"]["auth_failure"]`。`AuditMiddleware` 把 `auth_context.auth_method` 与 `auth_failure` 写进事件的 `metadata`——不记录 token 本体。备选（否决）：中间件内自行调用 provider 链——双倍解析且与依赖结果可能不一致。

### D2: 参数透传用显式白名单而非 `**config`

适配器构造 `chat_stream(messages=..., model=..., tools=..., temperature=config.get("temperature"), max_tokens=config.get("max_tokens"), timeout=config.get("timeout"), num_retries=config.get("num_retries"))`。白名单防止 agent `model_config_db` 里的任意键（如路由配置）以未知 kwargs 形态打进 provider。

### D3: usage 回传 + 整体估算两级核算

1. `hecate_llm.chat_stream`：给 LiteLLM 传 `stream_options={"include_usage": True}`，在流终止时 yield `{"content": None, "usage": {...}}` 终止块（LiteLLM 不支持时该块缺省——两级回退的"provider usage 可用"分支）。非流式 `chat` 已有 usage，不动。
2. 适配器：消费终止块 usage → 分项记录（prompt/completion/cached）；无 usage → 在流结束后基于**累计输出全文**与输入消息整体估算（`ceil(len/4)`，各算一次）——分块不变性由此保证（旧实现是逐片段 `//4` 累加，小片段被取整吞掉）。估算路径在 logger.info 与返回给引擎的 telemetry dict 中带 `estimated=true`。
3. `_record_cost` 签名扩展为接收 `usage: dict`（含 `estimated` 键），amount 计算改为 `(prompt + completion) * 0.00001`，`record_usage` 调用不变。
4. 纯工具调用：输出文本为空但输入消息存在 → 输入侧估算非零，满足"纯工具调用也有用量"。

### D4: A2A 受信密钥源 = 配置内联 JWKS 或文件路径

新增 `A2A_TRUSTED_JWKS: str = ""`（JSON 内联，以 `{` 开头）或 `A2A_TRUSTED_JWKS_PATH: str = ""`（文件路径）；两者皆空且 `verify_signature=True` 时直接 `ValueError`（fail-closed：没有信任根就不该开验签）。发现流程：`signatures` 非空 → 逐签名取 `header.kid` 在受信 JWKS 中找 key → `verify_agent_card_signature` 校验；任何失败抛 `ValueError`（含原因），不再返回卡片。`AgentCard` 增加 `verified: bool = False` 字段，验签成功置 True。备选（否决）：每次发现实时拉取远端 JWKS——引入网络信任根问题（拉谁的 JWKS？），首版用本地受信集合，远端 JWKS 留给后续。

### D5: 功能目录标注

`docs/features/feature-catalog.md` 中 A2A Signed Cards 相关行按"服务端签名（已交付）/客户端验签（本 change 交付，受信 JWKS 本地源）"分别标注状态，遵循 writing-style（不带具体数字/日期）。

## Risks / Trade-offs

- [usage 分块行为差异] 部分 provider/代理不支持 `stream_options`，usage 终止块可能缺席 → Mitigation：估算回退路径保证核算不断；`estimated` 标记保证可区分。
- [估算口径变化] 从"逐片段 //4"改为"整体 ceil(len/4)"会使存量账面用量上升（此前系统性低估，含计零片段）→ Mitigation：PR 说明；这是修复而非回归，配额告警阈值由运维按新口径复核。
- [A2A 验签的部署前置] `verify_signature=True` 的调用方需先配置受信 JWKS → Mitigation：配置缺失时错误信息明确指出需设置 `A2A_TRUSTED_JWKS`；当前无生产调用方，风险窗口为零。
- [审计 metadata 体积] 每事件新增 1-2 个短字符串键 → JSONB 开销可忽略。

## Migration Plan

1. 部署后审计日志即开始携带真实 `user_id`；存量记录保持原样（append-only，不回填）。
2. 需要精确计费的环境确认 provider 支持 usage 回传（不支持则走估算并观察 `estimated` 标记比例）。
3. 启用 A2A 客户端验签的部署先配置 `A2A_TRUSTED_JWKS(_PATH)`，再对远端卡片调用 `verify_signature=True`。
4. 回滚 = revert（无迁移）。

## Open Questions

无——#7 的配额预留/结算已明确划出本批（用户确认的范围决策）。
