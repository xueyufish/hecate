## Context — 背景

Hecate 拥有一个完善的安全系统，涵盖三个组件：

1. **ToolGateEvaluator**（`engine/tool_gate.py`）— 评估每个工具的 `available_when` 表达式以控制可见性（从 LLM 上下文中隐藏）。使用带限制命名空间的 Python `eval()`。
2. **ToolAccessPolicy**（`engine/tool_access.py`）— 5 层执行时评估：DangerousPattern → RuleEngine → WorkspaceBoundary → RiskLevel → SandboxRouting。返回 `AccessDecision`（EXECUTE / EXECUTE_SANDBOX / REQUIRE_APPROVAL / DENY）。
3. **PreToolHook / PostToolHook**（`engine/guardrail.py`）— 用于任意前/后执行检查的 guardrail 钩子。

问题在于：这些是硬编码且孤立的——你不能添加新层、按代理配置它们或以声明方式组合它们。企业部署需要按代理策略、插件可用性检查和审计模式。

**研究基础**（分析了 14 个平台）：
- OpenClaw：8 层可组合管道（profile → provider → global → agent → provider → agent → sandbox → subagent）、工具组、MCP 同意信封
- AgentScope：3 维模型（Mode + Rules + Built-in Checks）、5 个 PermissionModes、`bypass_immune` 标志、建议规则自动生成
- Salesforce Agentforce：每个动作的 `available when`、每次迭代重新评估、确定性测试、平台 RBAC（对象/字段/记录）
- Amazon Bedrock AgentCore：Cedar 策略即代码、网关边界执行、Lambda 拦截器、NLC→Cedar 转换、自动推理验证
- Google Gemini Enterprise：使用 NLC 的语义治理策略、dry-run 模式、按代理身份
- IBM watsonx：`ToolPermission` 枚举（READ_ONLY/READ_WRITE）、安全控制中心、OBO 令牌交换
- Huawei AgentArts：基于 IAM（Action/Resource/Condition）、生产级沙箱、MCP 网关
- Dify：插件清单权限声明（粗粒度）

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 可组合的策略管道，包含可插拔层（PolicyLayer ABC）
- 5 个管道层：PluginAvailability、Profile、Visibility、Security、Mode
- 按代理策略配置（mode + allow/deny 列表，数据库支持）
- 声明式规则引擎（glob 模式 + 参数条件）
- PermissionMode（DEFAULT / RESTRICTED / AUDIT）
- 将现有 ToolAccessPolicy 和 ToolGateEvaluator 包装为管道层（零重写）
- 用于策略 CRUD 的 REST API
- 每个策略决策的审计日志

**非目标：**
- Cedar 策略语言（太重，新运行时）
- 自然语言约束（非确定性，不适合安全路径）
- 按通道限制（延期至未来增强）
- 前端策略编辑器 UI（API 优先；UI 在后续版本）
- 重写 ToolAccessPolicy 内部逻辑（包装，不重写）
- ACCEPT_EDITS / BYPASS 模式（代码执行特有的，非平台级）
- 按租户连接池隔离（由现有 workspace_id 处理）

## Decisions — 决策

### Decision 1: 包装，不重写（选项 B）

**选择**：将现有的 `ToolAccessPolicy` 和 `ToolGateEvaluator` 包装为管道层。不重写其内部逻辑。

**理由**：现有的 5 层 ToolAccessPolicy 经过实战考验，涵盖了真实的安全威胁（危险模式、工作空间边界、风险级别）。重写可能导致安全回归。包装是零风险迁移，保留所有现有行为。没有策略配置的代理使用 DEFAULT 模式（向后兼容）。

**被拒绝的替代方案**：完全重写为可插拔层（选项 A）。风险更高、实现周期更长、没有增量价值。

### Decision 2: 5 层管道架构

**选择**：具有 5 个层的管道，按固定评估顺序执行。

```
Layer 0: PluginAvailabilityLayer  → 如果插件/MCP 未启用则 DENY
Layer 1: ProfileLayer             → 根据按代理规则 DENY/ALLOW
Layer 2: VisibilityLayer          → 如果 available_when 失败则 HIDE（仅 LLM 上下文）
Layer 3: SecurityLayer            → EXECUTE/SANDBOX/APPROVAL/DENY（包装 ToolAccessPolicy）
Layer 4: ModeLayer                → 根据 PermissionMode 覆盖
```

