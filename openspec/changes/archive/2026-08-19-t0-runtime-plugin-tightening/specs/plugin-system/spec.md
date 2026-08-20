## MODIFIED Requirements

### Requirement: Entry loading via python: prefix
The system SHALL load plugins with `entry: python:module:Class` format using `importlib.import_module()` to import the module and instantiate the class, subject to the T0 trust gate for in-process plugin entries. The loaded instance SHALL be registered with `PluginRegistry`. When the trust gate rejects the entry, the system SHALL skip the plugin without crashing the discovery loop: log an ERROR naming the T0 policy as the rejection reason, count the plugin in the discovery error count, and neither register nor persist it.

#### Scenario: Load first-party Python plugin
- **WHEN** a plugin manifest declares `entry: python:hecate.plugins.my_plugin:MyToolPlugin`
- **THEN** the system imports the module, instantiates `MyToolPlugin`, and registers it with `PluginRegistry` in both SaaS and self-hosted modes

#### Scenario: Rejected entry skipped without crashing discovery
- **WHEN** a discovered plugin's `python:` entry is rejected by the T0 trust gate
- **THEN** the system logs an ERROR naming the T0 policy, counts the plugin as an error, continues discovering remaining plugins, and does not register or persist the rejected plugin

#### Scenario: Invalid Python entry
- **WHEN** a plugin manifest declares `entry: python:nonexistent:Class`
- **THEN** the system catches `ImportError`, logs the error, and marks the plugin with status `error`

## ADDED Requirements

### Requirement: T0 trust gate for in-process plugin entries
The loader SHALL apply a fail-closed trust gate before importing any `python:` entry: a module is loadable in-process only when it is first-party (`hecate` exactly, or any module under the `hecate.` namespace) or when it matches a configured allowlist prefix under self-hosted deployment. Prefix matching SHALL respect module-name segment boundaries: a configured prefix matches a module only when it equals the module or is an ancestor package of it (prefix `mycompany.` matches `mycompany.tools.x` but not `mycompanyevil.x`). In SaaS mode (`SAAS_MODE=true`) non-first-party modules SHALL be rejected outright and the allowlist SHALL NOT grant exceptions. In self-hosted mode non-first-party modules SHALL be rejected by default (default-deny) and loadable only when the module matches a prefix in `PLUGIN_PYTHON_ENTRY_ALLOWLIST`. Third-party content SHALL NOT be able to inject modules into the first-party namespace: plugin directories are not placed on the module search path.

#### Scenario: SaaS rejects non-first-party python entry
- **WHEN** `SAAS_MODE=true` and a discovered plugin declares `entry: python:my_plugin:MyToolPlugin`
- **THEN** the loader rejects the entry before import, regardless of any allowlist configuration

#### Scenario: Self-hosted default-deny for non-first-party entry
- **WHEN** `SAAS_MODE=false`, `PLUGIN_PYTHON_ENTRY_ALLOWLIST` is empty, and a discovered plugin declares `entry: python:my_plugin:MyToolPlugin`
- **THEN** the loader rejects the entry before import

#### Scenario: Self-hosted allowlist prefix grants loading
- **WHEN** `SAAS_MODE=false`, `PLUGIN_PYTHON_ENTRY_ALLOWLIST=["mycompany."]`, and a discovered plugin declares `entry: python:mycompany.tools.weather:WeatherPlugin`
- **THEN** the loader imports the module and loads the plugin

#### Scenario: Allowlist prefix does not cross segment boundaries
- **WHEN** `SAAS_MODE=false`, `PLUGIN_PYTHON_ENTRY_ALLOWLIST=["mycompany."]`, and a discovered plugin declares `entry: python:mycompanyevil.exfil:EvilPlugin`
- **THEN** the loader rejects the entry before import

#### Scenario: First-party namespace root is loadable
- **WHEN** a discovered plugin declares an entry whose module is exactly `hecate`
- **THEN** the loader permits the import in both SaaS and self-hosted modes

### Requirement: Install-time rejection of untrusted python entries
The plugin installation path SHALL apply the same T0 trust gate when installing a `.hecate-plugin` bundle whose manifest declares a `python:` entry: installation SHALL fail with an error that identifies the trust gate and names the remediation (remove the plugin or, on self-hosted, add its module prefix to the allowlist), and the just-extracted plugin directory SHALL be removed so no rejected artifact remains on disk. Bundles whose `python:` entry passes the gate, and bundles without `python:` entries, SHALL install normally.

#### Scenario: Bundle with non-first-party python entry fails installation
- **WHEN** a `.hecate-plugin` bundle declares `entry: python:my_plugin:MyToolPlugin` and the gate rejects the module under the current deployment mode
- **THEN** installation fails with an error naming the trust gate and the remediation, and the extracted plugin directory is removed

#### Scenario: Bundle with first-party python entry installs
- **WHEN** a `.hecate-plugin` bundle declares `entry: python:hecate.plugins.my_plugin:MyToolPlugin`
- **THEN** installation succeeds

### Requirement: Runtime dependency installation disabled in SaaS mode
In SaaS mode the installer SHALL NOT execute runtime dependency installation (`pip install` of a plugin's `requirements.txt`): the step SHALL be skipped with a WARNING log. In self-hosted mode runtime dependency installation SHALL continue to run, with imported third-party modules still subject to the T0 trust gate at load time.

#### Scenario: SaaS skips requirements installation
- **WHEN** `SAAS_MODE=true` and a bundle containing `requirements.txt` is installed
- **THEN** the installer skips dependency installation and logs a WARNING, and the installation itself proceeds

#### Scenario: Self-hosted installs requirements
- **WHEN** `SAAS_MODE=false` and a bundle containing `requirements.txt` is installed
- **THEN** the installer runs dependency installation as before
