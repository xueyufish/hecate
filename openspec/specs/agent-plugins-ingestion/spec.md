## Purpose

Ingests third-party Agent Plugins 1.0 packages (plugin.json manifest + skills/ directories + mcp.json, per the agent-plugins.org 1.0.0 specification) as Hecate's ecosystem-facing plugin format: packages are validated offline, materialized as immutable snapshots, and projected into the platform as SkillModel rows plus MCP server registrations, with component-level trust dispatch (skills → T4, http/sse MCP → T2, stdio MCP → sandboxed platform install) per ADR-029. Content scanning ships as the plugin-content-scanning capability (feature 5.13a) and is enforced inside this pipeline's scan stage.

## ADDED Requirements

### Requirement: Install sources and immutable materialization
The system SHALL accept three install sources for Agent Plugins packages: a local directory path, a git URL (public repositories only in v1, no credential support), and a ZIP file used strictly as transport. Every source SHALL be materialized into a managed install directory as an immutable snapshot; the original source location SHALL NOT be referenced after install completes. Git installs SHALL record the resolved ref, the commit SHA, and a content digest of the materialized tree (pin-by-hash provenance triple).

#### Scenario: Directory install materialized
- **WHEN** a package is installed from a local directory path
- **THEN** the system copies the package tree into the managed install directory, and later edits to the source directory have no effect on the installed package

#### Scenario: Git install records provenance triple
- **WHEN** a package is installed from `https://github.com/org/repo`
- **THEN** the system clones the repository, materializes the package tree, and records ref, commit SHA, and content digest in the package origin

#### Scenario: Zip accepted only as transport
- **WHEN** a package is installed from an uploaded ZIP file
- **THEN** the system extracts it into the managed install directory and does not retain the ZIP as the runtime source

#### Scenario: Git clone failure aborts install
- **WHEN** a git install source cannot be cloned (network error or repository not found)
- **THEN** the install fails with a structured error and no PluginModel row or directory is persisted

### Requirement: Closed-manifest validation
The system SHALL validate plugin.json offline against the closed Agent Plugins 1.0.0 content model with exactly 10 permitted top-level fields: `$schema`, `name`, `version`, `description`, `author`, `homepage`, `repository`, `license`, `keywords`, `extensions`. Unknown top-level fields SHALL produce a warning and be ignored (no semantics assigned). Any other violation — missing or invalid required field, invalid `name` grammar, unrecognized `$schema` version, malformed `author` — SHALL reject the entire package. The validator SHALL NOT fetch any remote schema.

#### Scenario: Valid manifest accepted
- **WHEN** a plugin.json with valid `$schema` and `name` is validated
- **THEN** the package proceeds to component discovery

#### Scenario: Unknown top-level field warns and continues
- **WHEN** plugin.json contains an 11th top-level field `x-custom`
- **THEN** the system logs a warning, ignores the field, and continues validation

#### Scenario: Unrecognized schema version rejected
- **WHEN** plugin.json declares `$schema` for an unsupported version
- **THEN** the package is rejected and no components are imported

#### Scenario: Invalid name grammar rejected
- **WHEN** plugin.json declares a `name` violating the 1.0.0 name grammar (e.g. uppercase characters)
- **THEN** the package is rejected with a validation error

### Requirement: Fixed-location component discovery with skip-and-continue
Skills SHALL be discovered only in immediate child directories of `skills/` that contain a `SKILL.md` file; recursive search of deeper descendants SHALL NOT occur. `mcp.json` SHALL be read only at the package root. A non-conforming skill SHALL be skipped with a warning while remaining skills import. An invalid `mcp.json` SHALL disable only the MCP component while skills still import. Every filesystem path the ingester touches SHALL resolve inside the package root; symlink escapes SHALL be rejected with that path or component denied.

