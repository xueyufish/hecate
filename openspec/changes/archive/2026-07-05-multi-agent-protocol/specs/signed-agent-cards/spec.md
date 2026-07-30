## ADDED Requirements — 新增需求

### Requirement: 系统为 AgentCard 签名生成 ES256 密钥对 — System generates ES256 key pairs for AgentCard signing
系统应使用 ES256 算法（RFC 7518）生成 ECDSA P-256 密钥对，用于签署 AgentCard。

#### Scenario: 生成新的签名密钥对 — Generate new signing key pair
- **WHEN** 管理员请求为工作区生成密钥
- **THEN** 系统生成 ECDSA P-256 密钥对，分配一个 `kid`（密钥 ID），并将其存储在 `agent_card_keys` 表中

#### Scenario: 带宽限期的密钥轮换 — Key rotation with grace period
- **WHEN** 管理员轮换签名密钥
- **THEN** 系统生成新的密钥对，将旧密钥标记为 `rotating` 状态，持续一个可配置的宽限期（默认 7 天），并在宽限期内同时在 JWKS 中提供两个密钥

### Requirement: 系统使用 JWS 签名签署 AgentCard — System signs AgentCards with JWS signatures
系统应使用 JWS（RFC 7515）和 ES256 算法签署 AgentCard，在签名前通过 RFC 8785 JSON 规范化方案对卡片进行规范化。

#### Scenario: 签署 AgentCard — Sign an AgentCard
- **WHEN** A2A 服务器为启用签名的工作区生成 AgentCard
- **THEN** AgentCard 应包含一个 `signatures` 数组，其中包含一个 JWS 对象，包含 `protected`（base64url 编码头，含 `alg: ES256`、`kid`）、`signature`（base64url 签名），且签名应能针对规范化后的卡片进行验证

#### Scenario: 签名禁用时提供未签名卡片 — Unsigned card when signing disabled
- **WHEN** A2A 服务器为禁用签名的工作区生成 AgentCard
- **THEN** AgentCard 不应包含 `signatures` 字段

### Requirement: 系统在知名端点提供 JWKS — System serves JWKS at well-known endpoint
系统应在 `/.well-known/jwks.json` 提供 JWKS（JSON Web Key Set）文档，其中包含用于 AgentCard 签名验证的公钥。

#### Scenario: 获取 JWKS — Fetch JWKS
- **WHEN** 任何 HTTP 客户端发送 `GET /.well-known/jwks.json`
- **THEN** 系统返回 JWKS 文档，其中以 JWK 格式包含公钥，包括 `kty`、`crv`、`x`、`y`、`kid` 和 `alg` 字段

#### Scenario: JWKS 排除私钥材料 — JWKS excludes private key material
- **WHEN** 获取 JWKS 端点
- **THEN** 响应不应包含任何私钥字段（EC 密钥的 `d`）

### Requirement: 系统验证来自远程 Agent 的已签名 AgentCard — System verifies signed AgentCards from remote agents
系统应使用来自远程 JWKS 端点或嵌入在卡片中的公钥，验证远程 AgentCard 上的 JWS 签名。

#### Scenario: 验证有效签名 — Verify valid signature
- **WHEN** A2AClient 从远程端点获取签名的 AgentCard
- **THEN** 系统对卡片进行规范化（排除 `signatures`），获取 JWKS，通过 `kid` 查找密钥，并验证 ES256 签名

#### Scenario: 拒绝无效签名 — Reject invalid signature
- **WHEN** 远程 AgentCard 的签名无法通过其 JWKS 验证
- **THEN** 系统拒绝该 AgentCard 并返回验证错误

#### Scenario: 拒绝 alg:none 降级攻击 — Reject alg:none downgrade
- **WHEN** 远程 AgentCard 签名的受保护头中包含 `alg: none`
- **THEN** 系统拒绝该 AgentCard，并返回降级攻击错误

### Requirement: 系统固定使用 ES256 算法 — System pins to ES256 algorithm
系统只应接受 ES256 签名用于 AgentCard 验证，拒绝所有其他算法，包括 `none`、`RS256` 和 `HS256`。

#### Scenario: 拒绝 RS256 签名 — Reject RS256 signature
- **WHEN** 远程 AgentCard 签名指定 `alg: RS256`
- **THEN** 系统拒绝该签名，并返回算法不匹配错误

### Requirement: 系统缓存 JWKS 响应 — System caches JWKS responses
系统应使用可配置的 TTL（默认 1 小时）缓存 JWKS 响应，以减少签名验证期间的网络开销。

#### Scenario: JWKS 缓存命中 — JWKS cache hit
- **WHEN** 系统在 TTL 内验证来自同一远程来源的第二个 AgentCard
- **THEN** 系统使用缓存的 JWKS，无需发起新的 HTTP 请求
