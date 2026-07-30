## Context — 背景

Hecate 有 4 个 Guardrail Hooks（PreLLMHook、PostLLMHook、PreToolHook、PostToolHook），在 AI 层进行拦截。它们是返回 ALLOW/BLOCK/SANITIZE 的 Python ABC。缺少的是：会话生命周期事件（start/end/prompt/compact）、按工具定位（所有工具钩子为每个工具触发）以及设置驱动的 shell 命令钩子（需要编写 Python 类）。

**研究基础**（14 个平台）：
- Claude Code：14+ 个钩子事件（PreToolUse、PostToolUse、SessionStart、SessionEnd、UserPromptSubmit、PreCompact、Stop 等）、工具匹配器（精确/管道/正则）、用于参数过滤的 `if` 字段、5 种处理器类型（command/http/mcp_tool/prompt/agent）、JSON 设置配置（3 个层级）、stdin JSON / 退出码 / stdout 上下文注入
- AgentScope：6 个洋葱中间件位置（on_reply/on_reasoning/on_acting/on_model_call/on_compress_context/on_system_prompt）、MiddlewareBase 类、自动检测实现的钩子、用于 OTel 跨度的 TracingMiddleware
- Salesforce：before_reasoning / after_reasoning 确定性块、将程序逻辑与 LLM 指令混合的 Agent Script DSL、每个动作的 available_when 门控
- 企业平台（Bedrock/Google/IBM）：基于策略的，非基于钩子的

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 4 个会话事件钩子 ABC（SessionStart、SessionEnd、UserPromptSubmit、PreCompact）
- 现有 PreToolHook/PostToolHook 上的工具名称匹配器（正则/精确/管道）
- ShellCommandHook — 设置驱动的 shell 命令执行
- JSON 钩子配置，支持按工作空间/按代理作用域
- 用于钩子管理的 REST API
- 向后兼容——没有匹配器的现有钩子不变地工作

**非目标：**
- ReAct 循环中间件（AgentScope 洋葱模式）— E3，延期
- HTTP/MCP 工具/提示/代理处理器类型 — Claude Code 有 5 种类型；我们从仅 shell 开始
- 用于参数级过滤的 `if` 字段 — 延期，v1 版本工具名称匹配足够
- PostToolUseFailure、PostToolBatch、SubagentStart/Stop 事件 — 延期
- Notification、PermissionRequest、CwdChanged 事件 — Claude Code 特有的，不适用

## Decisions — 决策

### Decision 1: 扩展 Guardrail Hooks，不替换

**选择**：为现有 PreToolHook/PostToolHook 添加 `matcher` 参数。在现有钩子旁边添加 4 个新的会话钩子 ABC。

**理由**：现有的 4 个 Guardrail Hooks 工作良好且已测试。替换它们有回归风险。添加可选的匹配器是向后兼容的——没有匹配器的钩子为所有工具触发（当前行为）。

### Decision 2: Shell 命令钩子（Claude Code 模式）

**选择**：ShellCommandHook 执行通过 JSON 配置的 shell 命令。stdin 接收事件 JSON，退出码 0=继续，退出码 2=阻止，stdout=注入上下文（仅适用于 SessionStart/UserPromptSubmit）。

**理由**：Claude Code 的 shell 钩子模式经过验证且对开发者友好。企业运营人员可以配置钩子（自动格式化、lint、审计日志）而无需编写 Python。安全性：shell 执行受 `HOOK_SHELL_ENABLED` 设置控制（默认为 False），每个钩子有超时。

### Decision 3: 匹配器语法（兼容 Claude Code）

**选择**：匹配器字符串评估方式：纯字母数字 + `|` → 精确/管道分隔；任何正则特殊字符 → 正则。空/None → 匹配所有。

示例：
- `"web_search"` → 精确匹配
- `"Edit|Write"` → 匹配任一
- `"mcp__github__.*"` → 正则匹配所有 GitHub MCP 工具
- `None` 或 `"*"` → 匹配所有工具

**理由**：Claude Code 的匹配器语法简单且经过验证。我们直接采用以获得熟悉度。

### Decision 4: JSON 设置配置

**选择**：通过 JSON 设置或数据库支持的 HookConfigModel 配置钩子：

```json
{
  "hooks": [
    {
      "event": "PostToolUse",
      "matcher": "Edit|Write",
      "command": "prettier --write $FILE_PATH",
      "timeout": 10
    },
    {
      "event": "SessionStart",
      "command": "echo 'Reminder: use Bun, not npm'",
      "timeout": 5
    }
  ]
}
```

**理由**：Claude Code 使用 JSON 设置（3 个层级：用户/项目/本地）。Hecate 使用数据库支持的模型实现按工作空间/按代理作用域，外加全局设置回退。

## Risks / Trade-offs — 风险 / 权衡

- **[Shell 执行安全]** — ShellCommandHook 执行任意命令。缓解措施：`HOOK_SHELL_ENABLED` 默认 False，每个钩子超时，所有钩子执行的审计日志记录。

- **[性能]** — 每次工具调用时匹配器评估增加开销。缓解措施：在配置加载时预编译正则模式，当匹配器为 None 时跳过评估。

- **[向后兼容性]** — 现有的 PreToolHook/PostToolHook 实现没有匹配器。缓解措施：匹配器是可选的；没有匹配器的钩子匹配所有内容（当前行为）。
