## Why — 为什么

与企-业平台（Bedrock AgentCore、Claude Code、Codex CLI、Dify、Google Vertex AI）相比，Hecate 的 Agent 执行环境存在关键安全漏洞。对 15+ 个平台（2025-2026）的行业研究识别出四个 P0 漏洞：(1) DockerEnvironment 零网络出站控制 — Agent 可以将数据泄露到任何外部服务器；(2) ToolAccessPolicy 的 `EXECUTE_SANDBOX` 决策未强制执行 — `sandbox_enabled` 仅仅是一个标志；(3) 安全审计事件散布在 `logger.debug()` 中，没有结构化管道；(4) 所有工具可以读取所有环境变量，包括 API 密钥和数据库密码。这些漏洞使 Hecate 不适合生产多租户部署而不进行修复。

## What Changes — 变更内容

### 9.12 环境网络出站控制
- 向 DockerEnvironment 添加 `NetworkEgressPolicy`，具有 `allowedDomains` / `deniedDomains` 配置
- 实现带有请求日志记录用于审计的出站流量代理
- 新配置：`AGENT_ENV_NETWORK_POLICY=allow_all|deny_all`（默认：`allow_all` 向后兼容）
- 每个 Agent 容器的网络命名空间隔离
- 当 `deny_all` 时：仅白名单中的域可访问；所有其他出站被阻止

### 9.13 沙箱强制实施集成
- ToolWorker 将 `EXECUTE_SANDBOX` 决策路由到 DockerEnvironment `exec_shell()` 而非直接执行
- 适用于 shell/exec 工具（`bash`、`exec_shell`、`execute_code`）和 `sandbox_enabled=True` 的 MCP 工具
- 工具执行后的容器退出验证（检测沙箱逃逸尝试）
- Agent 配置上的安全配置版本哈希 — 配置更改时热池容器失效
- 新配置：`AGENT_ENV_SANDBOX_ENFORCEMENT=false`（默认：关闭以向后兼容）

### 9.14 结构化安全审计管道
- 新的 `SecurityAuditEvent` 数据模型：工具名、参数、决策、原因、执行者/agent_id、workspace_id、on_behalf_of_user、时间戳、策略版本、会话 ID
- 新的 `SecurityAuditModel` ORM 表，带异步批量写入（内存缓冲区 → 每 N 个事件或 T 秒刷新一次）
- 每次 `ToolPolicyPipeline` + `ToolAccessPolicy` 评估自动发出审计事件
- REST 查询 API，带过滤（按 agent、workspace、决策、时间范围）
- 可配置保留期（默认 30 天，自动清理）
- 新配置：`AGENT_ENV_AUDIT_ENABLED=true`（默认：开启 — 低风险，仅观察）
- 适用于 LocalEnvironment 和 DockerEnvironment

### 9.15 每次执行的凭证范围限定
- 在调用前从工具执行上下文中剥离机密环境变量
- 基于模式的检测：`*_KEY`、`*_SECRET`、`*_TOKEN`、`*_PASSWORD`、`*_API_KEY`、`*_PWD` + 前缀 `HECATE_SECRET_*`
- 系统变量白名单始终保留：`PATH`、`HOME`、`LANG`、`LC_*`、`TMPDIR`、`USER`、`SHELL`
- 每个工具的 `CredentialScope` 配置映射（每个工具接收哪些凭证）
- 范围限定的凭证通过安全上下文注入，而非全局环境变量
- 新配置：`AGENT_ENV_CREDENTIAL_SCOPING=false`（默认：关闭以向后兼容）
- 仅适用于 DockerEnvironment（LocalEnvironment 记录为仅开发环境）

## Capabilities — 能力

### 新能力
- `environment-network-egress`：DockerEnvironment 的每环境应用级网络出站控制 — 域名允许列表/阻止列表、出站代理、请求日志记录
- `sandbox-enforcement`：保证 EXECUTE_SANDBOX 决策通过 DockerEnvironment 路由，带容器退出验证和热池配置失效
- `structured-security-audit`：结构化 SecurityAuditEvent 模型、异步批量存储、REST 查询 API、策略评估的自动发射、可配置保留期
- `credential-scoping`：运行时凭证隔离 — 基于模式的机密环境变量剥离、每工具凭证注入、系统变量白名单

### 修改的能力
- `agent-environment`：DockerEnvironment 获得 NetworkEgressPolicy 配置和凭证范围集成；EnvironmentManager 获得用于热池失效的安全配置版本跟踪
- `execution-security`：ToolAccessPolicy 的 `EXECUTE_SANDBOX` 决策现在在 ToolWorker 中有强制实施机制；AccessDecision 评估发出 SecurityAuditEvent
- `audit-logs`：现有的基本审计日志由结构化 SecurityAuditEvent 管道扩展；8.7 SS5 SIEM 管道将消费 9.14 事件作为输入

## Impact — 影响

### 代码变更
- `src/hecate/services/environment/docker_environment.py` — 网络出站策略、凭证范围限定
- `src/hecate/services/environment/manager.py` — 安全配置版本跟踪、热池失效
- `src/hecate/services/environment/environment.py` — AgentEnvironment ABC 获得可选安全钩子
- `src/hecate/engine/tool_access.py` — 每次评估发出 SecurityAuditEvent
- `src/hecate/engine/policy_pipeline.py` — 在可见性 + 执行评估时发出 SecurityAuditEvent
- `src/hecate/engine/workers/tool_worker.py` — EXECUTE_SANDBOX 路由到 DockerEnvironment
- `src/hecate/models/` — 新的 SecurityAuditModel ORM 表
- `src/hecate/core/config.py` — 新的 AGENT_ENV_* 安全配置设置
- `src/hecate/api/` — 新的审计事件查询 + 网络策略配置的 REST 端点

### 配置
- `.env.example` — 5 个新的 AGENT_ENV_* 环境变量
- 无破坏性变更 — 所有新功能默认使用向后兼容的值

### 依赖
- 无需新的外部包（使用现有的 asyncio、SQLAlchemy、FastAPI）
- 可选：用于高级网络隔离的 iptables/ipset（仅 Linux，记录但不要求）

### 测试
- NetworkEgressPolicy、CredentialScope、SecurityAuditEvent、SandboxEnforcementRouter 的单元测试
- 带网络出站 + 沙箱路由的 DockerEnvironment 集成测试
- 审计管道批量写入 + 查询 API 的单元测试
- ToolPolicyPipeline/ToolAccessPolicy 审计事件发射的引擎级测试
