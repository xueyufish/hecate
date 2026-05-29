## ADDED Requirements

### Requirement: User registration
The system SHALL allow new users to register with email and password.

#### Scenario: Successful registration
- **WHEN** user submits email and password to `POST /api/auth/register`
- **THEN** system creates a user record with bcrypt-hashed password and returns 201 with user ID

#### Scenario: Duplicate email
- **WHEN** user registers with an email that already exists
- **THEN** system returns 409 Conflict

#### Scenario: Invalid input
- **WHEN** user submits missing email or password shorter than 8 characters
- **THEN** system returns 422 with validation error details

### Requirement: User login
The system SHALL authenticate users and issue JWT tokens.

#### Scenario: Successful login
- **WHEN** user submits correct email and password to `POST /api/auth/login`
- **THEN** system returns `access_token` (30min expiry) and `refresh_token` (7d expiry)

#### Scenario: Wrong credentials
- **WHEN** user submits incorrect email or password
- **THEN** system returns 401 Unauthorized

### Requirement: Token refresh
The system SHALL allow refreshing access tokens using a valid refresh token.

#### Scenario: Successful refresh
- **WHEN** user submits a valid refresh_token to `POST /api/auth/refresh`
- **THEN** system returns a new access_token and refresh_token, old refresh_token is invalidated

#### Scenario: Expired refresh token
- **WHEN** user submits an expired or invalid refresh_token
- **THEN** system returns 401 Unauthorized

### Requirement: Get current user
The system SHALL return the authenticated user's profile.

#### Scenario: Authenticated user info
- **WHEN** user sends `GET /api/auth/me` with valid JWT
- **THEN** system returns user ID, email, and created_at

### Requirement: Dual authentication support
The system SHALL support both JWT Bearer Token and API Key authentication on all endpoints.

#### Scenario: JWT authentication
- **WHEN** request includes `Authorization: Bearer <jwt_token>`
- **THEN** system authenticates the user and sets request context

#### Scenario: API Key authentication
- **WHEN** request includes `Authorization: Bearer <api_key>`
- **THEN** system authenticates via API Key lookup (backward compatible)

### Requirement: User data model
The system SHALL store users in a `users` table with id (UUID), email (unique), hashed_password, created_at, updated_at.

#### Scenario: Database schema
- **WHEN** Alembic migration runs
- **THEN** `users` table is created with unique index on email
