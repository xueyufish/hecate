## ADDED Requirements

### Requirement: DangerousPattern dataclass
The system SHALL define `DangerousPattern` as a dataclass in `engine/tool_access.py` with four fields: `tool_pattern` (str, tool-name glob), `arg_key` (str, argument key to inspect), `arg_pattern` (str, glob pattern for argument value), and `description` (str, human-readable reason).

#### Scenario: Construction with all fields
- **WHEN** `DangerousPattern("bash", "command", "rm -rf /", "recursive root delete")` is constructed
- **THEN** all fields are set correctly

#### Scenario: Wildcard tool pattern
- **WHEN** `DangerousPattern("*", "code", "*DROP TABLE*", "SQL drop")` is constructed
- **THEN** `tool_pattern` is `"*"` matching all tools

### Requirement: Built-in dangerous patterns list
The system SHALL define `DANGEROUS_PATTERNS` as a module-level constant list of `DangerousPattern` instances in `engine/tool_access.py` covering destructive shell commands, dangerous code execution, sensitive file access, and SQL injection patterns.

#### Scenario: Shell command patterns
- **WHEN** `DANGEROUS_PATTERNS` is inspected
- **THEN** it contains patterns for `rm -rf /`, `mkfs`, `dd if=*of=/dev/`, `curl|sh`, and fork bombs

#### Scenario: Code execution patterns
- **WHEN** `DANGEROUS_PATTERNS` is inspected
- **THEN** it contains patterns for `os.system`, `subprocess`, `eval(`, and `exec(` in code arguments

#### Scenario: Sensitive file patterns
- **WHEN** `DANGEROUS_PATTERNS` is inspected
- **THEN** it contains patterns for `.ssh`, `.env`, `.bashrc`, `/etc/passwd`, and SSH key access

### Requirement: Dangerous pattern evaluation
The system SHALL check all tool calls against `DANGEROUS_PATTERNS` before user-defined rules. If a dangerous pattern matches, the system SHALL return `AccessDecision.DENY` regardless of any user-defined `ALLOW` rules.

#### Scenario: Dangerous pattern blocks execution
- **WHEN** tool `bash` is called with arguments `{"command": "rm -rf /"}`
- **AND** a user rule `ToolRule(ALLOW, "bash")` exists
- **THEN** the result is `AccessDecision.DENY`

#### Scenario: Dangerous pattern does not match safe variant
- **WHEN** tool `bash` is called with arguments `{"command": "rm -rf node_modules/"}`
- **THEN** no dangerous pattern matches (the dangerous pattern is `rm -rf /`, not `rm -rf *`)
- **AND** the result is determined by user rules or risk-level fallback

#### Scenario: Dangerous pattern with wildcard tool
- **WHEN** tool `execute_code` is called with arguments `{"code": "import subprocess; subprocess.call(['ls'])"}`
- **AND** a dangerous pattern `DangerousPattern("*", "code", "*subprocess*", ...)` exists
- **THEN** the result is `AccessDecision.DENY`

#### Scenario: Dangerous pattern skipped when argument absent
- **WHEN** tool `bash` is called without a `command` argument
- **THEN** dangerous patterns targeting the `command` key are skipped

### Requirement: ToolRule arg_conditions field
The system SHALL extend `ToolRule` dataclass with an optional `arg_conditions: dict[str, str] | None` field. When `arg_conditions` is `None`, the rule matches on tool name only (backward compatible). When `arg_conditions` is set, the rule matches only if the tool name matches AND all argument conditions match their corresponding argument values via `fnmatch`.

#### Scenario: Rule with no arg_conditions (backward compatible)
- **WHEN** `ToolRule(action=RuleAction.DENY, pattern="write_file")` is constructed
- **THEN** `arg_conditions` is `None`

#### Scenario: Rule with arg_conditions
- **WHEN** `ToolRule(action=RuleAction.ASK, pattern="write_file", arg_conditions={"path": "*.env"})` is constructed
- **THEN** `arg_conditions` is `{"path": "*.env"}`

#### Scenario: Rule matches with arg_conditions
- **WHEN** a rule has `arg_conditions={"path": "*.env"}` and tool call has `arguments={"path": ".env.production"}`
- **THEN** `fnmatch(".env.production", "*.env")` is evaluated and matches

