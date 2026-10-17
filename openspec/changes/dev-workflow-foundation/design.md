# Design

## Context

见 proposal.md。当前 CI 使用显式 workspace 包列表，worktree 仍仅安装根包 dev extra；Windows 的 hook 解释器适配保存在本机副本。流程文档同时存在逐 task 全量测试、合并前归档等与 AGENTS.md 冲突的约定。

## Goals / Non-Goals

目标：让环境同步及检查范围由同一个受版本控制的入口定义；在 Windows/POSIX 与多个 worktree 上正确选择本地解释器；保留现有 push 授权及 CI 全量验证。

非目标：检查自动选择、检查结果缓存、结构化运行历史、测试 harness 重构、Coding Agent 专用能力和产品运行时改动。

## Decisions

- 使用标准库 `scripts/dev.py`，以脚本位置确定仓库根。Python 可直接调用，`scripts/dev.sh` 为 shell/direnv/hooks 提供解释器选择和缺失环境时的 uv 引导。相比新增 task-runner 依赖，这种方式可在环境尚未安装时运行。
- `sync` 和 `sync --check` 共用显式 workspace 包列表（包含 CI 所需可选包及 runner）、dev extra、Python 3.12 和 `--locked`。lint 使用 `lint` dependency group 保持轻量；PostgreSQL 场景额外启用已声明的 `postgres` extra。显式指定本 worktree 的 `.venv`，不继承其他 worktree 的项目环境。CI、direnv、worktree 均调用此入口；不复制包列表。
- `check lint|format|types|all` 提供固定命令，lint/format 覆盖平台、packages、tests 和本工具；types 与 CI 一致覆盖 src/packages。`test` 透传显式 pytest 参数，不推导测试集合。子进程固定 cwd，保留退出码，不使用 shell 拼接。
- `doctor` 只读检查 uv、工作树虚拟环境的 Python/安装包以及 hook 路由，不修复或打印凭据；失败给出修复命令。
- hooks 安装器向 Git 当前 hook 目录写入轻量 shim。shim 从 Git 查询当前 worktree 根，再执行当前分支的 scripts hook。默认拒绝覆盖非托管 hook；显式 `--replace` 先保留恢复副本。所有 hook 保留 fail-closed 行为，pre-commit 无条件执行受版本控制的 main 分支保护。
- 统一现有文档，不增加重复工作流。归档在合并后由用户触发，目录与代码状态在 PR 中同步；按可审阅逻辑单元提交，不要求每个 task 提交或全量验证。push 仍需明确授权。

## Risks / Trade-offs

- 锁文件或依赖不一致会使同步失败 → 明确失败而不是重新解析；需要有意更新 uv.lock。
- Git hooks 在 clone 的 worktree 间共享 → shim 按调用工作树分派，安装旧 hook 时保留备份，测试验证另一个 worktree 的路由。
- 不同平台的子进程语义不同 → 使用参数列表、当前 worktree 的 Python 和 Git Bash/POSIX shell；增加平台路径及有空格路径的回归。
- 完整 mypy 范围比旧本地 hook 更大 → 利用增量缓存，保持本地和 CI 类型验证一致。

## Migration Plan

现有入口改为调用统一工具；已有 clone 执行 `hooks install --replace` 保留备份并安装 shim。环境用 sync 收口。回滚时恢复 hook 备份，并恢复此前脚本；不迁移产品数据。