#### Scenario: Discovery is non-recursive
- **WHEN** a package contains `skills/a/SKILL.md` and `skills/a/nested/SKILL.md`
- **THEN** only `skills/a` is discovered as a skill; the nested SKILL.md is treated as supporting data

#### Scenario: Non-conforming skill skipped
- **WHEN** one skill directory has invalid frontmatter and two others are valid
- **THEN** the invalid skill is skipped with a warning and the two valid skills import

#### Scenario: Invalid mcp.json disables MCP only
- **WHEN** a package has valid skills but mcp.json fails schema validation
- **THEN** all skills import and the MCP component is marked disabled with the reason recorded in the component inventory

#### Scenario: Symlink escape rejected
- **WHEN** a skill's supporting file is a symlink resolving outside the package root
- **THEN** the system rejects that path access and skips the affected component without aborting the install

### Requirement: SKILL.md import into SkillModel
Each discovered skill SHALL be imported by reusing the existing SKILL.md parser: frontmatter `name` and `description` map to model fields, the Markdown body maps to `instructions`, and optional `license`, `compatibility`, `metadata`, `allowed-tools` frontmatter SHALL be stored as JSON. Imported rows SHALL carry `source="plugin"`, the owning plugin id, and the package origin. As a Hecate hardening beyond the standard, the frontmatter `name` SHALL equal the skill directory name; a mismatch SHALL skip the skill with a warning. Existing SkillLoader token budgets SHALL apply to imported skills unchanged.

#### Scenario: Valid skill imported with provenance
- **WHEN** a conforming skill directory `skills/deploy` with matching frontmatter name is imported
- **THEN** a SkillModel row is created with `source="plugin"`, `plugin_id` set to the package, and `origin` set to the package origin

#### Scenario: Name-directory mismatch skipped
- **WHEN** directory `skills/deploy` contains a SKILL.md whose frontmatter `name` is `ship-it`
- **THEN** the skill is skipped with a warning and installation of remaining components continues

#### Scenario: Optional frontmatter preserved
- **WHEN** an imported skill declares `license` and `allowed-tools` in frontmatter
- **THEN** both values are stored as JSON on the skill record

### Requirement: mcp.json projection into MCP registry
For each `mcpServers` entry of type `streamable-http` or `sse`, the system SHALL register an MCP server through the existing connection management path (connection pool, circuit breaker, reconnection) with registration name `<plugin-name>__<server-name>`. `sse` entries SHALL map to the streamable-http transport. Non-loopback URLs SHALL be required to use HTTPS. Header values SHALL NOT contain credentials; an entry violating this SHALL be rejected for that server only.

#### Scenario: Http server registered with prefixed name
- **WHEN** package `docs-helper` declares an mcp.json entry `search` of type `streamable-http`
- **THEN** the system registers the server under the name `docs-helper__search` scoped to the installing workspace

#### Scenario: Sse entry mapped to streamable-http
- **WHEN** an mcp.json entry declares type `sse`
- **THEN** the system registers it through the streamable-http transport

#### Scenario: Plaintext URL on non-loopback rejected per entry
- **WHEN** an mcp.json entry declares `http://api.example.com/mcp` (non-loopback, no TLS)
- **THEN** that server entry is rejected with a recorded reason and other components install

### Requirement: Component-level trust dispatch
Skills and streamable-http/sse MCP entries SHALL be installable by a workspace admin into their workspace. stdio entries (local subprocess execution) SHALL be installable only at platform level by a platform installer designated through a configuration allowlist; the subprocess SHALL execute inside the container sandbox pool under a command allowlist (default `npx`/`uvx`) with fail-closed semantics — any policy application failure denies execution. When stdio installation is not permitted (installer not allowlisted, or SaaS deployment mode), stdio entries SHALL be skipped with a recorded warning while the rest of the package installs.

#### Scenario: Workspace admin installs skills and http MCP
- **WHEN** a workspace admin installs a package containing skills and streamable-http entries and no stdio entries
- **THEN** the install succeeds at workspace scope

