# Proposal

## Why

Hecate 的 worktree、direnv、CI、Git hooks 与开发文档使用不同的环境安装和验证命令，导致缺包、平台差异及重复验证。统一开发入口，使新 worktree 可复现启动，所有 Coding Agent 和开发者使用同一组命令。

## What Changes

- 增加标准库实现的 `scripts/dev.py`：统一 workspace 同步、只读环境检查、显式检查项及显式 pytest 目标。
- worktree、direnv 和 CI 调用相同的锁定环境同步逻辑；统一 lint/format/typecheck 的范围。
- 安装转发型 Git hooks，从当前 worktree 调用受版本控制的 hook 脚本；兼容 Windows/POSIX，保留已有 hook 的恢复副本。
- 对齐 AGENTS.md、开发流程文档及 PR 模板；局部测试用于开发，完整测试及平台矩阵由 CI 执行，归档遵从当前仓库规则。
- 增加隔离的开发工具回归测试，覆盖环境选择、错误传播和跨 worktree 的 hook 路由。

## Capabilities

### New Capabilities

- `developer-workflow`: 仓库开发环境、检查入口与 Git hook 安装/执行约定。

### Modified Capabilities

无；不修改产品运行时能力。

## Impact

影响 `scripts/`、`.envrc`、CI workflow、开发指引和测试。无需新增第三方依赖；不修改 Hecate 产品 API、数据模型或运行时架构。测试自动选择、结构化运行历史、失败诊断、语义导航和场景回放不在本次范围。保留 push 需要明确授权以及禁止跳过 hooks 的约束。
