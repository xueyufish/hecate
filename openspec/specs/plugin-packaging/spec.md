## ADDED Requirements

### Requirement: Plugin bundle format
The system SHALL support a `.hecate-plugin` bundle format — a ZIP archive containing a valid plugin directory structure (`plugin.yaml` + Python source files + optional `requirements.txt`). The packaging function SHALL validate that `plugin.yaml` exists and contains required fields before creating the bundle.

#### Scenario: Package a valid plugin directory
- **WHEN** a developer runs `hecate plugin package ./my-plugin`
- **THEN** the system creates `my-plugin.hecate-plugin` ZIP file containing all files from the directory

#### Scenario: Reject directory without plugin.yaml
- **WHEN** a developer runs `hecate plugin package ./not-a-plugin` and no `plugin.yaml` exists
- **THEN** the system rejects with an error message

#### Scenario: Bundle contains requirements.txt
- **WHEN** a plugin directory contains `requirements.txt`
- **THEN** the bundle includes it and the installer will install dependencies after extraction

### Requirement: Plugin install from bundle
The system SHALL support installing a `.hecate-plugin` bundle. Installation SHALL: extract the ZIP to the `plugins/` directory, install Python dependencies from `requirements.txt` via `uv pip install`, create or update a PluginModel record, and load the plugin via the existing PluginLoader.

#### Scenario: Install new plugin
- **WHEN** an administrator runs `hecate plugin install my-plugin.hecate-plugin`
- **THEN** the system extracts the bundle to `plugins/my-plugin/`, installs dependencies, creates a PluginModel record, and the plugin appears in the plugin list

#### Scenario: Install upgrades existing plugin
- **WHEN** an administrator installs a bundle whose plugin name already exists with an older version
- **THEN** the system overwrites the existing directory, updates the PluginModel version field, and reloads the plugin

#### Scenario: Install invalid bundle
- **WHEN** an administrator attempts to install a corrupted or non-ZIP file
- **THEN** the system rejects with an error and does not modify the plugins directory

### Requirement: Plugin uninstall
The system SHALL support uninstalling a plugin. Uninstall SHALL: delete the plugin directory from `plugins/`, delete the PluginModel record, and unregister from PluginRegistry.

#### Scenario: Uninstall installed plugin
- **WHEN** an administrator runs `hecate plugin uninstall my-plugin`
- **THEN** the system removes `plugins/my-plugin/`, deletes the PluginModel record, and the plugin no longer appears in the plugin list

#### Scenario: Uninstall non-existent plugin
- **WHEN** an administrator runs `hecate plugin uninstall nonexistent`
- **THEN** the system reports that the plugin is not installed

### Requirement: Upload plugin via REST API
The system SHALL expose a `POST /api/plugins/upload` endpoint that accepts a `.hecate-plugin` file upload. The backend SHALL extract, install dependencies, and register the plugin.

#### Scenario: Upload valid bundle
- **WHEN** a client uploads a valid `.hecate-plugin` file to `POST /api/plugins/upload`
- **THEN** the system installs the plugin and returns the PluginReadSchema

#### Scenario: Upload invalid file
- **WHEN** a client uploads a non-ZIP file
- **THEN** the system returns a 400 error

### Requirement: Delete plugin via REST API
The system SHALL expose a `DELETE /api/plugins/{id}` endpoint that uninstalls a plugin.

#### Scenario: Delete installed plugin
- **WHEN** a client sends `DELETE /api/plugins/{id}`
- **THEN** the system uninstalls the plugin and returns 200

#### Scenario: Delete built-in plugin rejected
- **WHEN** a client sends `DELETE /api/plugins/{id}` for a built-in plugin
- **THEN** the system returns 403 with "Built-in plugins cannot be uninstalled"

### Requirement: Upload plugin UI
The system SHALL provide an "Upload Plugin" button on the plugin management page that opens a file picker for `.hecate-plugin` files. On successful upload, the plugin list refreshes.

#### Scenario: Upload via UI
- **WHEN** an administrator clicks "Upload Plugin" and selects a `.hecate-plugin` file
- **THEN** the system uploads the file, installs the plugin, and the new plugin appears in the list

### Requirement: Uninstall plugin UI
The system SHALL provide an "Uninstall" button on the plugin detail page. Built-in plugins SHALL NOT show the uninstall button.

#### Scenario: Uninstall via UI
- **WHEN** an administrator clicks "Uninstall" on a third-party plugin detail page
- **THEN** the system uninstalls the plugin and redirects to the plugin list

#### Scenario: Built-in plugin has no uninstall button
- **WHEN** an administrator views a built-in plugin detail page
- **THEN** the "Uninstall" button is not displayed
