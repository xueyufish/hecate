## ADDED Requirements

### Requirement: Direct file writes enforce workspace roles
MCP tool_execute 的直接文件写操作 SHALL 要求 workspace editor 或更高角色，显式 system scope 沿用既有策略。拒绝 SHALL 发生在执行器调用之前，不得创建文件或工作目录；viewer 的合法只读操作继续可用。

#### Scenario: Viewer cannot write files
- **WHEN** viewer 经 tool_execute 调用 write_file
- **THEN** 返回权限错误，不创建或修改文件

