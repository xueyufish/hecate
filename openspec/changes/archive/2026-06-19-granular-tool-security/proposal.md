## Why — 为什么

功能 9.4（执行安全）通过 `ToolAccessPolicy` 在工具名称上使用 glob 模式匹配建立了工具名称级访问控制（例如 `terminal(git:*)`）。然而，现实世界的安全需要**参数级检查**——区分 `write_file({"path": ".env"})`（敏感）和 `write_file({"path": "output.txt"})`（安全）。当前的规则无法表达这种差异。此外，需要工作空间边界执行（9.4b），以便工作空间目录内的文件操作自动允许，而外部的操作需要显式批准。一项 10 平台调查（Claude Code、HermesAgent、AgentScope、AgentArts、openJiuwen、Salesforce、Google ADK、IBM watsonx、OpenClaw、Dify）确认基于 glob 的参数匹配是行业标准方法。

## What Changes — 变更内容

- **扩展 `ToolRule`** 以包含可选的 `arg_conditions: dict[str, str] | None`，用于基于 glob 的参数值匹配（Claude Code `Bash(rm *)` 模式）
- **扩展 `ToolAccessPolicy.evaluate()`** 以接受工具调用参数并在工具名称匹配后匹配 `arg_conditions`
- **添加内置危险模式列表**——针对常见危险操作（`rm -rf /`、`DROP TABLE`、`curl | sh`、`os.system`、`~/.ssh`、`~/.env`、`/etc/passwd`）的预定义 `DENY` 模式，不能被 `ALLOW` 规则覆盖
- **添加工作空间边界评估**——自动允许 `workspace_root` 内的文件操作，对外的操作需要批准
- **扩展 `ToolPolicyModel`** 以包含 `arg_conditions` JSON 列，用于持久化的参数级规则
- **扩展 `ToolWorker._check_access()`** 以传递解析后的参数给 `ToolAccessPolicy.evaluate()`
- **添加 `WorkspaceBoundaryPolicy`** 辅助类，检查 `path` 类型参数相对于 context 中的 `workspace_root`

## Capabilities — 能力

### New Capabilities — 新增能力
- `granular-tool-security`：参数级工具安全，在工具调用参数上使用 glob 模式匹配，内置危险模式检测，以及工作空间边界执行

### Modified Capabilities — 修改的能力
- `execution-security`：ToolRule 数据类获得 `arg_conditions` 字段；ToolAccessPolicy.evaluate() 签名扩展以接受参数；ToolWorker._check_access() 传递参数给策略评估器

## Impact — 影响

- **引擎层**（`engine/tool_access.py`）：扩展 `ToolRule`、`ToolAccessPolicy`，添加 `DANGEROUS_PATTERNS` 常量和 `WorkspaceBoundaryPolicy` 类
- **引擎层**（`engine/workers/tool_worker.py`）：传递参数给 `_check_access()`，扩展 `_check_access` 以转发参数给策略
- **模型层**（`models/tool_policy.py`）：向 `ToolPolicyModel` 添加 `arg_conditions` JSON 列
- **迁移**：新的 Alembic 迁移以向 `tool_policies` 表添加 `arg_conditions` 列
- **测试**：用于参数匹配、危险模式检测、工作空间边界检查的新测试套件
- **向后兼容**：所有更改都是增量的——现有的仅名称规则继续不变地工作
