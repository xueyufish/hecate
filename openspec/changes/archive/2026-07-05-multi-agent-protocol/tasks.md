## 1. 统一技能注册表 (2.9)

- [x] 1.1 创建 `src/hecate/skill_registry/__init__.py`，包含公共导出
- [x] 1.2 创建 `src/hecate/skill_registry/types.py` — SkillRef 数据类、ResolvedSkill 数据类、SkillRefType 枚举（tool/skill/knowledge/workflow/agent/remote_agent）
- [x] 1.3 创建 `src/hecate/skill_registry/registry.py` — SkillRegistry 服务，包含 resolve()、invoke()、format_for_llm()
- [x] 1.4 实现工具解析（SkillRef → 按名称查找 ToolModel）
- [x] 1.5 实现技能解析（SkillRef → 按名称查找 SkillModel）
- [x] 1.6 实现知识库解析（SkillRef → 按 UUID 查找 KnowledgeBaseModel）
- [x] 1.7 实现工作流解析（SkillRef → 按 UUID 查找 WorkflowModel）
- [x] 1.8 实现 Agent 解析（SkillRef → 按 UUID 查找 AgentModel）
- [x] 1.9 实现 invoke() 路由到 EnginePort 方法（tool_execute、knowledge_query、workflow_execute、agent_execute）
- [x] 1.10 实现 format_for_llm()，为每种技能类型生成 XML/JSON 上下文块
- [x] 1.11 在 AgentModel 上添加 `skill_ids` JSON 字段（补充现有的 tools/skills/knowledge_base_ids）
- [x] 1.12 为 agent.skill_ids 列创建 Alembic 迁移
- [x] 1.13 将 SkillRegistry 添加到核心 DI 容器
- [x] 1.14 创建 `tests/test_skill_registry/test_resolve.py` — 测试每种 ref_type 的解析
- [x] 1.15 创建 `tests/test_skill_registry/test_invoke.py` — 测试 invoke 路由
- [x] 1.16 创建 `tests/test_skill_registry/test_format.py` — 测试 LLM 格式化
- [x] 1.17 创建 `tests/test_skill_registry/test_backward_compat.py` — 测试使用旧字段的 Agent 仍然工作

## 2. Agent-工作流互嵌 (2.9a)

- [x] 2.1 在 EnginePort 上添加 `workflow_execute()` 可选方法（默认 NotImplementedError）
- [x] 2.2 在 AgentExecutionPort 中实现 `workflow_execute()` — 解析工作流、编译图、通过 PregelRuntime 执行
- [x] 2.3 创建 `src/hecate/engine/workflow_tool.py` — WorkflowTool 类，包装 workflow_id（类似于 AgentTool）
- [x] 2.4 实现 WorkflowTool.name、.description、.execute()，含从 Start Node 变量生成的 JSON Schema
- [x] 2.5 通过上下文栈实现嵌套深度跟踪（max_depth=3，抛出 NestingDepthExceededError）
- [x] 2.6 验证 AgentWorker（NodeType.AGENT）正确传递通道快照并接收响应
- [x] 2.7 创建 `tests/test_engine/test_workflow_tool.py` — 测试 WorkflowTool 模式生成和执行
- [x] 2.8 创建 `tests/test_engine/test_nesting_depth.py` — 测试深度限制强制（深度 1、2、3 通过；深度 4 抛出）

## 3. A2A 协议基础 (2.10)

