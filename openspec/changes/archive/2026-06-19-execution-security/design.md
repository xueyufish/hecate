## Context — 背景

ToolModel 存储了 `risk_level`（String，默认 "LOW"）、`approval_required`（Boolean，默认 False）、`sandbox_enabled`（Boolean，默认 False）和 `sandbox_config`（JSON）字段。AgentModel 存储了 `risk_level` 和 `guardrail_config`（JSON）。Docker SandboxExecutor 和容器池已完全实现（9.4c/9.4d）。EnginePort 将 `tool_execute_sandbox()` 暴露为可选方法。

然而，ToolWorker 无条件地调用 `port.tool_execute()`——这些字段都不影响执行。该平台没有零工具级安全执行。

一项 10 平台研究调查（Claude Code、Salesforce Agentforce、Google ADK、IBM watsonx、HermesAgent、OpenClaw、openJiuwen、Huawei AgentArts、Alibaba AgentScope、AutoGPT）显示，没有平台使用明确的 4 级风险分类法，但行业正在向 allow/deny/ask 规则引擎（Claude Code、HermesAgent RFC #21849）结合沙箱隔离的方向趋同。HermesAgent 的容器绕过批准模式被识别为 CVE-2026-29607（9.9 严重）——我们明确避免它。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 执行 ToolModel 上已经存在的 `approval_required`、`sandbox_enabled` 和 `risk_level` 字段
- 提供一个规则引擎，用于精确的每工具 allow/deny/ask 模式匹配
- 将启用沙箱的工具路由到 `tool_execute_sandbox()` 而不是 `tool_execute()`
- 通过带故障关闭超时的阻塞回调支持人工审批
- 为基于作用域的缓存（SESSION/PROJECT/GLOBAL）持久化批准决策
- 保持向后兼容（无策略配置 = 所有工具 EXECUTE）

**非目标：**
- 批准 API 端点（推迟到功能 9.4e）
- 带会话恢复的异步批准模式（OpenClaw 模式——未来增强）
- 用于批准的图形级中断集成（Command.interrupt 重新进入语义——未来引擎加固）
- 多渠道批准路由（Slack/Discord/Telegram——未来增强）
- 每操作粒度切换（功能 9.4a——40+ 操作）
- 可信工作空间自动允许（功能 9.4b）
- 内容审核（功能 9.2a）

## Decisions — 决策

### D24: RiskLevel 作为 StrEnum（LOW/MEDIUM/HIGH/CRITICAL）

在 `engine/tool_access.py` 中定义 `RiskLevel(StrEnum)`。ToolModel 上的存储保持为 `String(20)` 以实现向后兼容——代码使用枚举，数据库存储字符串值。现有数据无需迁移。

每个级别映射到当没有明确规则适用时的默认执行行为：
- LOW：自动执行（只读、幂等工具）
- MEDIUM：自动执行；如果设置了 `sandbox_enabled` 则使用沙箱
- HIGH：需要批准，除非设置了 `sandbox_enabled`
- CRITICAL：无论是否使用沙箱，始终需要批准

**被拒绝的替代方案：**
- 将列迁移为本机枚举类型——无功能增益的不必要迁移风险
- 保持为自由格式字符串——失去类型安全和默认语义
- 使用 Salesforce MCP 注解（readOnly/destructive/idempotent/openWorld）——提示而非执行；与风险级别正交

### D25: 三层评估（规则 → 风险级别 → 沙箱）

`ToolAccessPolicy.evaluate()` 中的评估顺序：

```
第 1 层：规则引擎（精确——Claude Code / HermesAgent 模式）
  → 工作空间级拒绝规则（ToolPolicyModel）——绝对的，不能被覆盖
  → Agent 级允许/询问规则（guardrail_config）——每 Agent 定制
  → 模式匹配：工具名称（glob），例如"terminal(rm *)"、"write_file(.env*)"

第 2 层：风险级别策略（默认——我们的差异化优势）
  → 如果没有规则匹配，使用 risk_level 确定默认行为
  → LOW → EXECUTE
  → MEDIUM → EXECUTE（如果 sandbox_enabled 则为 EXECUTE_SANDBOX）
  → HIGH → REQUIRE_APPROVAL（如果 sandbox_enabled 则为 EXECUTE_SANDBOX）
  → CRITICAL → REQUIRE_APPROVAL（始终，无论 sandbox 如何）

第 3 层：沙箱路由（隔离——现有基础设施）
  → 如果 sandbox_enabled：路由到 port.tool_execute_sandbox()
  → 如果没有：路由到 port.tool_execute()
  → 沙箱不会绕过批准（与 HermesAgent CVE-2026-29607 相反）
```

**被拒绝的替代方案：**
- 仅风险级别（无规则引擎）——过于粗糙，无法表达"允许 git 但拒绝 rm"
- 仅规则引擎（无风险级别）——失去默认语义，10 平台研究显示这是 HermesAgent 的 RFC #21849 试图填补的空白
- 沙箱绕过批准（HermesAgent 模式）——CVE-2026-29607（9.9 严重），明确拒绝

### D26: 引擎层中的 ToolAccessPolicy（零依赖）

`ToolAccessPolicy` 是 `engine/tool_access.py` 中的一个具体类（与 `engine/tool_gate.py` 中的 `ToolGateEvaluator` 一致）。它将工具元数据 + 规则 + 上下文作为参数，并返回 `AccessDecision`。不查询数据库——规则数据由调用者（ToolWorker）传入。

