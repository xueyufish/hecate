## ADDED Requirements — 新增需求

### 需求：NetworkEgressPolicy 配置
系统应为 DockerEnvironment 提供一个 `NetworkEgressPolicy` 配置，控制来自 Agent 容器的出站网络访问。策略应支持 `allow_all` 模式（默认，无限制）和 `deny_all` 模式（仅白名单中的域可访问）。全局默认值应通过 `AGENT_ENV_NETWORK_POLICY` 设置配置。

#### 场景：默认策略为 allow_all
- **当** 未设置 `AGENT_ENV_NETWORK_POLICY` 时
- **则** DockerEnvironment 容器具有不受限制的网络访问（向后兼容）

#### 场景：Deny all 策略阻止非白名单域
- **当** `AGENT_ENV_NETWORK_POLICY=deny_all` 且 `allowedDomains=["pypi.org", "api.openai.com"]` 时
- **则** Agent 容器可以访问 `pypi.org` 和 `api.openai.com`
- **且** Agent 容器无法访问 `evil.com` 或任何其他非白名单域

#### 场景：被拒绝域覆盖允许域
- **当** `allowedDomains=["*.example.com"]` 且 `deniedDomains=["bad.example.com"]` 时
- **则** `api.example.com` 可访问
- **且** `bad.example.com` 被阻止，即使它匹配允许的通配符

#### 场景：每 Agent 策略覆盖全局默认值
- **当** 全局 `AGENT_ENV_NETWORK_POLICY=allow_all` 但 Agent 配置有 `network_policy: {mode: "deny_all", allowed_domains: ["api.github.com"]}` 时
- **则** 该 Agent 的容器使用 deny_all，仅 `api.github.com` 可访问

### 需求：用于网络隔离的出站代理
系统应在 `deny_all` 策略激活时，将 DockerEnvironment 出站流量通过一个出站代理容器路由。代理应实施域级访问控制，并将所有请求记录到结构化审计管道。

#### 场景：代理容器按工作空间懒创建
- **当** 工作空间 W 中的第一个 Agent 具有 `deny_all` 策略时
- **则** 为工作空间 W 创建一个出站代理容器
- **且** 工作空间 W 中的后续 Agent 复用同一代理容器

#### 场景：Agent 容器连接到仅内部网络
- **当** Agent 具有 `deny_all` 策略时
- **则** Agent 的 Docker 容器连接到没有互联网网关的内部 Docker 网络
- **且** 出站流量只能到达出站代理

#### 场景：代理记录所有请求
- **当** Agent 容器通过代理发出 HTTP 请求时
- **则** 代理记录：时间戳、agent_id、workspace_id、目标域名、允许/阻止、响应状态
- **且** 日志条目作为 `SecurityAuditEvent` 发出

### 需求：LocalEnvironment 网络控制警告
当配置了网络出站控制但 `AGENT_ENV_BACKEND=local` 时，系统应记录一条 WARNING。

#### 场景：具有 deny_all 策略的 LocalEnvironment
- **当** `AGENT_ENV_BACKEND=local` 且 `AGENT_ENV_NETWORK_POLICY=deny_all` 时
- **则** 系统记录警告 "Network egress control not available on LocalEnvironment"
- **且** 不应用网络限制
