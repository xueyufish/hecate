## Why — 为什么

ToolModel 已经存储了 `risk_level`、`approval_required`、`sandbox_enabled` 和 `sandbox_config` 字段，但 ToolWorker 完全忽略了它们——每次工具调用都无条件地通过 `tool_execute()`。这意味着该平台对工具级安全策略的执行为零：没有基于风险的门控，没有沙箱路由，没有人工批准工作流。功能 9.4（执行安全）填补了这一空白，完成了"完整安全栈"里程碑项目，并交付了受调查的竞争对手（Claude Code、Salesforce、Google ADK、IBM watsonx、HermesAgent、OpenClaw、openJiuwen、Huawei AgentArts）中没有一个提供集成的三层系统的能力。

## What Changes — 变更内容

- **RiskLevel 枚举**——将现有的自由格式 `risk_level` 字符串形式化为 `StrEnum`（LOW/MEDIUM/HIGH/CRITICAL），带每个级别定义的默认执行语义
- **ToolAccessPolicy**——新的引擎层类，评估工具元数据（risk_level、approval_required、sandbox_enabled）加上上下文，针对可配置规则，产生 `AccessDecision`（EXECUTE / EXECUTE_SANDBOX / REQUIRE_APPROVAL / DENY）
- **规则引擎**——使用工具名称模式匹配（Claude Code 风格）的 allow/deny/ask 规则，存储在两个级别：工作空间级 `ToolPolicyModel`（拒绝基线）和 Agent 级 `guardrail_config`（允许/询问覆盖）
- **沙箱路由**——连接 ToolWorker，将启用了 `sandbox_enabled` 的工具路由到 `port.tool_execute_sandbox()` 而不是 `port.tool_execute()`
- **ApprovalCallback ABC**——新的引擎层抽象接口，用于阻塞批准请求；ToolWorker 在执行需要批准的工具之前等待批准；超时 = 拒绝（故障关闭）
- **ApprovalScope 枚举**——ONCE / SESSION / PROJECT / GLOBAL，控制批准决策保持有效的时间
- **ApprovalRecord 模型**——新的 ORM 模型，持久化批准决策用于作用域缓存（SESSION/PROJECT/GLOBAL）
- **ToolWorker 集成**——将 ToolAccessPolicy + ApprovalCallback 注入 ToolWorker；在每次 `tool_execute` 调用前执行策略

## Capabilities — 能力

### New Capabilities — 新增能力
- `execution-security`：三层工具执行安全——规则引擎（精确的 allow/deny/ask 模式匹配）+ 风险级别策略（每个 LOW/MEDIUM/HIGH/CRITICAL 的默认执行）+ 沙箱路由（现有 DockerSandboxExecutor 集成）。包括通过带故障关闭超时的阻塞 ApprovalCallback 的批准工作流。

### Modified Capabilities — 修改的能力
（无——ToolModel 已经存储了 risk_level、approval_required、sandbox_enabled、sandbox_config 字段；guardrail_config 已经是一个灵活的 JSON 字典。此变更在不改变现有规范级需求的情况下增加了执行逻辑和新模型。）

## Impact — 影响

- **引擎层**（`engine/tool_access.py`）：新增——ToolAccessPolicy、AccessDecision、RiskLevel、ApprovalScope、ApprovalCallback、ApprovalDecision、ToolRule、RuleAction
- **引擎层**（`engine/workers/tool_worker.py`）：修改——tool_execute 前的策略评估、沙箱路由、批准回调集成
- **模型层**（`models/approval.py`）：新增——ApprovalRecordModel + Pydantic 模式
- **模型层**（`models/tool_policy.py`）：新增——用于工作空间级拒绝规则的 ToolPolicyModel
- **服务层**（`services/approval.py`）：新增——ApprovalCallbackImpl（数据库持久化、通知、异步等待、超时）
- **迁移**：新增——approval_records 表、tool_policies 表
- **此变更中无 API 层更改**——批准 API 端点推迟到功能 9.4e
- **无破坏性变更**——现有字段被执行，而非重构；默认行为（无策略配置）= 所有工具 EXECUTE（向后兼容）
