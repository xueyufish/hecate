# Developer Workflow Specification

## Purpose

为 Hecate 开发者与 Coding Agent 提供一致的工作树环境、验证命令与 Git hook 路由，使不同平台和入口能够复现同一开发流程，并及时报告环境或验证错误。

## Requirements

### Requirement: Reproducible worktree environment
开发入口 SHALL 按锁文件同步当前 worktree 独立的虚拟环境，安装统一声明的开发 workspace 包与工具。worktree、direnv 和 CI SHALL 复用同一同步定义；锁文件不一致或同步失败 MUST 返回失败。

#### Scenario: New worktree bootstrap
- **WHEN** 创建一个新的开发 worktree
- **THEN** 初始化使用与 CI 相同的 workspace 包选择，包含平台可选包和 runner
- **AND** 虚拟环境属于该 worktree，不使用另一个 worktree 的环境

#### Scenario: Stale lockfile
- **WHEN** 项目依赖与锁文件不一致
- **THEN** 同步失败且不自动改写锁文件

### Requirement: Explicit shared verification commands
开发入口 SHALL 提供 lint、format、types 和显式目标的 pytest 命令，使用当前 worktree 的解释器、固定仓库根目录并传播失败退出码。CI 和 Git hooks SHALL 复用适用的检查定义。

#### Scenario: Package type regression
- **WHEN** packages 中存在类型错误
- **THEN** 本地类型检查与 CI 检查同样覆盖该目录并返回失败

#### Scenario: Focused pytest run
- **WHEN** 开发者指定一个测试文件与 pytest 选项
- **THEN** 入口透传该目标与选项，不自动扩大为全量测试

### Requirement: Read-only environment diagnosis
环境检查 SHALL 检查工作树环境和 hook 安装状态，报告缺失项及修复入口，且 MUST NOT 安装依赖、修改 Git 配置或暴露凭据。

#### Scenario: Incomplete environment
- **WHEN** 工作树缺少所需 workspace 包或解释器
- **THEN** 检查返回非零并指出环境同步入口

### Requirement: Worktree-aware managed hooks
已安装 hooks SHALL 调用当前 worktree 受版本控制的脚本，保留 main 分支保护、提交消息校验及 push 前类型检查。安装器 MUST 拒绝静默覆盖自定义 hook；显式替换时 SHALL 保留恢复副本。

#### Scenario: Shared clone with multiple worktrees
- **WHEN** 在同一 clone 的另一 worktree 触发托管 hook
- **THEN** hook 使用调用方工作树的脚本与虚拟环境

#### Scenario: Existing custom hook
- **WHEN** 未明确指定替换而目标目录已有非托管 hook
- **THEN** 安装失败且原 hook 保持不变

### Requirement: Consistent development guidance
开发指南 SHALL 引用统一命令，要求受影响行为的局部验证，并将完整测试矩阵交由 CI。任务 checkbox、逻辑提交、push 明确授权及合并后用户触发归档 SHALL 遵循相同生命周期约定。

#### Scenario: Implementing a task
- **WHEN** 开发者按指南推进一个任务
- **THEN** 指南不要求每个 task 重跑全量测试或机械创建独立提交
- **AND** 不将未运行、跳过或待完成的 CI 报告为通过
