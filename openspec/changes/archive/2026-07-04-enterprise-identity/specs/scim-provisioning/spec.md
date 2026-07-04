## ADDED Requirements

### Requirement: SCIM 2.0 User endpoints
The system SHALL expose SCIM 2.0-compliant user management endpoints at `/scim/v2/Users` supporting POST, GET (list), GET (single), PUT, PATCH, and DELETE operations per RFC 7644.

#### Scenario: Create user via SCIM POST
- **WHEN** a POST request to `/scim/v2/Users` is received with a valid SCIM User JSON body and valid bearer token
- **THEN** the system SHALL create a UserModel with `email=userName`, `display_name=displayName`, `given_name=name.givenName`, `family_name=name.familyName`, `external_id=externalId`, `active=True`, `sso_id=userName`
- **AND** SHALL return HTTP 201 with the SCIM User representation including `id`, `meta.location`, and `meta.resourceType=User`

#### Scenario: List users with pagination
- **WHEN** a GET request to `/scim/v2/Users?startIndex=1&count=10` is received
- **THEN** the system SHALL return a ListResponse with `totalResults`, `startIndex`, `itemsPerPage`, and `resources` array of SCIM User objects

#### Scenario: Filter users by userName
- **WHEN** a GET request to `/scim/v2/Users?filter=userName eq 'john@example.com'` is received
- **THEN** the system SHALL parse the SCIM filter, query UserModel by email, and return matching users in a ListResponse

#### Scenario: Get user by ID
- **WHEN** a GET request to `/scim/v2/Users/{id}` is received for an existing user
- **THEN** the system SHALL return the SCIM User representation with all attributes

#### Scenario: Get non-existent user
- **WHEN** a GET request to `/scim/v2/Users/{id}` is received for a non-existent user
- **THEN** the system SHALL return a SCIM error response with `status=404` and `detail="Resource {id} not found"`

#### Scenario: Update user via PUT (full replacement)
- **WHEN** a PUT request to `/scim/v2/Users/{id}` is received with a full SCIM User body
- **THEN** the system SHALL replace all editable fields on the UserModel and return the updated SCIM User with HTTP 200

#### Scenario: Partial update via PATCH
- **WHEN** a PATCH request to `/scim/v2/Users/{id}` is received with a PatchOp body containing `Operations`
- **THEN** the system SHALL apply each operation (replace, add, remove) to the UserModel fields and return HTTP 200 with the updated user

#### Scenario: Deprovision user via PATCH active=false
- **WHEN** a PATCH request sets `active` to `false`
- **THEN** the system SHALL set `UserModel.active=False` and the user SHALL no longer be able to authenticate

#### Scenario: Delete user
- **WHEN** a DELETE request to `/scim/v2/Users/{id}` is received
- **THEN** the system SHALL set `UserModel.active=False` (soft delete) and return HTTP 204 No Content

#### Scenario: Duplicate userName rejected
- **WHEN** a POST request creates a user with a `userName` that already exists
- **THEN** the system SHALL return a SCIM error with `status=409`, `scimType=uniqueness`

### Requirement: SCIM 2.0 Group endpoints
The system SHALL expose SCIM 2.0-compliant group management endpoints at `/scim/v2/Groups` supporting POST, GET, PATCH, and DELETE operations for workspace/team membership sync.

#### Scenario: Create group via SCIM POST
- **WHEN** a POST request to `/scim/v2/Groups` is received with `displayName` and `members` array
- **THEN** the system SHALL create a group record mapping to workspace roles/teams and return HTTP 201

#### Scenario: List groups
- **WHEN** a GET request to `/scim/v2/Groups` is received
- **THEN** the system SHALL return a ListResponse of all groups with member references

#### Scenario: Update group membership via PATCH
- **WHEN** a PATCH request to `/scim/v2/Groups/{id}` adds or removes members
- **THEN** the system SHALL update the group membership accordingly

#### Scenario: Delete group
- **WHEN** a DELETE request to `/scim/v2/Groups/{id}` is received
- **THEN** the system SHALL soft-delete the group and return HTTP 204

### Requirement: SCIM discovery endpoints
The system SHALL expose SCIM 2.0 discovery endpoints for ServiceProviderConfig, Schemas, and ResourceTypes per RFC 7643 Section 4.

#### Scenario: Get ServiceProviderConfig
- **WHEN** a GET request to `/scim/v2/ServiceProviderConfig` is received
- **THEN** the system SHALL return capabilities including `patch.supported=true`, `filter.supported=true`, `sort.supported=true`, `etag.supported=true`, and `bulk.supported=false`

#### Scenario: Get Schemas
- **WHEN** a GET request to `/scim/v2/Schemas` is received
- **THEN** the system SHALL return schema definitions for the core User, Group, and Enterprise extension schemas

#### Scenario: Get ResourceTypes
- **WHEN** a GET request to `/scim/v2/ResourceTypes` is received
- **THEN** the system SHALL return definitions for User and Group resource types with their schema, endpoint, and description

### Requirement: SCIM authentication
The system SHALL authenticate SCIM API requests using a separate SCIM bearer token, distinct from JWT and API key authentication.

#### Scenario: Valid SCIM bearer token
- **WHEN** a SCIM request includes `Authorization: Bearer {scim_token}` where the token matches the configured `SCIM_BEARER_TOKEN` setting
- **THEN** the request SHALL be processed

#### Scenario: Missing or invalid SCIM token
- **WHEN** a SCIM request has no Authorization header or an invalid token
- **THEN** the system SHALL return HTTP 401 with a SCIM error response

### Requirement: SCIM error response format
The system SHALL return errors in the SCIM error format per RFC 7644 Section 3.12 with `schemas`, `status`, `scimType` (when applicable), and `detail` fields.

#### Scenario: Validation error
- **WHEN** a SCIM request body fails validation
- **THEN** the system SHALL return `{"schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"], "status": "400", "scimType": "invalidSyntax", "detail": "..."}`

#### Scenario: Not found error
- **WHEN** a SCIM resource is not found
- **THEN** the system SHALL return `{"schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"], "status": "404", "detail": "Resource not found"}`

### Requirement: SCIM user model extension
The system SHALL add `external_id` (String 255, nullable), `display_name` (String 255, nullable), `given_name` (String 128, nullable), `family_name` (String 128, nullable), and `active` (Boolean, default True) fields to UserModel to support SCIM provisioning.

#### Scenario: New user fields default values
- **WHEN** an existing user is loaded after migration
- **THEN** `active` SHALL be `True`, and `external_id`, `display_name`, `given_name`, `family_name` SHALL be `None`

#### Scenario: Inactive user cannot authenticate
- **WHEN** a user with `active=False` attempts to authenticate via any auth provider
- **THEN** the system SHALL reject the authentication and return None or HTTP 401
