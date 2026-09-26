# a2a-protocol Delta

## ADDED Requirements

### Requirement: 客户端卡片验签失败关闭

A2A 客户端发现远端 Agent Card 时，若调用方要求验签（`verify_signature=True`），系统 SHALL 接入 JWS 验签实现并按可信密钥来源（配置的受信 JWKS）执行验证：卡片无签名、签名验证失败（篡改）、或签发者不在受信集合时，发现过程 SHALL 以异常失败（不返回 Agent Card）。验签成功的卡片 SHALL 附带已验证标记。`verify_signature=False` 时卡片照常返回且明确处于"未验证"状态——调用方验证信任与服务端签名能力是两个独立维度，系统 SHALL 区分标注。

#### Scenario: 无签名卡片被拒绝

- **WHEN** `verify_signature=True` 且远端卡片不含 `signatures`
- **THEN** 发现过程抛出异常，不返回 Agent Card

#### Scenario: 篡改卡片被拒绝

- **WHEN** 卡片签名与内容不匹配（签名验证失败）
- **THEN** 发现过程抛出异常，不返回 Agent Card

#### Scenario: 未知签发者被拒绝

- **WHEN** 卡片由受信 JWKS 之外的密钥签署
- **THEN** 发现过程抛出异常，不返回 Agent Card

#### Scenario: 验签通过标记已验证状态

- **WHEN** 卡片通过受信 JWKS 验签成功
- **THEN** 返回的卡片携带已验证标记，供后续调用决策使用

#### Scenario: 未要求验签时明确未验证状态

- **WHEN** `verify_signature=False` 拉取任意卡片
- **THEN** 卡片正常返回且标记为未验证
