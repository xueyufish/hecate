## ADDED Requirements

### Requirement: Next.js project initialization
The system SHALL have a Next.js 14 project in `web/` directory with App Router, shadcn/ui, and Tailwind CSS configured.

#### Scenario: Project structure
- **WHEN** developer opens `web/` directory
- **THEN** it contains a valid Next.js project with app/ router, components/ directory, and shadcn/ui installed

### Requirement: Application layout
The system SHALL provide a responsive desktop layout with sidebar navigation and main content area.

#### Scenario: Authenticated layout
- **WHEN** user is logged in
- **THEN** sidebar shows navigation links: Agents, Knowledge Bases, and the main area displays the current page

#### Scenario: Unauthenticated redirect
- **WHEN** user is not logged in and visits any protected page
- **THEN** system redirects to the login page

### Requirement: API client
The system SHALL provide a typed API client that handles authentication, error responses, and SSE streaming.

#### Scenario: Authenticated requests
- **WHEN** API client makes a request
- **THEN** it attaches the current access_token as Bearer header

#### Scenario: Token auto-refresh
- **WHEN** API request returns 401 with expired access_token
- **THEN** client automatically refreshes tokens and retries the request

### Requirement: Auth state management
The system SHALL persist auth state across page refreshes using secure storage.

#### Scenario: Login persistence
- **WHEN** user logs in and refreshes the page
- **THEN** user remains logged in (tokens restored from storage)

#### Scenario: Token expiry
- **WHEN** both access and refresh tokens expire
- **THEN** system redirects to login page