#### Scenario: Stdio requires platform installer
- **WHEN** a package containing a stdio entry is installed by a user not on the platform installer allowlist
- **THEN** the stdio entry is skipped with a recorded warning and remaining components install at workspace scope

#### Scenario: Stdio command outside allowlist denied
- **WHEN** a platform-installed stdio entry declares a command not on the allowlist, or a sandbox policy cannot be applied
- **THEN** execution is denied (fail-closed) and the denial is recorded

#### Scenario: Stdio subprocess runs in sandbox
- **WHEN** a permitted stdio MCP server starts
- **THEN** the subprocess executes inside the container sandbox pool with the plugin root mounted per spec placeholder semantics (`${PLUGIN_ROOT}`/`${PLUGIN_DATA}`)

### Requirement: Package record and provenance
Each installed package SHALL persist exactly one PluginModel row with `type="agent-plugin"`, status, workspace scope, provenance fields `origin` (source descriptor including git pin triple when applicable), `content_hash`, and `scan_result` (carrying the content-scan verdict, findings, and scanner version once feature 5.13a is active), and a manifest JSON containing the full plugin.json plus a component inventory listing each skill and MCP server with its import outcome. The install directory and the database row SHALL be created and removed as a pair; at startup, install directories without a matching row SHALL be removed as orphans.

#### Scenario: Package row carries provenance
- **WHEN** a package installs successfully
- **THEN** its PluginModel row contains origin, content_hash, and a component inventory with per-component outcomes

#### Scenario: Orphan directory cleaned at startup
- **WHEN** the managed install directory contains a package directory with no matching PluginModel row
- **THEN** the system removes the orphan directory at startup and logs the cleanup

### Requirement: Bare SKILL.md directory acceptance
A directory containing skill directories with SKILL.md but no plugin.json SHALL be accepted as a virtual package (Claude Code ecosystem compatibility): the system SHALL synthesize a package identity from the directory name, record it as a PluginModel row marked virtual, and import skills normally so uninstall semantics stay uniform.

#### Scenario: Bare directory installed as virtual package
- **WHEN** a directory with `skills-a/SKILL.md` but no plugin.json is installed
- **THEN** the system creates a virtual package record named after the directory and imports the skill with `source="plugin"`

#### Scenario: Virtual package uninstalls uniformly
- **WHEN** a virtual package is uninstalled
- **THEN** its imported skills are removed exactly as for a standard package

### Requirement: Reinstall and collision semantics
Reinstalling a package whose name already exists with the same origin SHALL upsert: previously imported skills of that plugin are removed, components re-imported, and version and provenance updated. Reinstalling the same package name from a different origin SHALL be rejected with a clear error. A package whose imported skill name collides with an existing non-plugin skill in the same workspace SHALL be rejected with the conflicting skill names listed.

#### Scenario: Same-origin reinstall upserts
- **WHEN** a package is reinstalled from the same origin at a newer version
- **THEN** prior plugin-sourced skills are replaced by the new import and the row's version is updated

#### Scenario: Different-origin reinstall rejected
- **WHEN** a package name exists with origin A and an install arrives from origin B
- **THEN** the install is rejected with an error identifying the existing origin

#### Scenario: Collision with user skill rejected
- **WHEN** an imported skill name equals an existing user-created skill name in the same workspace
- **THEN** the install is rejected listing the conflicting skill names

### Requirement: Enable as single source of truth
The package enable state SHALL be the single source of truth projected to both runtime surfaces: enabling registers the package's MCP servers (and permits stdio execution), disabling unregisters MCP servers and hides imported skills from skill loading. At startup, MCP registrations SHALL be replayed for every enabled agent-plugin package.

#### Scenario: Disable hides skills and unregisters MCP
- **WHEN** an enabled package is disabled
- **THEN** its MCP servers are unregistered and its imported skills become invisible to skill loading

