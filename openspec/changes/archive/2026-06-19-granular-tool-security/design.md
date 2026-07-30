## Context — 背景

功能 9.4（执行安全）建立了三层工具访问控制：规则引擎（工具名称 glob）→ 风险级别回退 → 沙箱路由。规则引擎仅匹配工具名称（`fnmatch(tool_name, rule.pattern)`）——它无法区分同一工具的不同参数值。

进行了一项 10 平台研究以指导设计：

| 平台 | 参数检查 | 技术 |
|----------|-------------------|-----------|
| Claude Code | ✅ | 命令字符串上的 Glob，复合命令解析（`Bash(rm *)`） |
| HermesAgent | ✅ | 正则危险模式 + LLM 辅助批准 |
| AgentScope 2.0 | ✅ | Tree-sitter AST + 前缀模式 + 7 层分析 |
| AgentArts | ❌ | 仅沙箱隔离，无参数检查 |
| openJiuwen | ❌ | 内核级（Landlock + seccomp） |
| Salesforce/Dify/IBM/Google | ❌ | 仅工具名称级别 |

**当前状态**：`ToolAccessPolicy.evaluate()` 接收 `tool_meta`（risk_level、approval_required、sandbox_enabled）、`rules`（`ToolRule` 列表）和 `context`（tool_name）。`ToolWorker._check_access()` 方法已经接收了解析后的 `arguments` 字典，但并未将其转发给策略评估器。

**约束**：引擎层保持零外部依赖（仅标准库）。所有运行时上下文（workspace_root、会话信息）必须通过 context 字典传递。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 扩展 `ToolRule` 以包含可选的 `arg_conditions` 用于基于 glob 的参数值匹配
- 扩展 `ToolAccessPolicy.evaluate()` 以在工具名称匹配后匹配参数条件
- 添加内置危险模式检测，不能被用户规则覆盖
- 为文件路径参数添加工作空间边界执行
- 保持与现有仅名称规则的完全向后兼容

**非目标：**
- 复合命令解析（`&&`、`||`、`;`）——推迟到未来增强（Claude Code 做了这个，但增加了复杂性）
- 基于正则表达式的参数匹配——glob 模式覆盖 80% 的用例（根据调查）
- LLM 辅助批准分类（HermesAgent 智能批准）——未来增强
- Tree-sitter AST 命令分析（AgentScope 方法）——对于 MVP 来说过度设计
- 内核级隔离（Landlock/seccomp）——与参数检查正交，已由 Docker 沙箱执行器处理（9.4c）

## Decisions — 决策

### D31: 参数条件的 Glob 模式匹配

使用 `fnmatch` glob 模式进行参数值匹配，与现有的工具名称匹配一致。

```python
ToolRule(DENY, "write_file", arg_conditions={"path": "*.env"})
ToolRule(ASK,   "execute_code", arg_conditions={"code": "*os.system*"})
ToolRule(ALLOW, "read_file", arg_conditions={"path": "src/*"})
```

**理由**：Claude Code 使用 glob（`Bash(rm *)`），AgentScope 使用前缀模式（`npm run:*`）。两者都类似 glob。Glob 已经存在于我们的代码库中（`fnmatch`），需要零新依赖，并且用户容易理解。Regex（HermesAgent）因学习成本更高和调试困难被拒绝。

**被拒绝的替代方案**：
- 正则匹配——更高的复杂性，用户难以掌握正则模式
- Tree-sitter AST 解析——需要外部依赖（tree-sitter），对于 MVP 过度设计
- 仅前缀匹配（`npm run:*`）——glob 的子集，没有优势

### D32: 作为 DENY 基线的内置危险模式

在 `engine/tool_access.py` 中定义 `DANGEROUS_PATTERNS` 常量——一个 `(tool_name_glob, arg_key, arg_glob, description)` 元组列表。这些模式在用户定义规则之前检查，且不能被 `ALLOW` 规则覆盖。

```python
DANGEROUS_PATTERNS: list[DangerousPattern] = [
    # Shell 命令
    DangerousPattern("bash", "command", "rm -rf /",       "递归根目录删除"),
    DangerousPattern("bash", "command", "mkfs*",           "文件系统格式化"),
    DangerousPattern("bash", "command", "dd if=*of=/dev/", "磁盘覆写"),
    DangerousPattern("bash", "command", "*curl*|*sh",      "远程代码执行"),
    DangerousPattern("bash", "command", ":*()*{*}*",        "fork 炸弹"),
    # 代码执行
    DangerousPattern("execute_code", "code", "*os.system*",   "OS 系统调用"),
    DangerousPattern("execute_code", "code", "*subprocess*",   "子进程调用"),
    DangerousPattern("execute_code", "code", "*eval(*",         "eval 执行"),
    DangerousPattern("execute_code", "code", "*exec(*",         "exec 执行"),
    # 敏感文件
    DangerousPattern("write_file", "path", "*/.ssh/*",      "SSH 密钥写入"),
    DangerousPattern("write_file", "path", "*/.env*",       "环境变量文件写入"),
    DangerousPattern("write_file", "path", "*/.bashrc",     "Shell 配置写入"),
    DangerousPattern("write_file", "path", "/etc/*",        "系统配置写入"),
    DangerousPattern("read_file",  "path", "/etc/passwd",   "密码文件读取"),
    DangerousPattern("read_file",  "path", "*/.ssh/id_*",   "SSH 密钥读取"),
    # SQL 危险操作
    DangerousPattern("*", "code",  "*DROP TABLE*",          "SQL 表删除"),
    DangerousPattern("*", "code",  "*DELETE FROM*",         "无 WHERE 的 SQL 删除"),
]
```

