## ADDED Requirements

### Requirement: AuthProviderABC defines pluggable authentication interface
The system SHALL define an `AuthProviderABC` abstract base class in `auth/provider.py` with the following abstract interface: `name` property, `description` property, and `authenticate(token, db)` method that returns `AuthContext | None`.

#### Scenario: Concrete auth provider implementation
- **WHEN** a class extends AuthProviderABC and implements all abstract methods
- **THEN** it SHALL be registerable with PluginRegistry under type="auth_provider"

#### Scenario: Missing abstract method
- **WHEN** a class extends AuthProviderABC but does not implement `authenticate()`
- **THEN** instantiation SHALL raise TypeError

### Requirement: JWTAuthProvider as built-in implementation
The system SHALL provide a `JWTAuthProvider` that implements AuthProviderABC. It SHALL decode JWT access tokens using the existing `decode_access_token()` function and return an `AuthContext` with `auth_method="jwt"`.

#### Scenario: Valid JWT token
- **WHEN** `authenticate(valid_jwt_token, db)` is called
- **THEN** it SHALL return an `AuthContext` with user_id, org_id, workspace_id, and role from the JWT claims

#### Scenario: Invalid JWT token
- **WHEN** `authenticate(invalid_token, db)` is called
- **THEN** it SHALL return None (not raise an exception)

#### Scenario: Expired JWT token
- **WHEN** `authenticate(expired_jwt_token, db)` is called
- **THEN** it SHALL return None

### Requirement: APIKeyAuthProvider as built-in implementation
The system SHALL provide an `APIKeyAuthProvider` that implements AuthProviderABC. It SHALL look up the API key hash in the database and return an `AuthContext` with `auth_method="api_key"`.

#### Scenario: Valid database-backed API key
- **WHEN** `authenticate(valid_api_key, db)` is called
- **THEN** it SHALL return an `AuthContext` with the key's scope, workspace, and creator

#### Scenario: Expired API key
- **WHEN** `authenticate(expired_api_key, db)` is called
- **THEN** it SHALL return None

#### Scenario: API key not found
- **WHEN** `authenticate(unknown_key, db)` is called
- **THEN** it SHALL return None

### Requirement: Auth provider iteration in auth flow
The system SHALL provide a `resolve_auth_context(credentials, db)` function that iterates all registered auth providers in order. The first provider to return a non-None `AuthContext` SHALL be used. If no provider succeeds, an HTTP 401 SHALL be raised.

#### Scenario: JWT succeeds first
- **WHEN** a request has a valid JWT token
- **THEN** JWTAuthProvider SHALL return AuthContext before APIKeyAuthProvider is tried

#### Scenario: API key succeeds after JWT fails
- **WHEN** a request has an invalid JWT but valid API key
- **THEN** JWTAuthProvider SHALL return None, then APIKeyAuthProvider SHALL return AuthContext

#### Scenario: All providers fail
- **WHEN** a request has an invalid JWT and invalid API key
- **THEN** all providers SHALL return None, and the function SHALL raise HTTP 401

### Requirement: Backward compatibility with existing get_auth_context
The existing `get_auth_context()` FastAPI dependency SHALL continue to work during migration. It SHALL delegate to `resolve_auth_context()` internally, preserving the same behavior.

#### Scenario: Existing dependency injection works
- **WHEN** a FastAPI endpoint uses `Depends(get_auth_context)`
- **THEN** it SHALL receive the same AuthContext as before (no behavior change)

### Requirement: Auth provider registration via PluginRegistry
Auth providers SHALL be registered with PluginRegistry under `type="auth_provider"`. The manifest SHALL include the provider's `name` and `description`.

#### Scenario: Register a new auth provider
- **WHEN** `registry.register(manifest, saml_provider)` is called with `type="auth_provider"`
- **THEN** the provider SHALL be iterated during auth resolution

#### Scenario: Provider ordering
- **WHEN** multiple auth providers are registered
- **THEN** they SHALL be iterated in registration order (first registered = first tried)
