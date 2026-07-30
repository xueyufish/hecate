## ADDED Requirements — 新增需求

### Requirement: DangerousPattern dataclass — 需求：DangerousPattern 数据类
The system SHALL define `DangerousPattern` as a dataclass in `engine/tool_access.py` with four fields: `tool_pattern` (str, tool-name glob), `arg_key` (str, argument key to inspect), `arg_pattern` (str, glob pattern for argument value), and `description` (str, human-readable reason).

系统应在 `engine/tool_access.py` 中将 `DangerousPattern` 定义为数据类，包含四个字段：`tool_pattern`（字符串，工具名称 glob）、`arg_key`（字符串，要检查的参数键）、`arg_pattern`（字符串，参数值的 glob 模式）和 `description`（字符串，人类可读的原因）。

#### Scenario: Construction with all fields — 场景：使用所有字段构造
- **WHEN** `DangerousPattern("bash", "command", "rm -rf /", "recursive root delete")` is constructed
- **THEN** all fields are set correctly

- **当**构造 `DangerousPattern("bash", "command", "rm -rf /", "recursive root delete")`
- **则**所有字段正确设置

#### Scenario: Wildcard tool pattern — 场景：通配符工具模式
- **WHEN** `DangerousPattern("*", "code", "*DROP TABLE*", "SQL drop")` is constructed
- **THEN** `tool_pattern` is `"*"` matching all tools

- **当**构造 `DangerousPattern("*", "code", "*DROP TABLE*", "SQL drop")`
- **则** `tool_pattern` 为 `"*"`，匹配所有工具

### Requirement: Built-in dangerous patterns list — 需求：内置危险模式列表
The system SHALL define `DANGEROUS_PATTERNS` as a module-level constant list of `DangerousPattern` instances in `engine/tool_access.py` covering destructive shell commands, dangerous code execution, sensitive file access, and SQL injection patterns.

系统应在 `engine/tool_access.py` 中将 `DANGEROUS_PATTERNS` 定义为模块级常量列表，包含 `DangerousPattern` 实例，涵盖破坏性 shell 命令、危险代码执行、敏感文件访问和 SQL 注入模式。

#### Scenario: Shell command patterns — 场景：Shell 命令模式
- **WHEN** `DANGEROUS_PATTERNS` is inspected
- **THEN** it contains patterns for `rm -rf /`, `mkfs`, `dd if=*of=/dev/`, `curl|sh`, and fork bombs

- **当**检查 `DANGEROUS_PATTERNS`
- **则**包含 `rm -rf /`、`mkfs`、`dd if=*of=/dev/`、`curl|sh` 和 fork 炸弹的模式

#### Scenario: Code execution patterns — 场景：代码执行模式
- **WHEN** `DANGEROUS_PATTERNS` is inspected
- **THEN** it contains patterns for `os.system`, `subprocess`, `eval(`, and `exec(` in code arguments

- **当**检查 `DANGEROUS_PATTERNS`
- **则**包含代码参数中的 `os.system`、`subprocess`、`eval(` 和 `exec(` 的模式

#### Scenario: Sensitive file patterns — 场景：敏感文件模式
- **WHEN** `DANGEROUS_PATTERNS` is inspected
- **THEN** it contains patterns for `.ssh`, `.env`, `.bashrc`, `/etc/passwd`, and SSH key access

- **当**检查 `DANGEROUS_PATTERNS`
- **则**包含 `.ssh`、`.env`、`.bashrc`、`/etc/passwd` 和 SSH 密钥访问的模式

### Requirement: Dangerous pattern evaluation — 需求：危险模式评估
The system SHALL check all tool calls against `DANGEROUS_PATTERNS` before user-defined rules. If a dangerous pattern matches, the system SHALL return `AccessDecision.DENY` regardless of any user-defined `ALLOW` rules.

系统应在用户定义规则之前检查所有工具调用是否匹配 `DANGEROUS_PATTERNS`。如果匹配危险模式，系统应返回 `AccessDecision.DENY`，无论是否存在用户定义的 `ALLOW` 规则。

