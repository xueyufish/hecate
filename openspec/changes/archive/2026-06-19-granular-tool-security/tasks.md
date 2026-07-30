## 1. 引擎层 — DangerousPattern 和 DANGEROUS_PATTERNS

- [x] 1.1 在 `engine/tool_access.py` 中定义 `DangerousPattern` 数据类，字段：`tool_pattern`（str）、`arg_key`（str）、`arg_pattern`（str）、`description`（str）
- [x] 1.2 定义模块级常量列表 `DANGEROUS_PATTERNS`，包含 shell 命令模式：`rm -rf /`、`mkfs*`、`dd if=*of=/dev/`、`*curl*|*sh`、fork 炸弹
- [x] 1.3 添加代码执行模式：针对 `execute_code` 工具的 `*os.system*`、`*subprocess*`、`*eval(*`、`*exec(*`
- [x] 1.4 添加敏感文件模式：针对 `write_file` 和 `read_file` 工具的 `.ssh`、`.env`、`.bashrc`、`/etc/passwd`、SSH 密钥访问
- [x] 1.5 添加 SQL 危险模式：针对通配符工具且 `code` 参数的 `*DROP TABLE*`、`*DELETE FROM*`

## 2. 引擎层 — ToolRule arg_conditions 扩展

- [x] 2.1 向 `ToolRule` 数据类添加 `arg_conditions: dict[str, str] | None = None` 字段
- [x] 2.2 添加 `from __future__ import annotations` 检查——确保所有使用 ToolRule 的现有代码仍能工作（向后兼容）
- [x] 2.3 编写单元测试：带和不带 arg_conditions 的 ToolRule 构造

## 3. 引擎层 — ToolAccessPolicy arg_conditions 匹配

- [x] 3.1 扩展 `evaluate()` 签名以接受可选参数 `arguments: dict[str, Any] | None = None`
- [x] 3.2 实现 `_match_dangerous_patterns(tool_name, arguments)` 方法——如果任何危险模式匹配则返回 `True`
- [x] 3.3 在 evaluate() 的 START 处集成危险模式检查——在用户规则之前，如果匹配则返回 DENY
- [x] 3.4 扩展 `_match_rules()` 以在工具名称匹配后检查 `arg_conditions`——如果规则有 arg_conditions，所有必须通过 fnmatch 匹配；如果没有 arg_conditions，仅按名称匹配（向后兼容）
- [x] 3.5 编写单元测试：危险模式检测（shell、代码、文件、SQL 模式）
- [x] 3.6 编写单元测试：arg_conditions 匹配（单条件、多条件、不匹配、向后兼容）
- [x] 3.7 编写单元测试：危险模式覆盖用户 ALLOW 规则

## 4. 引擎层 — WorkspaceBoundaryPolicy

- [x] 4.1 定义 `WorkspaceBoundaryPolicy` 类，包含 `check(tool_name, arguments, workspace_root) -> AccessDecision | None` 方法
- [x] 4.2 实现路径提取——检查 `arguments` 字典是否包含已知的路径键（`path`、`file_path`、`directory`、`directory_path`）
- [x] 4.3 使用 `os.path.normpath` 和 `os.path.join` 实现路径规范化，以解析相对路径和检测遍历（`../`）
- [x] 4.4 实现边界检查——如果规范化路径以 `workspace_root` 开头则返回 `EXECUTE`，如果在外部则返回 `REQUIRE_APPROVAL`，如果没有路径参数则返回 `None`
- [x] 4.5 将工作空间边界集成到 `evaluate()`——在用户规则之后、风险级别回退之前，仅在设置了 `context["workspace_root"]` 时
- [x] 4.6 编写单元测试：工作空间内路径（EXECUTE）、工作空间外路径（REQUIRE_APPROVAL）、遍历攻击、无路径参数（None）、无 workspace_root（跳过）

## 5. 模型层 — ToolPolicyModel arg_conditions 列

- [x] 5.1 向 `models/tool_policy.py` 中的 `ToolPolicyModel` 添加 `arg_conditions: Mapped[dict | None]` JSON 列
- [x] 5.2 向 `ToolPolicyCreateSchema` 添加 `arg_conditions: dict[str, str] | None` 字段
- [x] 5.3 向 `ToolPolicyReadSchema` 添加 `arg_conditions: dict[str, str] | None` 字段
- [x] 5.4 创建 Alembic 迁移以向 `tool_policies` 表添加 `arg_conditions` 列（可为空，默认 None）
- [x] 5.5 编写模型测试：带 arg_conditions 创建、不带 arg_conditions 创建、从属性的 ReadSchema
- [x] 5.6 如果需要，更新 `tests/conftest.py`——确保 tool_policy 模型导入已存在

## 6. ToolWorker 集成

- [x] 6.1 扩展 `ToolWorker._check_access()` 以传递 `arguments` 给 `ToolAccessPolicy.evaluate()`
- [x] 6.2 确保向后兼容——当未配置策略时，像以前一样返回 None
- [x] 6.3 编写集成测试：参数转发给策略、危险模式阻止工具调用、arg_conditions ASK 触发批准、工作空间边界自动允许内部路径

## 7. 验证

- [x] 7.1 运行 `ruff check src/hecate/ tests/`——零错误
- [x] 7.2 运行 `ruff format --check src/ tests/`——零问题
- [x] 7.3 运行 `mypy src/`——零错误
- [x] 7.4 运行 `python -m pytest tests/ -q`——所有测试通过
- [x] 7.5 验证 engine/tool_access.py 除标准库外无导入（`__future__`、`abc`、`dataclasses`、`enum`、`fnmatch`、`logging`、`os.path`、`typing`）