#### Scenario: Startup replay re-registers
- **WHEN** the platform restarts with an enabled agent-plugin package carrying http MCP entries
- **THEN** the servers are re-registered into the MCP registry without user action

### Requirement: Package size caps
Installation SHALL enforce configurable size caps: by default 100 MB per package and 500 MB aggregate per workspace (platform-level installs count against a platform aggregate). Installs exceeding a cap SHALL be rejected with the measured size reported.

#### Scenario: Oversized package rejected
- **WHEN** a package directory tree measures 150 MB against a 100 MB cap
- **THEN** the install is rejected with the measured and allowed sizes in the error

### Requirement: Scan stage slot
The ingestion pipeline SHALL invoke the content scanner between validation and persistence, and re-invoke it on enable. The scan stage SHALL produce a verdict of allow, warn, or block: a block verdict SHALL abort the install before any row or directory is persisted, and a scanner failure SHALL abort the install (fail-closed). Successful installs SHALL persist the scan verdict, findings, and scanner version into `scan_result`; enable-time rescans SHALL apply per the plugin-content-scanning capability.

#### Scenario: Block verdict aborts install
- **WHEN** a package's content scan yields a block verdict
- **THEN** the install is rejected with findings in the error and no PluginModel row or directory is persisted

#### Scenario: Successful install persists scan result
- **WHEN** a package installs with a warn verdict
- **THEN** the PluginModel row's `scan_result` carries the verdict, findings, and scanner version

#### Scenario: Scan failure aborts install
- **WHEN** the content scanner raises an unexpected error during install
- **THEN** the install is rejected with a scanner-failure error

### Requirement: Master switch
All ingestion entry points (REST API and CLI) SHALL be gated by a configuration switch. With feature 5.13a shipped, the switch SHALL default to on; setting it off SHALL make install endpoints return a feature-disabled error and the CLI report the feature disabled. The switch SHALL serve as an emergency kill-switch at all times.

#### Scenario: Default-on allows install
- **WHEN** the configuration switch is at its default (on) and an install is requested
- **THEN** the install proceeds through validation and scanning

#### Scenario: Kill-switch rejects install
- **WHEN** the configuration switch is off and an install is requested
- **THEN** the API returns a feature-disabled error and no state changes

### Requirement: Ingestion API and CLI
The system SHALL expose `POST /api/plugins/agent-plugins/install` accepting a source descriptor (`type`: `dir` | `git` | `zip` plus location), returning the created package summary with component inventory. Existing plugin list, enable, disable, and delete endpoints SHALL operate on agent-plugin rows unchanged. A minimal CLI subset — `install`, `uninstall`, `list` with `--source` — SHALL be provided (reused later by the 12.0 marketplace installer).

#### Scenario: API install returns summary
- **WHEN** `POST /api/plugins/agent-plugins/install` is called with a valid git source
- **THEN** the API returns 201 with package identity, provenance, and per-component outcomes

#### Scenario: CLI install from git URL
- **WHEN** `hecate plugin install --source git https://github.com/org/repo` runs
- **THEN** the package installs and the CLI prints the component summary

#### Scenario: Existing endpoints serve agent-plugin rows
- **WHEN** `GET /api/plugins` is called with type filter `agent-plugin`
- **THEN** installed agent-plugin packages are listed with their status and provenance

### Requirement: Uninstall cascade
Uninstalling an agent-plugin package SHALL, in one transaction: delete imported SkillModel rows by plugin id, unregister its MCP servers, remove the PluginModel row, and delete the install directory. A failure at any step SHALL roll back the entire uninstall.

#### Scenario: Uninstall removes all artifacts
- **WHEN** an installed package with 3 skills and 1 MCP server is uninstalled
- **THEN** the skills, MCP registration, plugin row, and install directory are all removed

#### Scenario: Uninstall failure rolls back
- **WHEN** directory deletion fails during uninstall
- **THEN** the database changes are rolled back and the package remains installed with the error reported
