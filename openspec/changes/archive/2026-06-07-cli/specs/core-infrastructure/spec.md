## ADDED Requirements

### Requirement: CLI output format setting in core config
The `Settings` class SHALL include an `CLI_DEFAULT_OUTPUT: str` setting (default: `"table"`) that controls the default output format for the `hecate` CLI when no `--json` flag is provided. This is a server-side setting only and does not affect API behavior.

#### Scenario: Default output format
- **WHEN** no `CLI_DEFAULT_OUTPUT` environment variable is set
- **THEN** the CLI SHALL default to table output format

#### Scenario: JSON output format
- **WHEN** `CLI_DEFAULT_OUTPUT=json` is set
- **THEN** the CLI SHALL default to JSON output format unless overridden by command flags
