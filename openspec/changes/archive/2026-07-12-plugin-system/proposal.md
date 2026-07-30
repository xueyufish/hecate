## Why — 为什么

Hecate 的 Plugin SPI Core（5.5a）提供了 `PluginRegistry`、`PluginManifest` 和 `PluginLifecycle` 用于进程内插件注册，但所有现有的 SPI 实现（EvaluatorABC、ChannelABC、AuthProviderABC、SecretProviderABC）都是通过硬编码的 Python 函数（`register_auth_providers()`、`register_channels()`）注册的。没有声明式插件加载机制——没有 `plugin.yaml` 清单解析、没有目录发现、没有配置管理、没有权限执行、也没有用于插件生命周期管理的 UI。这阻碍了第三方插件开发，并将平台限制为仅内置提供者。

## What Changes — 变更内容

- **插件清单加载**：将 `plugin.yaml` 文件解析为 `PluginManifest` 对象，并进行验证（必填字段、`api_version` / `min_platform_version` 兼容性检查）
- **目录发现**：扫描 `plugins/` 目录中的插件包，在启动时自动发现并注册插件
- **扩展 PluginLifecycle**：在现有的 `on_load` / `on_unload` protocol 基础上添加 `on_enable`、`on_disable`、`on_config_change` 钩子
- **PluginModel 数据库表**：新的 ORM 模型，持久化插件状态（installed、enabled、error）、按工作空间启用和配置值——数据库是运行时真理源
- **配置管理**：`plugin.yaml` 中的 `config_schema`（JSON Schema）→ 数据库存储 → 运行时注入到插件实例；前端根据 schema 自动生成配置表单
- **权限声明和执行**：插件在 `plugin.yaml` 中声明所需权限；加载器在运行时拒绝未声明的权限
- **入口加载策略**：`python:module:Class`（进程内 importlib）和 `mcp://endpoint`（通过现有 MCP Client 5.3）——进程内 + MCP 混合架构，无需自定义守护进程
- **REST API**：`GET /api/plugins`（列表）、`POST /api/plugins/{id}/enable`、`POST /api/plugins/{id}/disable`、`PUT /api/plugins/{id}/config`（更新配置）
- **前端插件管理页面**：带状态徽章的插件列表、启用/禁用开关、根据 `config_schema` 自动生成的配置表单、MCP 端点连接管理 UI
- **双层作用域**：平台级插件（随 Hecate 发布，全局可用）+ 工作空间级插件（按工作空间安装和启用）

## Capabilities — 能力

### 新能力

- `plugin-system`：插件运行时引擎——清单加载、目录发现、兼容性验证、扩展生命周期、数据库支持的状态管理、配置注入、权限执行、入口加载（进程内 + MCP）、REST API 和前端管理 UI

### 变更的能力

（无——现有的通过 `register_auth_providers()` 等的 SPI 注册机制保持不变；插件系统在现有命令式路径之外提供了额外的声明式加载路径）

## Impact — 影响

- **新文件**：
  - `src/hecate/plugin/loader.py` — plugin.yaml 解析器 + 目录扫描器 + 入口加载器
  - `src/hecate/plugin/config.py` — 配置 schema 验证 + 运行时注入
  - `src/hecate/plugin/permission.py` — 权限声明解析 + 执行
  - `src/hecate/models/plugin.py` — `PluginModel` ORM（id、name、type、version、status、config、workspace_id）
  - `src/hecate/api/management/plugins.py` — REST API 路由
  - `alembic/versions/xxx_add_plugin_model.py` — 数据库迁移
  - `web/src/app/(dashboard)/plugins/page.tsx` — 前端管理页面
  - `web/src/app/(dashboard)/plugins/[id]/page.tsx` — 插件详情 + 配置页面
- **修改的文件**：
  - `src/hecate/plugin/lifecycle.py` — 向 `PluginLifecycle` protocol 添加 `on_enable` / `on_disable` / `on_config_change`
  - `src/hecate/plugin/manifest.py` — 向 `PluginManifest` 添加 `entry`、`permissions`、`config_schema` 字段
  - `src/hecate/main.py` — 注册插件路由，在启动时触发插件发现
  - `src/hecate/core/config.py` — 添加 `PLUGINS_DIR` 设置
  - `web/src/components/sidebar.tsx` — 添加"插件"导航链接
- **依赖**：`yaml`（已安装）、`jsonschema`（已安装，用于 Graph DSL）
- **数据库迁移**：新建带 workspace_id 外键的 `plugins` 表
