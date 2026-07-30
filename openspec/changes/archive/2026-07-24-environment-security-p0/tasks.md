## 1. Configuration & Data Models (Foundation) — 配置和数据模型（基础）

- [x] 1.1 向 `core/config.py` 添加安全配置设置：`AGENT_ENV_NETWORK_POLICY`（默认：`allow_all`）、`AGENT_ENV_AUDIT_ENABLED`（默认：`true`）、`AGENT_ENV_AUDIT_RETENTION_DAYS`（默认：`30`）、`AGENT_ENV_CREDENTIAL_SCOPING`（默认：`false`）、`AGENT_ENV_SANDBOX_ENFORCEMENT`（默认：`false`）
- [x] 1.2 使用 5 个新的 `AGENT_ENV_*` 变量和文档注释更新 `.env.example`
- [x] 1.3 在 `models/security_audit.py` 中创建 `SecurityAuditModel` ORM 表 — 字段：id（UUID PK）、agent_id（已索引）、workspace_id（已索引）、session_id（可空）、tool_name、arguments_hash（SHA-256）、decision、reason、policy_version、on_behalf_of_user（可空）、timestamp（已索引）、layer_results（JSON）
- [x] 1.4 在 `models/security_audit.py` 中创建 `SecurityAuditCreateSchema` / `SecurityAuditReadSchema` Pydantic 模式
- [x] 1.5 为 `security_audit_events` 表添加 Alembic 迁移，包含 (agent_id, timestamp) 和 (workspace_id, timestamp) 索引
- [x] 1.6 在 `services/environment/network_policy.py` 中创建 `NetworkEgressPolicy` 数据类 — 字段：mode（`allow_all`/`deny_all`）、allowed_domains（list[str]）、denied_domains（list[str]）
- [x] 1.7 在 `services/environment/credential_scope.py` 中创建 `CredentialScope` 数据类 — 字段：enabled（bool）、strip_patterns（list[str]）、whitelist（set[str]）、custom_patterns（list[str]）、tool_credentials（dict[str, list[str]]）

## 2. Structured Security Audit Pipeline (9.14) — 结构化安全审计管道

- [x] 2.1 在 `engine/audit_sink.py` 中创建 `AuditSink` ABC — 抽象方法 `emit(event: dict) -> None`（保持引擎层零依赖：引擎定义接口，服务提供实现）
- [x] 2.2 在 `engine/audit_sink.py` 中创建 `SecurityAuditEmitter` — 将事件收集到异步缓冲区，每 50 个事件或 5 秒通过 `AuditSink` 刷新
- [x] 2.3 在 `ToolPolicyPipeline.evaluate_visibility()` 中添加审计发射 — 当达到 HIDE/DENY 决策时为每个工具发出事件
- [x] 2.4 在 `ToolPolicyPipeline.evaluate_execution()` 中添加审计发射 — 发出具有最终决策 + 每层 LayerResult 分解的事件
- [x] 2.5 在 `ToolAccessPolicy.evaluate()` 中添加审计发射 — 发出具有 AccessDecision、匹配规则、风险级别、policy_version 的事件
- [x] 2.6 在 `services/security/audit_service.py` 中实现 `SecurityAuditService` — 实现 `AuditSink`，通过异步批量写入器写入 `SecurityAuditModel`
- [x] 2.7 在 `SecurityAuditService` 中实现异步批量写入器 — 内存 deque 缓冲区，后台任务每 50 个事件或 5 秒刷新一次，关闭时刷新
- [x] 2.8 实现审计保留清理任务 — 每日后台任务删除早于 `AGENT_ENV_AUDIT_RETENTION_DAYS` 的行
- [x] 2.9 在 `api/security_audit.py` 中创建 REST API 端点：`GET /api/security/audit` 带查询参数（agent_id、workspace_id、decision、start、end、limit、offset）+ 分页响应
- [x] 2.10 将 `SecurityAuditService` 作为单例接入 DI 容器；通过 `EnginePort` 或执行上下文注入引擎
- [x] 2.11 为 `SecurityAuditEmitter` 缓冲区 + 刷新行为编写单元测试
- [x] 2.12 为 `SecurityAuditService` 批量写入 + 保留清理编写单元测试
- [x] 2.13 为 REST API 查询过滤和分页编写单元测试
- [x] 2.14 编写验证从 ToolPolicyPipeline（visibility + execution）和 ToolAccessPolicy 发出审计事件的单元测试

## 3. Sandbox Enforcement Integration (9.13) — 沙箱强制实施集成

- [x] 3.1 在 `engine/workers/sandbox_router.py` 中创建 `SandboxEnforcementRouter` — 检查 `AccessDecision`，在强制实施启用时将 `EXECUTE_SANDBOX` 路由到 DockerEnvironment 的 shell/exec 工具
- [x] 3.2 在 `SandboxEnforcementRouter` 中实现工具类别分类 — 确定工具是 shell/exec（路由到容器）、MCP 沙箱化（路由到容器）还是 Python 内置（直接执行）
- [x] 3.3 将 `SandboxEnforcementRouter` 集成到 `ToolWorker` 中 — 在策略评估和工具执行之间，检查 `AGENT_ENV_SANDBOX_ENFORCEMENT` 标志
- [x] 3.4 实现容器退出验证 — `exec_shell()` 后，检查返回码；在异常退出时发出带有 `decision="sandbox_anomaly"` 的 `SecurityAuditEvent`
- [x] 3.5 在 `EnvironmentManager` 中实现 `security_config_hash` 计算 — 每个 Agent 的网络策略 + 凭证范围 + 沙箱强制实施配置的哈希
- [x] 3.6 在 `security_config_hash` 更改时实现热池失效 — 销毁旧容器，在下一次 `get_or_create()` 时强制创建新容器
- [x] 3.7 为 `SandboxEnforcementRouter` 路由决策编写单元测试（shell 工具 → 容器、Python 工具 → 直接、MCP 工具 → 容器）
- [x] 3.8 为容器退出验证 + 异常事件发射编写单元测试
- [x] 3.9 为热池配置哈希失效编写单元测试
- [x] 3.10 编写集成测试：启用强制实施的 ToolWorker 将 bash 工具路由到 DockerEnvironment