#### Scenario: Rule does not match with arg_conditions
- **WHEN** a rule has `arg_conditions={"path": "*.env"}` and tool call has `arguments={"path": "output.txt"}`
- **THEN** `fnmatch("output.txt", "*.env")` is evaluated and does not match

#### Scenario: Multiple arg_conditions require all to match
- **WHEN** a rule has `arg_conditions={"path": "*.log", "content": "*password*"}`
- **AND** tool call has `arguments={"path": "app.log", "content": "hello world"}`
- **THEN** the rule does not match (content condition fails)

### Requirement: ToolAccessPolicy evaluate with arguments
The system SHALL extend `ToolAccessPolicy.evaluate()` to accept an optional `arguments: dict[str, Any]` parameter. When provided, argument conditions are checked after tool-name match. When not provided, the method behaves as before (backward compatible).

#### Scenario: evaluate with arguments and matching arg_conditions
- **WHEN** `policy.evaluate(tool_meta, rules, context, arguments={"path": ".env"})` is called
- **AND** rules contain `ToolRule(ASK, "write_file", arg_conditions={"path": "*.env"})`
- **AND** tool name is `"write_file"`
- **THEN** the result is `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: evaluate with arguments and non-matching arg_conditions
- **WHEN** `policy.evaluate(tool_meta, rules, context, arguments={"path": "output.txt"})` is called
- **AND** rules contain `ToolRule(ASK, "write_file", arg_conditions={"path": "*.env"})`
- **AND** tool name is `"write_file"`
- **THEN** the arg_conditions rule does not match and evaluation falls through to risk-level fallback

#### Scenario: evaluate without arguments (backward compatible)
- **WHEN** `policy.evaluate(tool_meta, rules, context)` is called without arguments parameter
- **THEN** arg_conditions on rules are ignored (treated as name-only match)

### Requirement: WorkspaceBoundaryPolicy class
The system SHALL define `WorkspaceBoundaryPolicy` as a class in `engine/tool_access.py` with a `check(tool_name: str, arguments: dict[str, Any], workspace_root: str) -> AccessDecision | None` method. The method returns `EXECUTE` if the tool's path argument resolves within `workspace_root`, `REQUIRE_APPROVAL` if outside, and `None` if the tool has no path argument.

#### Scenario: Path inside workspace
- **WHEN** `policy.check("write_file", {"path": "src/main.py"}, "/workspace")` is called
- **THEN** the result is `AccessDecision.EXECUTE`

#### Scenario: Path outside workspace
- **WHEN** `policy.check("write_file", {"path": "../../etc/passwd"}, "/workspace")` is called
- **THEN** the result is `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: Absolute path outside workspace
- **WHEN** `policy.check("read_file", {"path": "/etc/shadow"}, "/workspace")` is called
- **THEN** the result is `AccessDecision.REQUIRE_APPROVAL`

#### Scenario: Tool without path argument
- **WHEN** `policy.check("web_search", {"query": "hello"}, "/workspace")` is called
- **THEN** the result is `None` (no path to check)

#### Scenario: Path traversal with normalization
- **WHEN** `policy.check("read_file", {"path": "src/../../etc/passwd"}, "/workspace")` is called
- **THEN** the normalized path is `/etc/passwd` which is outside `/workspace`
- **AND** the result is `AccessDecision.REQUIRE_APPROVAL`

### Requirement: Workspace boundary in evaluation flow
The system SHALL integrate `WorkspaceBoundaryPolicy` into `ToolAccessPolicy.evaluate()`. When no user-defined rule matches, the system SHALL check workspace boundary if `context["workspace_root"]` is provided and the tool has path-type arguments.

#### Scenario: No rule matches, path inside workspace
- **WHEN** `policy.evaluate(tool_meta, rules, context, arguments={"path": "src/app.py"})` is called
- **AND** no rule matches
- **AND** `context["workspace_root"]` is `"/workspace"`
- **THEN** workspace boundary returns `EXECUTE`