#### Scenario: Dangerous pattern blocks execution — 场景：危险模式阻止执行
- **WHEN** tool `bash` is called with arguments `{"command": "rm -rf /"}`
- **AND** a user rule `ToolRule(ALLOW, "bash")` exists
- **THEN** the result is `AccessDecision.DENY`

- **当**工具 `bash` 被调用，参数为 `{"command": "rm -rf /"}`
- **且**存在用户规则 `ToolRule(ALLOW, "bash")`
- **则**结果为 `AccessDecision.DENY`

#### Scenario: Dangerous pattern does not match safe variant — 场景：危险模式不匹配安全变体
- **WHEN** tool `bash` is called with arguments `{"command": "rm -rf node_modules/"}`
- **THEN** no dangerous pattern matches (the dangerous pattern is `rm -rf /`, not `rm -rf *`)
- **AND** the result is determined by user rules or risk-level fallback

- **当**工具 `bash` 被调用，参数为 `{"command": "rm -rf node_modules/"}`
- **则**没有危险模式匹配（危险模式是 `rm -rf /`，不是 `rm -rf *`）
- **且**结果由用户规则或风险级别回退决定

#### Scenario: Dangerous pattern with wildcard tool — 场景：带通配符工具的危险模式
- **WHEN** tool `execute_code` is called with arguments `{"code": "import subprocess; subprocess.call(['ls'])"}`
- **AND** a dangerous pattern `DangerousPattern("*", "code", "*subprocess*", ...)` exists
- **THEN** the result is `AccessDecision.DENY`

- **当**工具 `execute_code` 被调用，参数为 `{"code": "import subprocess; subprocess.call(['ls'])"}`
- **且**存在危险模式 `DangerousPattern("*", "code", "*subprocess*", ...)`
- **则**结果为 `AccessDecision.DENY`

#### Scenario: Dangerous pattern skipped when argument absent — 场景：参数缺失时跳过危险模式
- **WHEN** tool `bash` is called without a `command` argument
- **THEN** dangerous patterns targeting the `command` key are skipped

- **当**工具 `bash` 被调用但未提供 `command` 参数
- **则**跳过针对 `command` 键的危险模式

### Requirement: ToolRule arg_conditions field — 需求：ToolRule arg_conditions 字段
The system SHALL extend `ToolRule` dataclass with an optional `arg_conditions: dict[str, str] | None` field. When `arg_conditions` is `None`, the rule matches on tool name only (backward compatible). When `arg_conditions` is set, the rule matches only if the tool name matches AND all argument conditions match their corresponding argument values via `fnmatch`.

系统应扩展 `ToolRule` 数据类，增加可选的 `arg_conditions: dict[str, str] | None` 字段。当 `arg_conditions` 为 `None` 时，规则仅按工具名称匹配（向后兼容）。当设置了 `arg_conditions`，仅当工具名称匹配且所有参数条件通过 `fnmatch` 匹配其对应参数值时，规则才匹配。

#### Scenario: Rule with no arg_conditions (backward compatible) — 场景：无 arg_conditions 的规则（向后兼容）
- **WHEN** `ToolRule(action=RuleAction.DENY, pattern="write_file")` is constructed
- **THEN** `arg_conditions` is `None`

- **当**构造 `ToolRule(action=RuleAction.DENY, pattern="write_file")`
- **则** `arg_conditions` 为 `None`

#### Scenario: Rule with arg_conditions — 场景：带 arg_conditions 的规则
- **WHEN** `ToolRule(action=RuleAction.ASK, pattern="write_file", arg_conditions={"path": "*.env"})` is constructed
- **THEN** `arg_conditions` is `{"path": "*.env"}`

- **当**构造 `ToolRule(action=RuleAction.ASK, pattern="write_file", arg_conditions={"path": "*.env"})`
- **则** `arg_conditions` 为 `{"path": "*.env"}`

#### Scenario: Rule matches with arg_conditions — 场景：规则匹配 arg_conditions
- **WHEN** a rule has `arg_conditions={"path": "*.env"}` and tool call has `arguments={"path": ".env.production"}`
- **THEN** `fnmatch(".env.production", "*.env")` is evaluated and matches

