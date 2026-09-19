# Spec Delta

## ADDED Requirements

### Requirement: Hecate namespace extension recognition
After plugin.json passes closed-manifest validation, the ingestion pipeline SHALL look up the `extensions` entry keyed by the Hecate namespace (`io.github.xueyufish`) and the same-named top-level directory per the spec's extension-directory rule. The namespace directory SHALL be the content source: it contains a `plugin.yaml` manifest carrying the Hecate-private fields (type, entry, permissions, config_schema, optional metadata). The `extensions` value for the Hecate namespace MAY exist as pointer metadata only and SHALL NOT override directory content; a non-object value SHALL produce a warning and be ignored. plugin.json SHALL be the sole identity source: a namespace manifest whose name or version differs from plugin.json SHALL reject the package; matching or absent values SHALL pass. `extensions` entries keyed by any other namespace, and directories named for other namespaces, SHALL be ignored without assigning semantics. A namespace directory whose plugin.yaml is present but malformed SHALL degrade to a plain agent-plugin install with a recorded warning (skip-and-continue).

#### Scenario: Dual-format package recognized
- **WHEN** a package contains plugin.json and a `io.github.xueyufish/plugin.yaml` declaring type and entry
- **THEN** the package installs as one agent-plugin row whose identity (name, version) comes from plugin.json, and the namespace manifest is attached to the package record

#### Scenario: Identity conflict rejected
- **WHEN** the namespace plugin.yaml declares version `2.0.0` while plugin.json declares `1.0.0`
- **THEN** the install is rejected with an error naming the conflicting identity field

#### Scenario: Other namespaces ignored
- **WHEN** a package declares `extensions: {"com.other.client": {...}}` and ships a `com.other.client/` directory
- **THEN** the ingestion ignores both without validation, and the package installs by its open face alone

#### Scenario: Malformed namespace manifest degrades
- **WHEN** `io.github.xueyufish/plugin.yaml` exists but fails to parse or violates the manifest content model
- **THEN** the package installs as a plain agent-plugin with the namespace error recorded as a warning, and no Hecate-private component is imported

### Requirement: Dual-format code component
A namespace manifest MAY declare an `entry` (python grammar) whose code payload SHALL live inside the namespace directory. The plugin loader SHALL resolve entry modules with the namespace directory on the module search path. Enabling a dual-format package whose code component passed trust gating SHALL register the code plugin through the existing loader path; disabling SHALL unregister it. At startup, code components of enabled dual-format packages SHALL be re-registered together with the existing MCP replay. Root-level loader directory discovery SHALL NOT pick up dual-format packages (no `plugin.yaml` at package root), so loading happens exclusively through the enable projection.

#### Scenario: Enable registers code component
- **WHEN** an enabled dual-format package declares a permitted entry and the platform starts or the package is enabled
- **THEN** the code plugin is loaded from the namespace payload and appears in the plugin registry, alongside the package's skills and MCP registrations

#### Scenario: Disable unregisters code component
- **WHEN** an enabled dual-format package with a code component is disabled
- **THEN** the code plugin is unregistered, its skills become invisible to skill loading, and its MCP servers are unregistered

#### Scenario: Root discovery does not double-load
- **WHEN** the loader scans the plugins directory at startup
- **THEN** a dual-format package directory (plugin.json at root, manifest under the namespace directory) is not loaded as a legacy root-manifest plugin

### Requirement: Namespace package trust tier
The install scope of a dual-format package SHALL be the highest trust tier among its components: a code entry requires platform-level installation under the T0 python-entry gates (first-party/allowlist policy, reusing the existing entry check); stdio, streamable-http, and skill components keep their existing tiers. When the code component is not permitted for the requesting installer (not on the platform installer allowlist, or the deployment policy denies it), the code component SHALL be skipped with a recorded warning while the declarative components install — mirroring the stdio behavior. The namespace manifest's `permissions` entries SHALL be carried into the package record and audited by the content scanner.

#### Scenario: Workspace admin installs declarative-only dual-format package
- **WHEN** a dual-format package carries skills and an http MCP entry but no code entry, and a workspace admin installs it
- **THEN** the install succeeds at workspace scope with all components imported

#### Scenario: Code entry requires platform installer
- **WHEN** a dual-format package declares a code entry and the requester is not a platform installer
- **THEN** the code component is skipped with a recorded warning, the skills and MCP components install, and the component inventory marks the code component denied

#### Scenario: Code entry passes T0 gates
- **WHEN** a platform installer installs a dual-format package whose entry resolves to a module outside the first-party prefix and outside the configured allowlist
- **THEN** the code component is denied by the existing T0 entry policy with the denial recorded, and declarative components install