#### Scenario: No rule matches, path outside workspace
- **WHEN** `policy.evaluate(tool_meta, rules, context, arguments={"path": "/etc/passwd"})` is called
- **AND** no rule matches
- **AND** `context["workspace_root"]` is `"/workspace"`
- **THEN** workspace boundary returns `REQUIRE_APPROVAL`

#### Scenario: User rule overrides workspace boundary
- **WHEN** rules contain `ToolRule(ALLOW, "read_file", arg_conditions={"path": "/etc/hostname"})`
- **AND** arguments are `{"path": "/etc/hostname"}`
- **THEN** the ALLOW rule matches and workspace boundary is not checked
- **AND** the result is `AccessDecision.EXECUTE`

#### Scenario: No workspace_root in context
- **WHEN** `context` does not contain `"workspace_root"`
- **THEN** workspace boundary check is skipped

### Requirement: Extended evaluation order
The system SHALL evaluate tool access in the following order: (1) dangerous patterns, (2) user rules with arg_conditions, (3) workspace boundary, (4) risk-level fallback, (5) sandbox routing. Each layer is only evaluated if the previous layer did not produce a decision.

#### Scenario: Dangerous pattern overrides user ALLOW
- **WHEN** a dangerous pattern matches
- **AND** a user ALLOW rule also matches
- **THEN** the result is `DENY` (dangerous patterns checked first)

#### Scenario: User DENY overrides workspace boundary
- **WHEN** a user DENY rule matches
- **AND** the path is inside workspace (boundary would ALLOW)
- **THEN** the result is `DENY` (user rules checked before boundary)

#### Scenario: Workspace boundary overrides risk-level fallback
- **WHEN** no user rule matches
- **AND** the path is inside workspace (boundary returns EXECUTE)
- **AND** risk level is HIGH (fallback would return REQUIRE_APPROVAL)
- **THEN** the result is `EXECUTE` (boundary checked before fallback)

### Requirement: ToolPolicyModel arg_conditions column
The system SHALL add `arg_conditions` JSON column to `ToolPolicyModel` in `models/tool_policy.py`. The column stores a JSON object mapping argument keys to glob patterns. When `None`, the rule matches on tool name only.

#### Scenario: Create policy with arg_conditions
- **WHEN** a `ToolPolicyModel` is created with `arg_conditions={"path": "*.env"}`
- **THEN** the value is persisted as JSON and retrievable

#### Scenario: Create policy without arg_conditions
- **WHEN** a `ToolPolicyModel` is created without `arg_conditions`
- **THEN** the column value is `None`

### Requirement: ToolPolicyCreateSchema arg_conditions field
The system SHALL add `arg_conditions: dict[str, str] | None` field to `ToolPolicyCreateSchema` in `models/tool_policy.py`.

#### Scenario: Schema with arg_conditions
- **WHEN** `ToolPolicyCreateSchema(rule_action="deny", tool_pattern="write_file", arg_conditions={"path": "*.env"})` is validated
- **THEN** the schema is accepted

#### Scenario: Schema without arg_conditions
- **WHEN** `ToolPolicyCreateSchema(rule_action="deny", tool_pattern="write_file")` is validated
- **THEN** `arg_conditions` is `None`

### Requirement: ToolWorker passes arguments to policy
The system SHALL extend `ToolWorker._check_access()` to forward parsed tool call arguments to `ToolAccessPolicy.evaluate()`.

#### Scenario: Arguments forwarded to policy
- **WHEN** ToolWorker processes a tool call with `arguments={"path": ".env"}`
- **AND** `access_policy` is configured
- **THEN** `ToolAccessPolicy.evaluate()` receives the arguments dict

#### Scenario: Backward compatible when no policy configured
- **WHEN** ToolWorker processes a tool call without `access_policy`
- **THEN** `_check_access` returns `None` (no enforcement)

### Requirement: Engine layer zero dependencies maintained
`engine/tool_access.py` SHALL continue to have zero external dependencies beyond the Python standard library after all extensions.

#### Scenario: No new imports
- **WHEN** `engine/tool_access.py` imports are inspected
- **THEN** all imports are from `__future__`, `abc`, `dataclasses`, `enum`, `fnmatch`, `logging`, `os.path`, or `typing`