**被拒绝的替代方案：**
- 带可插拔实现的 ABC——现阶段过度设计，一种评估策略就足够了
- 服务层类——会破坏引擎的零依赖约束
- PreToolHook 实现——GuardrailAction 只有 ALLOW/BLOCK/SANITIZE，没有 REQUIRE_APPROVAL 结果

### D27: ApprovalCallback 阻塞模式（不是 Command.interrupt）

批准通过 ToolWorker 内的阻塞异步回调实现，而不是通过 `Command.interrupt`。现有的中断机制在被中断节点之后恢复到下一个节点，但工具尚未执行——这将产生没有工具结果的对话。

```python
class ApprovalCallback(ABC):
    async def request_approval(
        self, tool_name: str, arguments: dict, risk_level: str, context: dict
    ) -> ApprovalDecision: ...

@dataclass
class ApprovalDecision:
    approved: bool
    reason: str = ""
    scope: ApprovalScope = ApprovalScope.ONCE
```

ToolWorker 等待 `approval_callback.request_approval()`，它会阻塞直到决策到达或超时到期。超时 = 拒绝（故障关闭），与 HermesAgent 和 OpenClaw 一致。

**被拒绝的替代方案：**
- Command.interrupt——恢复跳转到下一个节点，工具永远不会执行（见上面的分析）
- 修改 PregelRuntime 以支持中断时重新进入——过于侵入性，为所有用例更改中断契约
- 异步模式（OpenClaw 模式）——立即返回 approval_id，会话继续——对于 MVP 来说太复杂，推迟到未来增强

### D28: 规则存储——工作空间级 ToolPolicyModel + Agent 级 guardrail_config

两层规则存储，具有 Claude Code 风格的优先级：

```
第 1 层：ToolPolicyModel（工作空间级，数据库表）
  → 主要用于 DENY 规则（安全基线，由工作空间管理员设置）
  → 不能被 Agent 级规则覆盖
  → 字段：workspace_id、rule_action（DENY/ASK/ALLOW）、tool_pattern、priority

第 2 层：AgentModel.guardrail_config（Agent 级，JSON 字典）
  → 每 Agent 定制
  → 可以添加 allow/ask 规则但不能覆盖工作空间 DENY
  → 格式：{"tool_rules": {"allow": ["terminal(git:*)"], "ask": ["write_file(.env*)"]}}
  → 也可以设置"min_auto_approve_risk"（例如，"MEDIUM" = 高于 MEDIUM 的工具需要批准）
```

评估顺序：工作空间 DENY → Agent DENY → Agent ASK → Agent ALLOW → risk_level 回退。

**被拒绝的替代方案：**
- 仅在 guardrail_config 中的规则（无数据库表）——没有工作空间级基线，无法执行管理员设置的拒绝规则
- 仅在数据库表中的规则（无 Agent 配置）——过于僵化，Agent 无法定制
- 像 Claude Code（Managed > CLI > Local > Shared > User）这样的设置层次结构——对于我们的每工作空间单租户模型来说过度设计

### D29: 故障关闭超时（超时即拒绝）

默认超时：60 秒（可通过 `guardrail_config.approval_timeout` 每 Agent 配置）。超时时，返回 `ApprovalDecision(approved=False, reason="Approval timeout")`。对于自动化/定时任务场景（无交互用户），`guardrail_config.approval_mode = "deny"` 短路为立即拒绝而不等待。

这与 HermesAgent（`approvals.timeout: 60`，超时即拒绝）和 OpenClaw（超时即拒绝）模式匹配。没有平台使用故障开放（超时即允许）。

### D30: ApprovalScope（ONCE/SESSION/PROJECT/GLOBAL）

```python
class ApprovalScope(StrEnum):
    ONCE = "once"        # 每次调用重新批准（默认）
    SESSION = "session"  # 为当前会话缓存（内存中）
    PROJECT = "project"  # 持久化到数据库，在工作空间中的会话间有效
    GLOBAL = "global"    # 管理员级自动批准（基于配置）
```

ApprovalCallbackImpl 在阻塞前检查作用域缓存：
- ONCE：始终调用 `request_approval()`
- SESSION：检查内存字典 `{(session_id, tool_name): decision}`
- PROJECT：查询 ApprovalRecord 表获取活动批准
- GLOBAL：检查 Agent/工作空间配置获取自动批准规则

**被拒绝的替代方案：**
- 仅 ONCE 和 ALWAYS（Claude Code 简化）——失去团队工作流的粒度
- 仅 ONCE（Google ADK 基于调用的）——对重复工具调用来说摩擦太大

## Risks / Trade-offs — 风险 / 权衡

**风险：ToolWorker 在批准上阻塞会阻塞事件循环**
缓解：`ApprovalCallback.request_approval()` 是异步的；事件循环继续处理其他任务。只有特定的工具调用被阻塞。

**风险：批准等待期间没有崩溃恢复**
权衡：工具级阻塞（不是图形级中断）意味着等待期间没有检查点。如果进程崩溃，待处理的批准将丢失。MVP 接受——图形级中断加固是未来的增强。

**风险：规则引擎复杂性**
缓解：从简单开始——仅工具名称 glob 模式。内容匹配（如 Claude Code 的 `Bash(git *)` 的参数模式）推迟到 9.4a。

**权衡：工作空间级规则每次工具调用都需要数据库查询**
缓解：ToolPolicyModel 结果按会话缓存在 ToolWorker 中。规则是工作空间作用域的，很少更改。

**权衡：ApprovalScope 缓存可能批准过时的工具调用**
缓解：PROJECT/GLOBAL 批准是工具名称作用域的，不是参数作用域的。参数作用域的批准推迟到 9.4a。