- [x] 3.1 在 pyproject.toml 的 `[tools]` 可选依赖组中添加 `a2a-sdk`
- [x] 3.2 在 `core/config.py` 中添加 A2A 配置（A2A_SERVER_ENABLED、A2A_SERVER_URL、A2A_AGENT_NAME、A2A_AUTH_MODE）
- [x] 3.3 创建 `src/hecate/a2a/__init__.py`，包含公共导出
- [x] 3.4 创建 `src/hecate/a2a/types.py` — A2A 协议对象的 Pydantic 模型（AgentCard、Task、TaskStatus、Artifact、Message、Part）
- [x] 3.5 创建 `src/hecate/a2a/server/card.py` — 从 Hecate 配置 + SkillRegistry 生成 AgentCard
- [x] 3.6 创建 `src/hecate/a2a/server/executor.py` — 桥接 A2A 请求到 EnginePort 的 AgentExecutor
- [x] 3.7 创建 `src/hecate/a2a/server/task_store.py` — 使用异步 SQLAlchemy 的 DatabaseTaskStore（a2a_tasks 表）
- [x] 3.8 创建 `src/hecate/a2a/server/streaming.py` — 用于 TaskStatusUpdateEvent / TaskArtifactUpdateEvent 的 SSE 事件发射器
- [x] 3.9 创建 `src/hecate/a2a/server/handler.py` — JSON-RPC 请求处理器（SendMessage、SendStreamingMessage、GetTask、CancelTask）
- [x] 3.10 创建 `src/hecate/a2a/server/auth.py` — 使用现有 AuthProviderABC 的 APIKey + HTTP Bearer 认证
- [x] 3.11 创建 `src/hecate/a2a/server/app.py` — FastAPI 路由器，包含 /.well-known/agent-card.json + /a2a/ JSON-RPC 端点
- [x] 3.12 在 `src/hecate/main.py` 中注册 A2A 服务器路由（条件判断 A2A_SERVER_ENABLED）
- [x] 3.13 创建 `src/hecate/models/a2a_task.py` — A2ATaskModel ORM（id、context_id、state、status_message、artifacts、history、workspace_id）
- [x] 3.14 为 a2a_tasks 表创建 Alembic 迁移
- [x] 3.15 创建 `tests/test_a2a/test_server/test_card.py` — 测试 AgentCard 生成
- [x] 3.16 创建 `tests/test_a2a/test_server/test_handler.py` — 测试 JSON-RPC 方法（send、get、cancel）
- [x] 3.17 创建 `tests/test_a2a/test_server/test_streaming.py` — 测试 SSE 事件格式
- [x] 3.18 创建 `tests/test_a2a/test_server/test_task_store.py` — 测试任务生命周期持久化
- [x] 3.19 创建 `tests/test_a2a/test_server/test_auth.py` — 测试 APIKey 和 Bearer 认证

## 4. A2A 客户端 (2.10)

- [x] 4.1 创建 `src/hecate/a2a/client/discovery.py` — 从 /.well-known/agent-card.json 获取和解析 AgentCard
- [x] 4.2 创建 `src/hecate/a2a/client/client.py` — A2AClient，包含 send_message()、send_streaming_message()、get_task()、cancel_task()
- [x] 4.3 实现 A2AClient 认证头注入（APIKey、Bearer token）
- [x] 4.4 实现 A2AClient 超时和重试（重用 RetryStrategy 模式）
- [x] 4.5 创建 `src/hecate/a2a/client/push.py` — 用于推送通知的 FastAPI webhook 接收器
- [x] 4.6 在 SkillRegistry 中实现 remote_agent SkillRef 解析（通过 discovery 获取 AgentCard）
- [x] 4.7 在 SkillRegistry 中实现 remote_agent SkillRef 调用（委托给 A2AClient.send_message）
- [x] 4.8 创建 `tests/test_a2a/test_client/test_discovery.py` — 测试 AgentCard 获取和解析
- [x] 4.9 创建 `tests/test_a2a/test_client/test_client.py` — 测试 A2AClient 方法
- [x] 4.10 创建 `tests/test_a2a/test_client/test_push.py` — 测试 webhook 接收器

## 5. 签名 Agent Card (2.10a)