- **当**规则有 `arg_conditions={"path": "*.env"}` 且工具调用有 `arguments={"path": ".env.production"}`
- **则**评估 `fnmatch(".env.production", "*.env")` 并匹配

#### Scenario: Rule does not match with arg_conditions — 场景：规则不匹配 arg_conditions
- **WHEN** a rule has `arg_conditions={"path": "*.env"}` and tool call has `arguments={"path": "output.txt"}`
- **THEN** `fnmatch("output.txt", "*.env")` is evaluated and does not match

- **当**规则有 `arg_conditions={"path": "*.env"}` 且工具调用有 `arguments={"path": "output.txt"}`
- **则**评估 `fnmatch("output.txt", "*.env")` 且不匹配

#### Scenario: Multiple arg_conditions require all to match — 场景：多个 arg_conditions 要求全部匹配
- **WHEN** a rule has `arg_conditions={"path": "*.log", "content": "*password*"}`
- **AND** tool call has `arguments={"path": "app.log", "content": "hello world"}`
- **THEN** the rule does not match (content condition fails)

- **当**规则有 `arg_conditions={"path": "*.log", "content": "*password*"}`
- **且**工具调用有 `arguments={"path": "app.log", "content": "hello world"}`
- **则**规则不匹配（内容条件失败）

### Requirement: ToolAccessPolicy evaluate with arguments — 需求：带参数的 ToolAccessPolicy evaluate
The system SHALL extend `ToolAccessPolicy.evaluate()` to accept an optional `arguments: dict[str, Any]` parameter. When provided, argument conditions are checked after tool-name match. When not provided, the method behaves as before (backward compatible).

系统应扩展 `ToolAccessPolicy.evaluate()` 以接受可选的 `arguments: dict[str, Any]` 参数。提供时，参数条件在工具名称匹配后检查。未提供时，方法行为与之前相同（向后兼容）。

#### Scenario: evaluate with arguments and matching arg_conditions — 场景：带参数和匹配 arg_conditions 的评估
- **WHEN** `policy.evaluate(tool_meta, rules, context, arguments={"path": ".env"})` is called
- **AND** rules contain `ToolRule(ASK, "write_file", arg_conditions={"path": "*.env"})`
- **AND** tool name is `"write_file"`
- **THEN** the result is `AccessDecision.REQUIRE_APPROVAL`

- **当**调用 `policy.evaluate(tool_meta, rules, context, arguments={"path": ".env"})`
- **且**规则包含 `ToolRule(ASK, "write_file", arg_conditions={"path": "*.env"})`
- **且**工具名称为 `"write_file"`
- **则**结果为 `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: evaluate with arguments and non-matching arg_conditions — 场景：带参数和不匹配 arg_conditions 的评估
- **WHEN** `policy.evaluate(tool_meta, rules, context, arguments={"path": "output.txt"})` is called
- **AND** rules contain `ToolRule(ASK, "write_file", arg_conditions={"path": "*.env"})`
- **AND** tool name is `"write_file"`
- **THEN** the arg_conditions rule does not match and evaluation falls through to risk-level fallback

- **当**调用 `policy.evaluate(tool_meta, rules, context, arguments={"path": "output.txt"})`
- **且**规则包含 `ToolRule(ASK, "write_file", arg_conditions={"path": "*.env"})`
- **且**工具名称为 `"write_file"`
- **则** arg_conditions 规则不匹配，评估回退到风险级别

#### Scenario: evaluate without arguments (backward compatible) — 场景：无参数的评估（向后兼容）
- **WHEN** `policy.evaluate(tool_meta, rules, context)` is called without arguments parameter
- **THEN** arg_conditions on rules are ignored (treated as name-only match)

- **当**调用 `policy.evaluate(tool_meta, rules, context)` 而不带 arguments 参数
- **则**规则上的 arg_conditions 被忽略（视为仅名称匹配）

### Requirement: WorkspaceBoundaryPolicy class — 需求：WorkspaceBoundaryPolicy 类
The system SHALL define `WorkspaceBoundaryPolicy` as a class in `engine/tool_access.py` with a `check(tool_name: str, arguments: dict[str, Any], workspace_root: str) -> AccessDecision | None` method. The method returns `EXECUTE` if the tool's path argument resolves within `workspace_root`, `REQUIRE_APPROVAL` if outside, and `None` if the tool has no path argument.

系统应在 `engine/tool_access.py` 中定义 `WorkspaceBoundaryPolicy` 类，包含 `check(tool_name: str, arguments: dict[str, Any], workspace_root: str) -> AccessDecision | None` 方法。如果工具路径参数解析后在 `workspace_root` 内，该方法返回 `EXECUTE`；如果在外部，返回 `REQUIRE_APPROVAL`；如果工具没有路径参数，返回 `None`。

#### Scenario: Path inside workspace — 场景：路径在工作空间内
- **WHEN** `policy.check("write_file", {"path": "src/main.py"}, "/workspace")` is called
- **THEN** the result is `AccessDecision.EXECUTE`

- **当**调用 `policy.check("write_file", {"path": "src/main.py"}, "/workspace")`
- **则**结果为 `AccessDecision.EXECUTE`

#### Scenario: Path outside workspace — 场景：路径在工作空间外
- **WHEN** `policy.check("write_file", {"path": "../../etc/passwd"}, "/workspace")` is called
- **THEN** the result is `AccessDecision.REQUIRE_APPROVAL`

- **当**调用 `policy.check("write_file", {"path": "../../etc/passwd"}, "/workspace")`
- **则**结果为 `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: Absolute path outside workspace — 场景：绝对路径在工作空间外
- **WHEN** `policy.check("read_file", {"path": "/etc/shadow"}, "/workspace")` is called
- **THEN** the result is `AccessDecision.REQUIRE_APPROVAL`

