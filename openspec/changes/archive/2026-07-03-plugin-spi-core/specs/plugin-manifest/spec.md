## ADDED Requirements

### Requirement: PluginManifest dataclass
The system SHALL define a `PluginManifest` dataclass that describes plugin metadata. The dataclass MUST be frozen (immutable) and include the following fields:
- `type`: str — plugin type identifier (e.g., "tool", "evaluator", "channel", "auth_provider", "notifier")
- `name`: str — unique plugin name within its type
- `version`: str — semantic version string (e.g., "1.0.0")
- `api_version`: str — API version this plugin targets
- `min_platform_version`: str — minimum platform version required
- `description`: str — human-readable description
- `permissions`: list[str] — required permissions (e.g., ["network:https", "filesystem:read"])

#### Scenario: Create PluginManifest
- **WHEN** a developer creates a PluginManifest instance with all required fields
- **THEN** the instance is immutable (frozen) and all fields are accessible

#### Scenario: PluginManifest with optional fields
- **WHEN** a developer creates a PluginManifest with only required fields
- **THEN** optional fields default to empty string or empty list as appropriate

### Requirement: PluginManifest equality and hashing
The system SHALL support equality comparison and hashing of PluginManifest instances based on type + name + version.

#### Scenario: Compare equal manifests
- **WHEN** two PluginManifest instances have the same type, name, and version
- **THEN** they are equal and have the same hash

#### Scenario: Compare different manifests
- **WHEN** two PluginManifest instances differ in type, name, or version
- **THEN** they are not equal
