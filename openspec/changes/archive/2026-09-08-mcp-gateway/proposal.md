# 5.4a MCP Gateway

## Why

Hecate 定位"MCP-first Agent 平台"，但对外工具面是割裂的：`/mcp` 只暴露约 15 个 first-party 硬编码工具（无租户隔离、有 key 即全量可见），外部 MCP 工具只能被内部 agent 消费，REST API 完全没有进入 MCP 世界的通道。AgentArts 将网关做成独立产品卡验证了市场价值；roadmap 将 5.4a 列为 Sprint 8 Opening Queue 首位（底座 ✅、阻塞最浅）。探索已确认**无外部强依赖**：workspace 级凭证复用现有 `ApiKeyModel`，不需要等待 11.16/11.17。决策依据见 [docs/research/2026-09-mcp-gateway-explore-decisions.md](../../../docs/research/2026-09-mcp-gateway-explore-decisions.md)（D1–D10）。

## What Changes

- **统一端点（D1，渐进式）**：网关是 `/mcp` 的原地进化——first-party 硬编码工具保持不动；联邦工具在同一 FastMCP 实例上动态追加；对任意调用者，工具目录 = first-party ∪ 联邦，按身份过滤
- **REST→MCP 转换（D2，混合模型）**：OpenAPI spec 导入时为每个 operation 落一行 ToolModel（`source="rest"`，policy 可寻址、可绑 agent）；执行是 spec 驱动的 virtual HTTP 调用，零包装代码
- **MCP 边界 authz（D3）**：调用者身份 = workspace-scoped API key（复用 `ApiKeyModel` / `ApiKeyService.verify_key()`）；`tools/list` 经 policy pipeline 按调用上下文过滤（HIDE 语义），`tools/call` 走完整决策管道（DENY 短路）——policy pipeline 由此获得第一个生产消费者
- **Target 注册（D4/D8）**：REST / MCP target 集中注册（endpoint、spec、静态凭证、出网基线）；凭证网关持有、API 响应脱敏；HTTPS 强制、non-loopback 拒绝
- **统一命名（D6）**：联邦工具统一 `<target>__<tool>` 命名（沿用 5.5c 先例），`tools/list` 去重
- **统一日志**：网关转发路径接入既有 otel trace bridge / tool execution analytics，记录调用者 workspace

**明确不做**（防范围蔓延，proposal→tasks 全程携带）：OAuth 凭证流程与轮换（→5.8）；SSE/streaming/webhook 源（→trigger 类特性，1.1.25）；Per-Token-Type 认证管线（→11.16）；Two-Tier Identity（→11.17）；`source="custom"` 执行实现；first-party 工具注册化；后端 MCP server 的 resources/prompts 联邦（v1 只联邦 tools）；1.3.15a sandbox 场景的 MCP 访问（同名不同物，D10）。

## Capabilities

### New Capabilities

- `mcp-gateway`: MCP 网关——REST/OpenAPI → MCP 工具转换（导入投影 + virtual 执行）、外部 MCP server 工具联邦、target 注册与静态凭证 brokering、MCP 协议边界的 workspace 级 authz（caller-scoped `tools/list` / `tools/call`）、`<target>__<tool>` 命名、网关转发路径的统一日志

### Modified Capabilities

- `mcp-server`: `/mcp` 端点行为变化——工具目录从"固定 first-party 全集"变为"first-party ∪ 联邦、按调用者身份过滤"；认证从仅全局 env API key 扩展为同时接受 DB-backed workspace-scoped key 并解析 workspace 身份
- `tool-registry`: 路由需求新增 `source="rest"` 分支——rest 工具经 spec 驱动的 HTTP 执行器执行（内部 agent 路径与网关路径共用同一执行语义）

## Impact

- **新增**：`src/hecate/tools/gateway/`（target 注册、OpenAPI 转换器、MCP 边界 authz 过滤器、REST 执行器）；新数据模型（target 存储形态——新表 vs 复用 PluginModel——是 design 待决点①）；Alembic 迁移
- **修改**：`src/hecate/tools/mcp/server.py`（动态追加联邦工具 + caller-scoped 目录）、`src/hecate/tools/mcp/auth.py`（DB-backed key 解析）、`src/hecate/tools/tool/registry.py`（rest 分支）、`main.py`（挂载不变，构造注入网关组件）
- **依赖**：无新增第三方依赖（OpenAPI 解析用 stdlib/既有 pydantic 能力，或最小引入 `pyyaml`——design 确认）
- **配置**：`GATEWAY_ENABLED`（默认 off）等新设置项进 `core/config.py` + `.env.example`
- **测试**：`tests/test_tools/test_gateway/`（转换器、authz 过滤、REST 执行器、命名去重）+ 修改能力的 delta 验证
- **风险**：`/mcp` 是既有对外面——first-party 工具行为必须零变化（D1 渐进式正是为此）；catalog 语义变化（目录随身份变化）对老客户端透明（只减不增）