- **当**调用 `policy.check("read_file", {"path": "/etc/shadow"}, "/workspace")`
- **则**结果为 `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: Tool without path argument — 场景：无路径参数的工具
- **WHEN** `policy.check("web_search", {"query": "hello"}, "/workspace")` is called
- **THEN** the result is `None` (no path to check)

- **当**调用 `policy.check("web_search", {"query": "hello"}, "/workspace")`
- **则**结果为 `None`（没有要检查的路径）

#### Scenario: Path traversal with normalization — 场景：带规范化的路径遍历
- **WHEN** `policy.check("read_file", {"path": "src/../../etc/passwd"}, "/workspace")` is called
- **THEN** the normalized path is `/etc/passwd` which is outside `/workspace`
- **AND** the result is `AccessDecision.REQUIRE_APPROVAL`

- **当**调用 `policy.check("read_file", {"path": "src/../../etc/passwd"}, "/workspace")`
- **则**规范化后的路径为 `/etc/passwd`，位于 `/workspace` 之外
- **且**结果为 `AccessDecision.REQUIRE_APPROVAL`

### Requirement: Workspace boundary in evaluation flow — 需求：评估流程中的工作空间边界
The system SHALL integrate `WorkspaceBoundaryPolicy` into `ToolAccessPolicy.evaluate()`. When no user-defined rule matches, the system SHALL check workspace boundary if `context["workspace_root"]` is provided and the tool has path-type arguments.

系统应将 `WorkspaceBoundaryPolicy` 集成到 `ToolAccessPolicy.evaluate()` 中。当没有用户定义的规则匹配时，如果提供了 `context["workspace_root"]` 且工具有路径类型参数，系统应检查工作空间边界。

#### Scenario: No rule matches, path inside workspace — 场景：无规则匹配，路径在工作空间内
- **WHEN** `policy.evaluate(tool_meta, rules, context, arguments={"path": "src/app.py"})` is called
- **AND** no rule matches
- **AND** `context["workspace_root"]` is `"/workspace"`
- **THEN** workspace boundary returns `EXECUTE`

- **当**调用 `policy.evaluate(tool_meta, rules, context, arguments={"path": "src/app.py"})`
- **且**没有规则匹配
- **且** `context["workspace_root"]` 为 `"/workspace"`
- **则**工作空间边界返回 `EXECUTE`

#### Scenario: No rule matches, path outside workspace — 场景：无规则匹配，路径在工作空间外
- **WHEN** `policy.evaluate(tool_meta, rules, context, arguments={"path": "/etc/passwd"})` is called
- **AND** no rule matches
- **AND** `context["workspace_root"]` is `"/workspace"`
- **THEN** workspace boundary returns `REQUIRE_APPROVAL`

- **当**调用 `policy.evaluate(tool_meta, rules, context, arguments={"path": "/etc/passwd"})`
- **且**没有规则匹配
- **且** `context["workspace_root"]` 为 `"/workspace"`
- **则**工作空间边界返回 `REQUIRE_APPROVAL`

#### Scenario: User rule overrides workspace boundary — 场景：用户规则覆盖工作空间边界
- **WHEN** rules contain `ToolRule(ALLOW, "read_file", arg_conditions={"path": "/etc/hostname"})`
- **AND** arguments are `{"path": "/etc/hostname"}`
- **THEN** the ALLOW rule matches and workspace boundary is not checked
- **AND** the result is `AccessDecision.EXECUTE`

- **当**规则包含 `ToolRule(ALLOW, "read_file", arg_conditions={"path": "/etc/hostname"})`
- **且**参数为 `{"path": "/etc/hostname"}`
- **则** ALLOW 规则匹配，不检查工作空间边界
- **且**结果为 `AccessDecision.EXECUTE`

#### Scenario: No workspace_root in context — 场景：上下文中无 workspace_root
- **WHEN** `context` does not contain `"workspace_root"`
- **THEN** workspace boundary check is skipped

- **当** `context` 不包含 `"workspace_root"`
- **则**跳过工作空间边界检查

### Requirement: Extended evaluation order — 需求：扩展评估顺序
The system SHALL evaluate tool access in the following order: (1) dangerous patterns, (2) user rules with arg_conditions, (3) workspace boundary, (4) risk-level fallback, (5) sandbox routing. Each layer is only evaluated if the previous layer did not produce a decision.

系统应按以下顺序评估工具访问：(1) 危险模式，(2) 带 arg_conditions 的用户规则，(3) 工作空间边界，(4) 风险级别回退，(5) 沙箱路由。每一层仅在前一层未产生决策时才进行评估。

#### Scenario: Dangerous pattern overrides user ALLOW — 场景：危险模式覆盖用户 ALLOW
- **WHEN** a dangerous pattern matches
- **AND** a user ALLOW rule also matches
- **THEN** the result is `DENY` (dangerous patterns checked first)

- **当**危险模式匹配
- **且**用户 ALLOW 规则也匹配
- **则**结果为 `DENY`（危险模式优先检查）

#### Scenario: User DENY overrides workspace boundary — 场景：用户 DENY 覆盖工作空间边界
- **WHEN** a user DENY rule matches
- **AND** the path is inside workspace (boundary would ALLOW)
- **THEN** the result is `DENY` (user rules checked before boundary)

- **当**用户 DENY 规则匹配
- **且**路径在工作空间内（边界会 ALLOW）
- **则**结果为 `DENY`（用户规则在边界前检查）

#### Scenario: Workspace boundary overrides risk-level fallback — 场景：工作空间边界覆盖风险级别回退
- **WHEN** no user rule matches
- **AND** the path is inside workspace (boundary returns EXECUTE)
- **AND** risk level is HIGH (fallback would return REQUIRE_APPROVAL)
- **THEN** the result is `EXECUTE` (boundary checked before fallback)

- **当**没有用户规则匹配
- **且**路径在工作空间内（边界返回 EXECUTE）
- **且**风险级别为 HIGH（回退会返回 REQUIRE_APPROVAL）
- **则**结果为 `EXECUTE`（边界在回退前检查）

### Requirement: ToolPolicyModel arg_conditions column — 需求：ToolPolicyModel arg_conditions 列
The system SHALL add `arg_conditions` JSON column to `ToolPolicyModel` in `models/tool_policy.py`. The column stores a JSON object mapping argument keys to glob patterns. When `None`, the rule matches on tool name only.

系统应在 `models/tool_policy.py` 的 `ToolPolicyModel` 中添加 `arg_conditions` JSON 列。该列存储将参数键映射到 glob 模式的 JSON 对象。当为 `None` 时，规则仅按工具名称匹配。

#### Scenario: Create policy with arg_conditions — 场景：带 arg_conditions 创建策略
- **WHEN** a `ToolPolicyModel` is created with `arg_conditions={"path": "*.env"}`
- **THEN** the value is persisted as JSON and retrievable

- **当**创建 `ToolPolicyModel` 时设置 `arg_conditions={"path": "*.env"}`
- **则**该值以 JSON 格式持久化并可检索

#### Scenario: Create policy without arg_conditions — 场景：不带 arg_conditions 创建策略
- **WHEN** a `ToolPolicyModel` is created without `arg_conditions`
- **THEN** the column value is `None`

- **当**创建 `ToolPolicyModel` 时未设置 `arg_conditions`
- **则**列值为 `None`

### Requirement: ToolPolicyCreateSchema arg_conditions field — 需求：ToolPolicyCreateSchema arg_conditions 字段
The system SHALL add `arg_conditions: dict[str, str] | None` field to `ToolPolicyCreateSchema` in `models/tool_policy.py`.

系统应在 `models/tool_policy.py` 的 `ToolPolicyCreateSchema` 中添加 `arg_conditions: dict[str, str] | None` 字段。

#### Scenario: Schema with arg_conditions — 场景：带 arg_conditions 的 Schema
- **WHEN** `ToolPolicyCreateSchema(rule_action="deny", tool_pattern="write_file", arg_conditions={"path": "*.env"})` is validated
- **THEN** the schema is accepted

- **当**验证 `ToolPolicyCreateSchema(rule_action="deny", tool_pattern="write_file", arg_conditions={"path": "*.env"})`
- **则**模式被接受

#### Scenario: Schema without arg_conditions — 场景：不带 arg_conditions 的 Schema
- **WHEN** `ToolPolicyCreateSchema(rule_action="deny", tool_pattern="write_file")` is validated
- **THEN** `arg_conditions` is `None`

- **当**验证 `ToolPolicyCreateSchema(rule_action="deny", tool_pattern="write_file")`
- **则** `arg_conditions` 为 `None`

### Requirement: ToolWorker passes arguments to policy — 需求：ToolWorker 传递参数给策略
The system SHALL extend `ToolWorker._check_access()` to forward parsed tool call arguments to `ToolAccessPolicy.evaluate()`.

系统应扩展 `ToolWorker._check_access()` 以将解析后的工具调用参数转发给 `ToolAccessPolicy.evaluate()`。

#### Scenario: Arguments forwarded to policy — 场景：参数转发给策略
- **WHEN** ToolWorker processes a tool call with `arguments={"path": ".env"}`
- **AND** `access_policy` is configured
- **THEN** `ToolAccessPolicy.evaluate()` receives the arguments dict

- **当** ToolWorker 处理带 `arguments={"path": ".env"}` 的工具调用
- **且**配置了 `access_policy`
- **则** `ToolAccessPolicy.evaluate()` 接收参数字典

#### Scenario: Backward compatible when no policy configured — 场景：未配置策略时向后兼容
- **WHEN** ToolWorker processes a tool call without `access_policy`
- **THEN** `_check_access` returns `None` (no enforcement)

- **当** ToolWorker 处理工具调用时未配置 `access_policy`
- **则** `_check_access` 返回 `None`（不执行强制）

### Requirement: Engine layer zero dependencies maintained — 需求：保持引擎层零依赖
`engine/tool_access.py` SHALL continue to have zero external dependencies beyond the Python standard library after all extensions.

所有扩展后，`engine/tool_access.py` 应继续保持除 Python 标准库外零外部依赖。

#### Scenario: No new imports — 场景：无新增导入
- **WHEN** `engine/tool_access.py` imports are inspected
- **THEN** all imports are from `__future__`, `abc`, `dataclasses`, `enum`, `fnmatch`, `logging`, `os.path`, or `typing`

- **当**检查 `engine/tool_access.py` 的导入
- **则**所有导入来自 `__future__`、`abc`、`dataclasses`、`enum`、`fnmatch`、`logging`、`os.path` 或 `typing`
