## ADDED Requirements — 新增需求

### 需求：DockerEnvironment 网络出站策略支持
DockerEnvironment 应接受一个可选的 `NetworkEgressPolicy` 配置，控制来自容器的出站网络访问。当策略模式为 `deny_all` 时，容器应连接到仅内部网络的 Docker 网络，流量通过出站代理路由。

#### 场景：以 deny_all 策略创建的 DockerEnvironment
- **当** DockerEnvironment 以 `network_policy={mode: "deny_all", allowed_domains: ["pypi.org"]}` 创建时
- **则** 容器连接到没有互联网网关的内部 Docker 网络
- **且** 出站流量通过工作空间出站代理路由

#### 场景：以 allow_all 策略创建的 DockerEnvironment
- **当** DockerEnvironment 以 `network_policy={mode: "allow_all"}` 创建或不带网络策略时
- **则** 容器使用默认的 Docker 桥接网络，具有不受限制的互联网访问

### 需求：EnvironmentManager 安全配置哈希跟踪
EnvironmentManager 应为每个 Agent 基于其有效安全配置（网络策略、凭证范围、沙箱强制实施设置）计算并存储 `security_config_hash`。当哈希更改时，该 Agent 的热池容器应被标记为失效。

#### 场景：Agent 创建时计算安全配置哈希
- **当** 使用安全配置创建或更新 Agent 时
- **则** EnvironmentManager 从配置计算 `security_config_hash`
- **且** 将哈希与 Agent 的环境元数据一起存储

#### 场景：哈希更改使热池容器失效
- **当** Agent 安全配置更新且哈希更改时
- **则** 该 Agent 的任何热池容器被标记为销毁
- **且** 下一次 `get_or_create()` 调用创建具有更新配置的新容器

### 需求：DockerEnvironment 凭证范围支持
DockerEnvironment 应接受一个可选的 `CredentialScope` 配置，确定哪些环境变量传递给工具执行。启用凭证范围限定时，机密码模式的环境变量应被剥离，仅注入范围限定的凭证。

#### 场景：具有凭证范围的工具执行
- **当** DockerEnvironment 具有 `credential_scoping=true` 且工具以 scope `["API_TOKEN"]` 执行时
- **则** 工具子进程环境仅包含白名单系统变量 + `API_TOKEN`
