## Why — 动机

Hecate 在 12 个资源域上暴露了 68 个 REST API 端点，但目前所有交互都需要直接 HTTP 调用（curl、httpx 或自定义代码）。需要命令行界面来为平台提供开发者友好的交互面——实现快速的 agent 生命周期管理、带流的交互式聊天会话、知识库操作以及所有资源的完整 CRUD。这是未来 SDK（1.2.1）和 NL2Agent（1.1.7）能力的基础。

## What Changes — 变更内容

- 通过 `typer` + `rich` 框架添加 `hecate` CLI 命令，注册为 `pyproject.toml` 中的 `[project.scripts]` 入口点
- 创建 `src/hecate/cli/` 模块，包含嵌套子命令结构，覆盖所有 68 个 API 端点：config、auth、agent、session、chat、kb、tool、skill、workflow、prompt、memory、template、conversation、model-provider
- 通过 `~/.hecate/config.toml` 实现配置管理，支持命名配置文件（base_url、api_key）
- 支持双重认证：API Key（直接）和 JWT（通过 `hecate auth login` 并自动刷新 token）
- 通过 `httpx.stream` 实现带有 SSE 流式支持的交互式聊天模式（`hecate chat interactive`）
- 通过 `rich` 默认输出表格，并提供 `--json` 标志用于机器可读输出
- 所有命令通过 `httpx`（已经是依赖）与 Hecate REST API 通信

## Capabilities — 能力变更

### 新增能力
- `cli`: Hecate Agent 平台的命令行界面——涵盖 CLI 框架、配置管理、认证、所有资源 CRUD 命令以及带流的交互式聊天

### 修改的能力
- `core-infrastructure`: 向 Settings 类添加 CLI 相关设置（默认 profile、输出格式）

## Impact — 影响范围

- **新代码**: `src/hecate/cli/` 目录（约 15-20 个文件）
- **依赖**: `typer>=0.15.0`、`rich>=13.0.0`（均为新增），`tomli>=2.0` 用于 TOML 配置读取（Python 3.12 的 stdlib 中有 `tomllib`，无需新增依赖）
- **pyproject.toml**: 将 `typer` 和 `rich` 添加到主要依赖；添加 `[project.scripts]` 入口点
- **现有 API 或服务代码无变更** — CLI 是纯客户端新增内容
- **engine 层无变更** — CLI 仅与 REST API 通信
