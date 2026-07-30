## Context — 背景

Hecate's Plugin System has two layers completed: 5.5 ✅ (runtime engine — plugin.yaml loading, directory discovery, PluginModel DB, config, permissions, REST API, frontend) and TP5 ✅ (8 plugin type ABCs, hecate.plugin SDK, hecate plugin init CLI, hot-reload, API validation, creation UI). Plugins are currently loaded from a local `plugins/` directory — there is no packaging, distribution, or install/uninstall workflow.

Hecate 的插件系统已完成两层：5.5 ✅（运行时引擎 — plugin.yaml 加载、目录发现、PluginModel DB、配置、权限、REST API、前端）和 TP5 ✅（8 种插件类型 ABC、hecate.plugin SDK、hecate plugin init CLI、热重载、API 验证、创建 UI）。插件当前从本地 `plugins/` 目录加载 — 没有打包、分发或安装/卸载工作流。

**Research basis — 研究基础**: Enterprise platforms (Dify, AgentArts, Versatile, deer-flow, Salesforce) all use ZIP/archive-based package formats for plugin distribution. None use pip or Git URL for enterprise plugin deployment. All support platform-managed installation through UI or API.

企业平台（Dify、AgentArts、Versatile、deer-flow、Salesforce）都使用基于 ZIP/归档的包格式进行插件分发。没有企业使用 pip 或 Git URL 进行企业级插件部署。所有平台都通过 UI 或 API 支持平台管理的安装。

## Goals / Non-Goals — 目标/非目标

**Goals — 目标:**
- `.hecate-plugin` ZIP bundle format (plugin.yaml + Python source + requirements.txt)
- `hecate plugin package` CLI — package a directory into a bundle
- `hecate plugin install` CLI — install from a bundle file
- `hecate plugin uninstall` CLI — remove a plugin
- Upload/install UI on plugin management page
- Uninstall UI on plugin detail page
- Post-install dependency handling (`uv pip install -r requirements.txt`)
- Version upgrade (overwrite existing plugin on newer version install)

**Non-Goals — 非目标:**
- Plugin marketplace/search/discovery — P5 12.0 Asset Marketplace
- Plugin signing/security scanning — P5 5.13 Plugin Security & Signing
- Inter-plugin dependency resolution — no enterprise platform does this
- Plugin rollback to previous version — future enhancement

## Decisions — 决策

### Decision 1: ZIP-based `.hecate-plugin` format — 基于 ZIP 的 `.hecate-plugin` 格式

**Choice — 选择**: ZIP archive containing the plugin directory structure.

**Rationale — 理由**: All enterprise platforms (Dify `.difypkg`, deer-flow `.skill`, Salesforce metadata package) use ZIP archives. ZIP is universally supported, works with UI file upload, and Python's `zipfile` module handles it natively without external dependencies.

所有企业平台（Dify `.difypkg`、deer-flow `.skill`、Salesforce 元数据包）都使用 ZIP 归档。ZIP 被普遍支持，适用于 UI 文件上传，Python 的 `zipfile` 模块无需外部依赖即可原生处理。

### Decision 2: Dependency installation via `uv pip install` — 通过 `uv pip install` 安装依赖

**Choice — 选择**: After extracting the bundle, run `uv pip install -r requirements.txt` in the venv.

**Rationale — 理由**: Hecate already uses `uv` for dependency management. The plugin's `requirements.txt` lists additional Python packages needed by the plugin. Installation happens once at install time, not at every startup.

Hecate 已经使用 `uv` 进行依赖管理。插件的 `requirements.txt` 列出了插件需要的额外 Python 包。安装仅在安装时发生一次，而不是每次启动时。

### Decision 3: Version upgrade = overwrite — 版本升级 = 覆盖

**Choice — 选择**: Installing a plugin whose name already exists overwrites the old directory and updates the PluginModel version field.

**Rationale — 理由**: No enterprise platform does complex version migration for plugins. Simple overwrite is predictable and matches Dify/AgentArts behavior. The `api_version` compatibility check (from 5.5) ensures the new version is compatible with the platform.

没有企业平台对插件进行复杂的版本迁移。简单的覆盖是可预测的，与 Dify/AgentArts 的行为一致。`api_version` 兼容性检查（来自 5.5）确保新版本与平台兼容。

### Decision 4: CLI extends existing `hecate plugin` command — CLI 扩展现有的 `hecate plugin` 命令

**Choice — 选择**: Add `package`, `install`, `uninstall` subcommands to the existing `hecate plugin` CLI from TP5.

## Risks / Trade-offs — 风险/权衡

- **[Malicious packages in requirements.txt — requirements.txt 中的恶意包]** → A plugin could declare malicious dependencies. Mitigation: 5.13 (P5) will add package signing + security scanning. For now, plugins are installed by trusted administrators.
  → 插件可能声明恶意依赖。缓解措施：5.13（P5）将添加包签名 + 安全扫描。目前，插件由受信任的管理员安装。

- **[Dependency conflicts — 依赖冲突]** → Two plugins requiring different versions of the same package. Mitigation: same as 5.5 — document that plugins should pin compatible versions. Per-plugin virtual environments are future work.
  → 两个插件需要不同版本的同一包。缓解措施：与 5.5 相同 — 记录插件应锁定兼容版本。每个插件独立的虚拟环境是未来的工作。

- **[Bundle size — Bundle 大小]** → Large plugins with many dependencies could produce large bundles. Mitigation: log bundle size at packaging time, warn if > 50MB.
  → 包含许多依赖的大型插件可能产生大型 bundle。缓解措施：在打包时记录 bundle 大小，如果超过 50MB 则发出警告。
