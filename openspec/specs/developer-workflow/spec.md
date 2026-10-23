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
开发指南 SHALL 引用统一命令，要求受影响行为的局部验证，并将完整测试矩阵交由 CI。普通开发 SHALL 默认直接执行，按需更新现有规格或设计文档；测试、静态检查、CI、PR review 和真实验证结果 SHALL 不因跳过 OpenSpec 而省略。逻辑提交、push 明确授权及手动 OpenSpec 命令的阶段边界 SHALL 遵循一致约定。

#### Scenario: Implementing a task
- **WHEN** 开发者按指南推进一个任务
- **THEN** 指南不要求每个 task 重跑全量测试或机械创建独立提交
- **AND** 不将未运行、跳过或待完成的 CI 报告为通过

### Requirement: Manual OpenSpec activation
Agent SHALL 仅在用户手动调用 OpenSpec slash command、命名 skill 或明确要求执行 OpenSpec CLI 命令时启动对应动作。普通需求、方案讨论、风险判断、已有 change 和提及 OpenSpec SHALL NOT 触发命令、技能或新 change 产物。该默认行为 SHALL 同样适用于 API/公共或持久化契约、安全/租户隔离、Runtime、架构、迁移、CI/release 门禁、跨域和不确定工作。Codex 技能 SHALL 禁用隐式调用，其他工具入口 SHALL 通过明确的调用规则保持同样边界。

#### Scenario: Ordinary high-risk implementation
- **WHEN** 用户要求实现权限边界、迁移、Runtime 或其他高风险修改，未手动调用 OpenSpec
- **THEN** Agent 直接调查、明确验收条件、实施并验证，不自动生成 proposal、delta specs 或 archive

#### Scenario: Uncertain requirement
- **WHEN** 普通开发中存在调查后仍不明确的重要需求或兼容性/安全选择
- **THEN** Agent 向用户提出具体问题，不用自动启动 OpenSpec 代替决策

#### Scenario: Design discussion or existing change
- **WHEN** 用户讨论 OpenSpec、要求普通设计方案，或仓库已有 change 但本次未调用对应动作
- **THEN** Agent 不运行 OpenSpec 命令或自行创建 change；可阅读并按实际行为更新现有文档

#### Scenario: Manual command selection
- **WHEN** 用户手动调用 OpenSpec 命令或命名 skill
- **THEN** Agent 按该动作和选定 schema 执行必要的上下文、依赖、任务和验证步骤

### Requirement: OpenSpec action boundaries
手动 propose SHALL 只创建规划产物；apply SHALL 从必需产物实施并验证任务；sync SHALL 同步规格；archive SHALL 保留完成状态与 delta 同步检查；explore SHALL 保持讨论边界，除非用户明确要求捕获规划产物。默认 schema SHALL 使用配置值，用户显式选定其他 schema 时 SHALL 遵从其产物契约。Agent SHALL NOT 自动跨入另一个动作，除非用户明确要求该动作或它是所选命令的必要子步骤。普通实施授权 SHALL NOT 转换为 OpenSpec 阶段授权，push SHALL 继续要求聊天中的明确确认。

#### Scenario: Proposal command completes
- **WHEN** 用户只调用 propose，规划产物已完成
- **THEN** Agent 展示提案后停止，不自动 apply 或 archive

#### Scenario: Apply command completes
- **WHEN** 用户只调用 apply，任务及必要验证已完成
- **THEN** Agent 报告实际结果，不自动 sync 或 archive

#### Scenario: Explicit action sequence
- **WHEN** 用户明确要求连续执行 propose、apply 和 archive
- **THEN** Agent 按阶段依赖和验证要求执行该序列，不为已要求的阶段重复索取消息

#### Scenario: Archive includes sync
- **WHEN** 用户调用 archive，存在需要同步的 delta
- **THEN** Agent 在该动作内检查并同步规格，确认一致后归档；不将归档报告为 CI、review 或 merge 已完成

#### Scenario: Inspection is not implementation
- **WHEN** 用户只要求运行 OpenSpec 状态或查看命令
- **THEN** Agent 仅执行对应查看动作，不自行规划或实施

### Requirement: Preserve checked-in agent workflows on worktree start
worktree 启动 SHALL 默认保留受版本控制的 Agent 技能与命令，不运行重新生成流程且不激活 OpenSpec change 工作流。显式选择工具时 SHALL 允许初始化对应工具并提示审阅可能覆盖定制的 diff。原有 `--no-tools` 入口 SHALL 保持兼容，环境同步失败 SHALL 继续返回失败且不得报告 ready。

#### Scenario: Default startup preserves customization
- **WHEN** 在带有定制技能的仓库中以默认参数启动 worktree
- **THEN** 启动不会调用 OpenSpec 工具生成，worktree 保留定制技能内容

#### Scenario: Explicit tool initialization
- **WHEN** 开发者显式指定要初始化的工具
- **THEN** 启动将该选择传给 OpenSpec 初始化，且不默默扩大工具集合或激活开发流程