**理由**：
- PluginAvailability 最先执行，因为它是最便宜的检查（无表达式求值、无规则匹配）
- Profile 在 Security 之前执行，因为按代理规则应优先于通用风险级别回退
- Visibility 仅影响 LLM 上下文（HIDE），不影响执行时
- Security 包装现有逻辑——最复杂的层，不变
- Mode 最后执行，因为 AUDIT 模式需要看到 Security 层的最终决策后才能覆盖

**被拒绝的替代方案**：OpenClaw 的 8 层管道——对 Hecate 的用例来说太复杂，大多数层（提供者级、子代理级）不适用。

### Decision 3: 3 种 PermissionModes（非 5 种）

**选择**：DEFAULT、RESTRICTED、AUDIT。

| 模式 | 行为 | 参考 |
|------|----------|-----------|
| DEFAULT | 正常管道评估 | AgentScope DEFAULT |
| RESTRICTED | 仅白名单工具通过（ProfileLayer 必须 ALLOW） | AgentScope EXPLORE |
| AUDIT | 所有工具允许，但每个决策都记录 | Google SGP dry-run |

**理由**：AgentScope 的 5 种模式（DEFAULT/ACCEPT_EDITS/EXPLORE/BYPASS/DONT_ASK）是为代码执行场景设计的。Hecate 是平台层——ACCEPT_EDITS 和 BYPASS 是代理框架关注点，不是平台关注点。DONT_ASK 通过不配置批准回调来覆盖。

**AUDIT 模式价值**：企业可以以 AUDIT 模式部署一周，审查哪些工具会被拒绝，然后切换到 DEFAULT。这正是 Google 的 dry-run 模式。

### Decision 4: 声明式规则（glob + arg_conditions），非策略 DSL

**选择**：`ToolPolicyRuleModel`，使用工具名称的 glob 模式 + 可选的 arg_conditions 字典（每个参数字键使用 glob 模式）。语义与 `tool_access.py` 中的现有 `ToolRule` 相同。

**理由**：
- Cedar 太重（新语言 + 运行时）
- NLC 是非确定性的
- Python eval() 仅用于 `available_when`（可见性），不用于策略规则
- Glob 模式正是 OpenClaw、AgentScope 和我们现有的 ToolRule 已经使用的——经过验证、简单、可审计

### Decision 5: 保留两个拦截点

**选择**：管道在两个点运行：
1. **可见性过滤**（在 LLMWorker 中，LLM 调用前）：运行 PluginAvailability + Profile + Visibility 层。获得 HIDE 决策的工具从发送给 LLM 的工具列表中移除。
2. **执行时评估**（在 ToolWorker 中，工具调用前）：运行所有 5 层。获取最终的 ALLOW/DENY/REQUIRE_APPROVAL/EXECUTE_SANDBOX 决策。

**理由**：Salesforce Agentforce 证明了 `available when`（可见性）和执行时访问控制是两个不同的关注点。Hecate 已经有两个拦截点——我们保留它们。

## Risks / Trade-offs — 风险 / 权衡

- **[性能开销]** — 管道每次工具调用评估多个层。缓解措施：层是纯 Python，无 I/O（插件可用性检查除外，可以缓存）。现有的 ToolAccessPolicy 已经评估了 5 层；增加 2 层可以忽略不计。

- **[AUDIT 模式带来的虚假安全感]** — AUDIT 模式允许所有工具但记录决策。运营人员可能忘记切换到 DEFAULT。缓解措施：AUDIT 模式在每次将 DENY 覆盖为 ALLOW 时记录 WARNING。仪表板可以显示"AUDIT 模式活跃"横幅。

- **[插件可用性检查延迟]** — 每次工具调用检查插件启用状态可能增加延迟。缓解措施：插件状态由 PluginService 缓存在内存中；检查是字典查找，不是数据库查询。

- **[向后兼容性]** — 没有策略配置的现有代理必须完全相同地工作。缓解措施：DEFAULT 模式是零配置默认值；SecurityLayer 包装现有的未更改的 ToolAccessPolicy。
