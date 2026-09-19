# Spec Delta

## MODIFIED Requirements

### Requirement: Plugin bundle format
The system SHALL support two `.hecate-plugin` bundle layouts. The legacy layout is a ZIP archive whose root contains `plugin.yaml` plus Python source files and optional `requirements.txt`. The dual-format layout is a ZIP archive of an Agent Plugins 1.0 package: `plugin.json` at the root, optional `skills/` and `mcp.json`, and the Hecate namespace directory (`io.github.xueyufish/`) carrying `plugin.yaml` and any code payload. Packaging SHALL validate the source directory before creating the bundle: the legacy layout requires `plugin.yaml` with required fields at the root; the dual-format layout requires `plugin.json` plus a namespace manifest. Unpacking a `.hecate-plugin` file outside Hecate SHALL yield a valid Agent Plugins package for the dual-format layout.

#### Scenario: Package a valid plugin directory
- **WHEN** a developer runs `hecate plugin package ./my-plugin` on a directory with root `plugin.yaml` and no plugin.json
- **THEN** the system creates `my-plugin.hecate-plugin` containing the legacy layout with `plugin.yaml` at the root and all files from the directory

#### Scenario: Reject directory without plugin.yaml
- **WHEN** a developer runs `hecate plugin package ./not-a-plugin` and the directory has neither root `plugin.yaml` nor a valid dual-format manifest set (plugin.json plus namespace manifest)
- **THEN** the system rejects with an error message

#### Scenario: Bundle contains requirements.txt
- **WHEN** a legacy plugin directory contains `requirements.txt`
- **THEN** the bundle includes it and the installer will install dependencies after extraction

#### Scenario: Legacy bundle stays installable
- **WHEN** an administrator installs a previously distributed legacy `.hecate-plugin` bundle
- **THEN** the system installs it through the legacy path exactly as before this change

#### Scenario: Dual-format bundle is a valid Agent Plugins package when unzipped
- **WHEN** a dual-format `.hecate-plugin` bundle is unzipped to a directory
- **THEN** the directory contains plugin.json at the root and validates as an Agent Plugins 1.0 package, with the Hecate manifest inside the namespace directory

## ADDED Requirements

### Requirement: Dual-format packaging output
`hecate plugin package` SHALL emit the dual-format layout: it generates a plugin.json from the plugin.yaml fields (name, version, description; author and homepage when provided), writes the plugin.yaml manifest into the Hecate namespace directory (omitting name and version, which live in plugin.json alone), keeps the Python payload inside the namespace directory, and passes through `skills/` and `mcp.json` when present. The output SHALL be produced uniformly even when the open face is empty (no skills and no mcp.json). Default output is a git-ready directory; an output path ending in `.hecate-plugin` SHALL produce the ZIP transport of the same tree.

#### Scenario: Package emits dual-format layout
- **WHEN** a developer runs `hecate plugin package ./my-tool` on a directory with `plugin.yaml` (type tool, entry) and Python sources
- **THEN** the output directory contains plugin.json at the root and `io.github.xueyufish/` with plugin.yaml and the Python payload, and installs in Hecate as an agent-plugin package with a working code component

#### Scenario: Empty open face still emits
- **WHEN** the packaged plugin has no skills and no mcp.json
- **THEN** the output is still a conformant Agent Plugins package with plugin.json and the namespace directory only

#### Scenario: Other clients ignore the namespace
- **WHEN** the emitted package is loaded by an ecosystem client that does not implement the Hecate namespace
- **THEN** the client sees a conformant package with its plugin.json, skills, and mcp.json, and ignores the namespace directory

### Requirement: Directory and git install sources with ZIP as transport
The Hecate plugin install surface SHALL accept three source types: a local directory path, a git URL (public repositories, optional ref), and a `.hecate-plugin` or `.zip` file used strictly as transport. Every source SHALL be materialized before validation, and layout detection SHALL route the materialized tree: a root `plugin.json` (dual-format) installs through the Agent Plugins pipeline, where the resolved origin is recorded (git ref, commit SHA, content digest); a root `plugin.yaml` (legacy) installs through the legacy installer path, which materializes by copying into the plugins directory and keeps the existing plugin-row semantics.

#### Scenario: Install from directory
- **WHEN** an administrator runs `hecate plugin install --source dir ./my-tool` on a packaged dual-format directory
- **THEN** the tree is recognized as dual-format and installed as an agent-plugin package with its components inventoried

#### Scenario: Install from git URL records provenance
- **WHEN** an administrator installs a dual-format package from a git URL with a ref
- **THEN** the system clones, materializes, and records the ref, commit SHA, and content digest in the package origin

#### Scenario: ZIP demoted to transport
- **WHEN** an administrator installs from a `.hecate-plugin` file
- **THEN** the archive is extracted to a staging tree and the original file is not referenced after install completes