## 4. Network Egress Control (9.12) — 网络出站控制

- [x] 4.1 在 `services/environment/egress_proxy.py` 中实现出站代理生命周期管理 — 每个工作空间懒创建、热池、Squid 或轻量级 HTTP CONNECT 代理
- [x] 4.2 为 `deny_all` 模式实现 Docker 网络创建 — 每个工作空间的仅内部网络，无互联网网关
- [x] 4.3 更新 `DockerEnvironment.__init__` 以接受可选的 `NetworkEgressPolicy` — 当 `deny_all` 时，将容器连接到内部网络 + 配置代理
- [x] 4.4 在出站代理中实现域名允许列表/阻止列表强制实施 — 从 `NetworkEgressPolicy.allowed_domains` / `denied_domains` 派生配置
- [x] 4.5 实现代理请求日志记录 — 每个请求发出带有目标域名、允许/阻止、响应状态的 `SecurityAuditEvent`
- [x] 4.6 更新 `EnvironmentManager` 以基于全局配置 + 每 Agent 覆盖将 `NetworkEgressPolicy` 传递给 DockerEnvironment
- [x] 4.7 实现 LocalEnvironment 警告 — 当 `AGENT_ENV_NETWORK_POLICY=deny_all` 且后端为 local 时记录 WARNING
- [x] 4.8 为 `NetworkEgressPolicy` 配置解析和验证编写单元测试
- [x] 4.9 为出站代理允许列表/阻止列表逻辑编写单元测试
- [x] 4.10 编写集成测试：具有 deny_all 策略的 DockerEnvironment 阻止非白名单域访问（模拟代理）

## 5. Per-Execution Credential Scoping (9.15) — 每次执行的凭证范围限定

- [x] 5.1 在 `services/environment/credential_scope.py` 中实现凭证模式检测 — 匹配 `*_KEY`、`*_SECRET`、`*_TOKEN`、`*_PASSWORD`、`*_API_KEY`、`*_PWD`、`HECATE_SECRET_*` 前缀、自定义模式
- [x] 5.2 实现系统变量白名单 — `PATH`、`HOME`、`LANG`、`LC_*`、`TMPDIR`、`USER`、`SHELL`、`HOSTNAME`、`TERM`、`PWD` 始终保留
- [x] 5.3 实现环境清理函数 — 给定完整环境字典 + CredentialScope，返回清理后的字典，机密被剥离，范围限定的凭证被注入
- [x] 5.4 更新 `DockerEnvironment.exec_shell()` 以在命令执行前当 `AGENT_ENV_CREDENTIAL_SCOPING=true` 时应用凭证范围限定
- [x] 5.5 实现 LocalEnvironment 警告 — 当凭证范围启用且后端为 local 时记录 WARNING
- [x] 5.6 为凭证模式检测编写单元测试（所有模式 + 自定义 + 前缀）
- [x] 5.7 为白名单保留编写单元测试（系统变量从不被剥离）
- [x] 5.8 为使用 CredentialScope 配置的环境清理编写单元测试
- [x] 5.9 编写集成测试：具有凭证范围的 DockerEnvironment 从工具子进程环境中剥离 OPENAI_API_KEY

## 6. Integration & Cross-Feature Tests — 集成与跨功能测试

- [x] 6.1 编写端到端测试：Agent 尝试 `curl` 到非白名单域 → 被网络策略阻止 → 记录审计事件 → 凭证未泄露
- [x] 6.2 编写端到端测试：EXECUTE_SANDBOX bash 工具 → 在容器中执行 → 审计事件带每层分解 → 容器健康已验证
- [x] 6.3 编写测试：所有功能禁用（默认值）→ 与现有 Agent 零行为变更
- [x] 6.4 编写测试：审计管道在 LocalEnvironment 上工作（发出事件，可通过 API 查询）
- [x] 6.5 编写测试：使用不变的安全配置的热池复用（容器被复用）
- [x] 6.6 编写测试：安全配置更改时的热池失效（容器被销毁 + 重新创建）

## 7. Documentation & Cleanup — 文档与清理

- [x] 7.1 使用环境安全 P0 部分更新 `docs/design/security-architecture.md`
- [x] 7.2 在 `docs/design/` 中记录 LocalEnvironment 限制 — "仅开发环境，不用于生产"
- [x] 7.3 运行 `ruff check src/hecate/ tests/ && ruff format --check src/ tests/ && mypy src/ && python -m pytest tests/ -q`
- [x] 7.4 验证所有新配置变量在 `.env.example` 中有文档记录
- [x] 7.5 如果实现期间任何需求细节发生变化，更新 spec delta 文件