- [x] 5.1 创建 `src/hecate/a2a/signing.py` — ES256 密钥对生成、JWS 签名、RFC 8785 规范化
- [x] 5.2 实现 sign_agent_card(card, private_key) → 带有 signatures 数组的卡片
- [x] 5.3 实现 verify_agent_card(card, jwks_url) → bool，含 JWKS 获取 + 缓存
- [x] 5.4 实现算法固定（仅 ES256，拒绝 alg:none、RS256、HS256）
- [x] 5.5 创建 `src/hecate/models/agent_card_key.py` — AgentCardKeyModel ORM（kid、private_key、public_key、algorithm、workspace_id、status、created_at、rotated_at）
- [x] 5.6 为 agent_card_keys 表创建 Alembic 迁移
- [x] 5.7 在 `/.well-known/jwks.json` 创建 JWKS 端点，以 JWK 格式返回公钥
- [x] 5.8 实现密钥轮换 API（POST /api/a2a/keys/rotate），含宽限期（旧密钥服务 7 天）
- [x] 5.9 将签名集成到 AgentCard 生成中（当工作区有活动密钥时签名）
- [x] 5.10 将验证集成到 A2AClient discovery 中（返回卡片前先验证）
- [x] 5.11 创建 `tests/test_a2a/test_signing.py` — 测试签名/验证循环、算法固定、JWKS 格式
- [x] 5.12 创建 `tests/test_a2a/test_key_rotation.py` — 测试含宽限期的密钥轮换

## 6. 协作冲突处理 (2.8)

- [x] 6.1 扩展 ConflictStrategy 枚举，添加 DISTRIBUTED_LOCK 和 NEGOTIATION 策略
- [x] 6.2 在 ConflictResolver 中实现分布式锁模式（通过 Redis 或 DB 的带 TTL 的异步锁获取）
- [x] 6.3 实现基于协商的冲突解决（委托给 P2PNegotiator）
- [x] 6.4 添加任务级冲突检测（两个 Agent 通过 TaskAllocator 认领同一任务）
- [x] 6.5 为 A2A 远程 Agent 添加权限范围不匹配检测（检查认证范围与请求的操作）
- [x] 6.6 在 CollaborationEventType 枚举中添加 A2A 特定事件类型（A2A_TASK_DELEGATED、A2A_TASK_RECEIVED、A2A_ARTIFACT_SENT、A2A_ARTIFACT_RECEIVED、A2A_AGENT_DISCOVERED）
- [x] 6.7 在 EventBus 负载元数据中实现 A2A 任务 ID 关联
- [x] 6.8 创建 `tests/test_engine/test_conflict_distributed.py` — 测试分布式锁策略
- [x] 6.9 创建 `tests/test_engine/test_conflict_a2a.py` — 测试 A2A 相关冲突场景

## 7. API 层

- [x] 7.1 创建 `src/hecate/api/management/a2a.py` — A2A 管理 API（密钥管理、远程 Agent 配置、任务列表）
- [x] 7.2 创建 `src/hecate/api/management/skill_registry.py` — SkillRegistry API（列出已解析技能、测试调用）
- [x] 7.3 在 main.py 中注册 A2A 和 SkillRegistry 管理路由
- [x] 7.4 创建 `tests/test_api/test_a2a_management.py` — 测试密钥轮换、远程 Agent 配置
- [x] 7.5 创建 `tests/test_api/test_skill_registry_api.py` — 测试技能列表和测试调用

## 8. 集成与验证

- [x] 8.1 扩展 AgentTool 以支持 remote_agent 目标（委托给 A2AClient.send_message）
- [x] 8.2 扩展 AgentTool 以支持工作流目标（内部使用 WorkflowTool）
- [x] 8.3 验证 A2A 任务执行触发 guardrail 钩子（A2A SendMessage 期间 PreLLMHook 触发）
- [x] 8.4 验证 A2A 任务出现在 Full-Chain Tracing 中
- [x] 8.5 验证嵌入的工作流触发 guardrails 和 tracing
- [x] 8.6 运行 `ruff check src/hecate/a2a/ src/hecate/skill_registry/ tests/test_a2a/ tests/test_skill_registry/`
- [x] 8.7 运行 `mypy src/hecate/a2a/ src/hecate/skill_registry/`
- [x] 8.8 运行 `python -m pytest tests/test_a2a/ tests/test_skill_registry/ tests/test_engine/test_workflow_tool.py tests/test_engine/test_nesting_depth.py tests/test_engine/test_conflict_distributed.py -v`
- [x] 8.9 运行完整测试套件 `python -m pytest tests/ -q`