**理由**：HermesAgent 的 `DANGEROUS_PATTERNS` 提供了有效的安全基线。AgentScope 的 7 层分析确认了相同的类别。这些模式提供了开箱即用的安全性，无需用户手动配置每条规则。模式首先检查（在用户规则之前），结果为 `DENY`——它们不能被覆盖。

**被拒绝的替代方案**：
- 可配置的危险模式（存储在数据库中）——增加了复杂性，这些模式很少改变
- 危险模式使用 ASK 而不是 DENY——DENY 更安全；用户可以为安全变体添加带有 `arg_conditions` 的显式 ALLOW 规则（例如 `rm -rf node_modules/`）

### D33: 作为策略层的工作空间边界（不是模型字段）

添加 `WorkspaceBoundaryPolicy` 作为 `engine/tool_access.py` 中的辅助类，检查文件路径参数是否解析在 `workspace_root` 内。`workspace_root` 通过 `context` 字典传递（来自服务层）。

```python
class WorkspaceBoundaryPolicy:
    def check(self, tool_name: str, arguments: dict, workspace_root: str) -> AccessDecision | None:
        """检查工具是否在工作空间边界内的文件上操作。

        如果路径在工作空间内返回 EXECUTE，如果在外部返回 REQUIRE_APPROVAL，
        如果工具没有路径参数返回 None。
        """
```

评估发生在用户规则和风险级别回退之间：

```
第 1 层：危险模式（DENY——不能覆盖）
第 2 层：用户规则（带 arg_conditions 的 DENY → ASK → ALLOW）
第 3 层：工作空间边界（内部 → ALLOW，外部 → ASK）
第 4 层：风险级别回退
第 5 层：沙箱路由
```

**理由**：工作空间边界在概念上位于显式规则（用户最了解）和风险级别默认值（系统回退）之间。如果用户显式配置了规则，它优先。如果没有规则匹配，工作空间边界提供合理的默认值：内部 = 受信任，外部 = 不受信任。

**被拒绝的替代方案**：
- 从工作空间边界自动生成 ALLOW/ASK 规则——不够透明，难以调试
- 将工作空间边界放在服务层——破坏了引擎的零依赖约束
- 在用户规则之前检查工作空间边界——用户应该能够覆盖工作空间默认值

### D34: ToolPolicyModel 中的 arg_conditions（JSON 列）

向 `ToolPolicyModel` 添加 `arg_conditions` JSON 列，用于持久化的参数级规则。

```python
class ToolPolicyModel(BaseModel):
    # ... 现有字段 ...
    arg_conditions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
```

示例行：
```json
{
  "workspace_id": "...",
  "rule_action": "deny",
  "tool_pattern": "write_file",
  "arg_conditions": {"path": "*.env"},
  "priority": 10
}
```

**理由**：与现有的 `ToolPolicyModel` 存储模式一致。JSON 列足够灵活，可以处理任何参数键-值组合。SQLAlchemy JSON 列类型适用于 PostgreSQL、MySQL 和 SQLite。

**被拒绝的替代方案**：
- 单独的 `ToolArgPolicyModel` 表——过早规范化，增加了连接复杂性
- 存储为分号分隔的字符串——脆弱，无类型安全
- 不持久化（仅运行时）——用户无法配置持久的参数级规则

### D35: 评估顺序——危险模式优先，arg_conditions 在名称匹配后

扩展的评估流程：

```
1. DANGEROUS_PATTERNS 检查（DENY——绕过免疫，不能被覆盖）
2. 用户规则匹配（DENY → ASK → ALLOW）
   对于每个层级，按优先级排序：
     a. 使用 fnmatch 检查工具名称
     b. 如果名称匹配且规则有 arg_conditions：
        使用 fnmatch(arg_value, pattern) 检查每个 arg_condition
        仅当 ALL 条件匹配时才匹配
     c. 如果名称匹配且规则没有 arg_conditions：
        立即匹配（仅名称匹配——向后兼容）
3. 工作空间边界检查（如果没有规则匹配且工具有路径参数）
4. 风险级别回退
5. 沙箱路由
```

**理由**：危险模式必须首先检查以确保它们不能被绕过。在用户规则中，仅名称规则是"更广泛的"，而 arg_conditions 规则是"更具体的"——priority 字段控制每个层级内的排序。向后兼容性得以保持：没有 arg_conditions 的现有规则与之前完全相同地工作。

## Risks / Trade-offs — 风险 / 权衡

- **[Glob 限制]** Glob 无法表达像"没有 WHERE 子句的 DELETE FROM"这样的复杂模式 → 缓解：使用 `*DELETE FROM*` 作为危险模式（过度匹配但更安全），用户可以为安全变体添加 ALLOW 规则
- **[没有复合命令解析]** `bash(safe-cmd *)` 不能防御 `safe-cmd && dangerous-cmd` → 缓解：记录为已知限制；未来增强以解析 `&&`、`||`、`;`（Claude Code 方法）
- **[危险模式误报]** 代码块中的 `*subprocess*` 会误伤 subprocess 模块的合法使用 → 缓解：用户可以为特定的安全模式添加 `ALLOW` 规则；危险模式使用 DENY 是故障安全的
- **[工作空间根目录可用性]** 如果 `context["workspace_root"]` 未设置，跳过工作空间边界检查 → 缓解：服务层必须提供 workspace_root；记录为需求
- **[性能]** 每次工具调用现在检查危险模式 + 用户规则 + 工作空间边界 → 缓解：模式列表很小（< 20 条），fnmatch 很快；每次评估测量 < 0.1ms
