## ADDED Requirements — 新增需求

### Requirement: 核心配置中的 CLI 输出格式设置
`Settings` 类 SHALL 包含一个 `CLI_DEFAULT_OUTPUT: str` 设置（默认：`"table"`），当未提供 `--json` 标志时，控制 `hecate` CLI 的默认输出格式。这是一个仅服务端设置，不影响 API 行为。

#### Scenario: 默认输出格式
- **WHEN** 未设置 `CLI_DEFAULT_OUTPUT` 环境变量
- **THEN** CLI SHALL 默认使用表格输出格式

#### Scenario: JSON 输出格式
- **WHEN** 设置 `CLI_DEFAULT_OUTPUT=json`
- **THEN** CLI SHALL 默认使用 JSON 输出格式，除非被命令标志覆盖
