# signed-agent-cards Specification

## Requirements

### Requirement: System generates ES256 key pairs for AgentCard signing
The system SHALL generate ECDSA P-256 key pairs for signing AgentCards using the ES256 algorithm (RFC 7518).

#### Scenario: Generate new signing key pair
- **WHEN** an administrator requests key generation for a workspace
- **THEN** the system generates an ECDSA P-256 key pair, assigns a `kid` (key ID), and stores it in the `agent_card_keys` table

#### Scenario: Key rotation with grace period
- **WHEN** an administrator rotates the signing key
- **THEN** the system generates a new key pair, marks the old key as `rotating` for a configurable grace period (default 7 days), and serves both keys in JWKS during the grace period

### Requirement: System signs AgentCards with JWS signatures
The system SHALL sign AgentCards using JWS (RFC 7515) with the ES256 algorithm, canonicalizing the card via RFC 8785 JSON Canonicalization Scheme before signing.

#### Scenario: Sign an AgentCard
- **WHEN** the A2A server generates an AgentCard for a workspace with signing enabled
- **THEN** the AgentCard SHALL include a `signatures` array with a JWS object containing `protected` (base64url header with `alg: ES256`, `kid`), `signature` (base64url signature), and the signature SHALL verify against the canonicalized card

#### Scenario: Unsigned card when signing disabled
- **WHEN** the A2A server generates an AgentCard for a workspace with signing disabled
- **THEN** the AgentCard SHALL NOT include a `signatures` field

### Requirement: System serves JWKS at well-known endpoint
The system SHALL serve a JWKS (JSON Web Key Set) document at `/.well-known/jwks.json` containing public keys for AgentCard signature verification.

#### Scenario: Fetch JWKS
- **WHEN** any HTTP client sends `GET /.well-known/jwks.json`
- **THEN** the system returns a JWKS document with public keys in JWK format including `kty`, `crv`, `x`, `y`, `kid`, and `alg` fields

#### Scenario: JWKS excludes private key material
- **WHEN** the JWKS endpoint is fetched
- **THEN** the response SHALL NOT contain any private key fields (`d` for EC keys)

### Requirement: System verifies signed AgentCards from remote agents
The system SHALL verify JWS signatures on remote AgentCards using public keys from the remote JWKS endpoint or embedded in the card.

#### Scenario: Verify valid signature
- **WHEN** the A2AClient fetches a signed AgentCard from a remote endpoint
- **THEN** the system canonicalizes the card (excluding `signatures`), fetches the JWKS, finds the key by `kid`, and verifies the ES256 signature

#### Scenario: Reject invalid signature
- **WHEN** a remote AgentCard has a signature that does not verify against its JWKS
- **THEN** the system rejects the AgentCard and returns a verification error

#### Scenario: Reject alg:none downgrade
- **WHEN** a remote AgentCard signature has `alg: none` in the protected header
- **THEN** the system rejects the AgentCard with a downgrade-attack error

### Requirement: System pins to ES256 algorithm
The system SHALL only accept ES256 signatures for AgentCard verification, rejecting all other algorithms including `none`, `RS256`, and `HS256`.

#### Scenario: Reject RS256 signature
- **WHEN** a remote AgentCard signature specifies `alg: RS256`
- **THEN** the system rejects the signature with an algorithm-mismatch error

### Requirement: System caches JWKS responses
The system SHALL cache JWKS responses with a configurable TTL (default 1 hour) to reduce network overhead during signature verification.

#### Scenario: JWKS cache hit
- **WHEN** the system verifies a second AgentCard from the same remote origin within the TTL
- **THEN** the system uses the cached JWKS without making a new HTTP request
