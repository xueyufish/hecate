# AGENTS.md — Hecate

## 项目概述

Hecate 是一个**企业级、多租户、模型无关、以 MCP 为先的智能体平台**，支持云端 SaaS 和自托管私有部署。项目采用 Python 3.12+、FastAPI 和 SQLAlchemy 2.0 异步接口。

## 本地开发环境

Hecate 使用 [uv](https://docs.astral.sh/uv/)，虚拟环境位于 `.venv/`。每次用 `cd` 进入仓库或 worktree 时，仓库自带的 [`.envrc`](.envrc) 会自动创建并激活 `.venv`，也适用于通过 `./scripts/worktree-help.sh start` 创建的 worktree。每台机器只需配置一次。安装 direnv，将它接入当前 shell，再运行 `direnv allow`。具体命令见 `.envrc` 文件开头。没有 direnv 时，按「常用命令」手动安装。

## 常用命令

```bash
# 将锁定的开发工作区依赖安装到当前 worktree 的 .venv
bash scripts/dev.sh sync
bash scripts/dev.sh hooks install   # 已有复制安装的 hooks 时加 --replace，旧文件会备份
bash scripts/dev.sh doctor

# 运行完整测试套件，通常需要几分钟
python -m pytest tests/ -q

# 运行单个测试文件或测试函数
python -m pytest tests/test_runtime/test_pregel.py -v
python -m pytest tests/test_runtime/test_pregel.py::test_linear_execution -v

# 推送前运行以下两项检查
bash scripts/dev.sh check all
bash scripts/dev.sh test <affected dirs/files> -q   # 完整套件和平台矩阵由 CI 运行

# 启动基础设施（PostgreSQL 16、Qdrant、MinIO、Temporal）
docker compose -f docker/docker-compose.yml up -d

# 执行数据库迁移，需先启动 PostgreSQL
alembic upgrade head

# 启动应用
uvicorn hecate.main:app --reload
```

**Git hooks 按阶段执行。** pre-commit 只做快速检查（Ruff lint 和格式检查，通常数秒），commit-msg 通过 commitizen 验证提交消息，pre-push 对 `src/` 和 `packages/` 运行共享类型检查。完整测试结果以 CI 为准。pre-push 不运行 pytest，避免本地 hook 和 CI 重复执行。开发过程中可交互运行 `pytest --testmon`，快速验证受影响的测试，详见「开发中的快速测试」。禁止使用 `--no-verify` 绕过 hooks。

## 架构

Phase R 之后采用模块化单体架构。领域目录包括 `runtime/`、`tools/`、`enterprise/`、`channel/`、`studio/` 和 `ops/`，另有作为控制平面边界的 `execution/` 和语言无关的 `contracts/` 模式。跨领域的两层分别是 `core/`（基础设施和组合根）和 `models/`（数据契约）。从应用抽出的包位于 uv 工作区的 `packages/`。Pregel 运行时内核位于 `packages/hecate-runtime`，`src/hecate/runtime/` 是兼容转发层。

- 运行时的详细设计、自包含约束和扩展点清单见 [`packages/hecate-runtime/AGENTS.md`](packages/hecate-runtime/AGENTS.md)。
- 架构、引擎设计、概念和 ADR 索引见 [`docs/design/README.md`](docs/design/README.md) 及各章节的 README。
- 引擎边界从 `packages/hecate-runtime/src/hecate_runtime/ports.py` 中的 `RuntimePort` 开始。
- ORM 别名、`ChannelManager` 语义、`StreamMode` 和 `PERSISTENT_TOPIC` 迁移等注意事项见 [`docs/gotchas.md`](docs/gotchas.md)。

## 开发注意事项

- **Python 环境**使用 uv 和 Python 3.12，虚拟环境位于 `.venv/`。用 `bash scripts/dev.sh sync` 安装仓库声明的开发环境，包依赖写入 `pyproject.toml`。
- **Git** 采用 GitHub Flow，所有改动通过 PR 进入主线。**`main` 是受保护分支，禁止直接编辑、提交、amend 或推送。** 写入前运行 `git rev-parse --abbrev-ref HEAD`。如果结果是 `main`，先用 `git checkout -b <branch>` 创建主题分支，使用 `feat/`、`fix/`、`docs/` 或 `chore/` 前缀。如果已经误改 `main`，依次执行 `git stash push -u -m "..."`、`git checkout -b <topic>`、`git stash pop`。禁止在 `main` 上执行 `git reset --hard`。推送和面向 `main` 的 PR 会触发 CI，发布标签打在 `main` 的提交上。
- **仓库禁用合并提交。** GitHub 只允许「Rebase and merge」和「Squash and merge」。本地优先用 `git rebase origin/main` 保持主线历史线性。
- **推送授权。** 每次推送前都要在对话中取得用户明确确认，除非用户已明确为该任务或交付流程授予持续授权。持续授权可涵盖将本任务的特性分支普通推送到 `origin`，并为该分支创建或更新目标为 `main` 的 PR。发布前须由实现者之外的评审者批准待发布的具体 Git 树。只有用户已明确指定某个启动操作代表授权时，执行该操作才会触发持续授权。
  - 发布前核对任务、仓库、分支和获批的 Git 树，保留所有 Git hooks。如需 rebase，先在推送前完成。获批的 Git 树发生变化时，重新提交评审。如果推送流程可能在缺少该核对的情况下自动发布改写后的 Git 树，停止并报告阻塞原因。
  - 用户明确要求暂停或逐次确认时，优先遵循该要求。强制推送（含 `--force-with-lease`）、向受保护分支推送以及合入 PR 不在持续授权范围内。PR 由用户合入。
- **Git hooks** 用 `bash scripts/dev.sh hooks install` 安装。使用 `--replace` 迁移已安装的 hooks 并保留备份。共享分发脚本调用当前 worktree 中纳入 Git 的脚本和该 worktree 的解释器。用 `hooks check` 检查安装状态。pre-push 会先 rebase 到 `origin/main`，再运行共享类型检查。完整 pytest 套件由 CI 运行。

## 项目约定

### 开发流程

- 功能编号采用 `X.Y.Z` 格式，例如 `1.3.1`、`9.4a`。需要扩展编号时加字母后缀，禁止重新编号。
- **默认直接开发。** 常规任务直接修改代码、现有 spec 或文档，保留受影响的测试、共享检查、CI、PR 评审和实际验证结果。只有用户手动调用 OpenSpec 的斜杠命令、指定 skill，或明确要求执行对应 CLI 命令时，才执行指定的 OpenSpec 操作。任务规模、风险、API、安全与租户隔离、运行时语义、架构、CI 或发布门禁、不确定性、提及 OpenSpec、请求方案、存在既有变更以及智能体的风险判断，均不会触发 OpenSpec。详见[工作流指南](docs/how-to/openspec-workflow.md)。
- **按统一目标在独立 worktree 中开发。** `./scripts/opsx-flow.sh start <name>` 默认保留已纳入 Git 的 skills，OpenSpec 仍须手动触发。相关任务可以共用一份方案并分成多个逻辑清晰的提交，独立目标各自开 PR。先调查不确定之处，就仍未解决的关键需求、兼容性或安全决策向用户确认，在任务范围内处理常规实现问题。
- **手动触发 OpenSpec 时，遵循所选操作及其 schema。** propose 只生成规划材料，apply 实现并验证现有任务，sync 更新主规范，archive 在归档前检查完成情况并同步差异，explore 仅做只读探索，用户另行要求记录规划的情况除外。只有用户明确要求执行下一步，或所选命令本身依赖该子步骤时，才继续，例如 archive 中的 sync。用户明确要求的一系列操作可以连续执行。常规实现授权不包含 OpenSpec 授权。推送遵循上文的授权规则。
- **归档手动选择的变更时**，完成变更材料、任务和规范同步检查，单独报告待完成的 CI、评审和合入。定位或目录内容有变化时，检查 `docs/design/positioning.md`。规则变更时，精简 `AGENTS.md`。归档本身不授权推送、合入、发布或删除 worktree。

### 提交与 PR

- **提交消息遵循 Conventional Commits。** 格式为 `<type>(<scope>): <subject>`。允许的 `type` 有 `feat`、`fix`、`docs`、`refactor`、`test`、`chore`、`perf`、`build`、`ci`。`subject` 使用祈使句，末尾无句号，最多 72 个字符。正文和尾注可自由编写，关闭 issue 或 PR 时须包含 `Refs:` 或 `Closes:` 尾注。commit-msg hook 通过 commitizen 检查格式，格式错误会拒绝提交，由提交者修正。
- **合入队列。** `main` 使用 GitHub 的合入队列。每个 PR 在合入前通过 `merge_group` 对照最新的 `main` 再验证。`ci.yml` 监听 `merge_group` 事件。
- **一个 PR 聚焦一个目标。** 分支含有独立的改动时，例如重构和新功能，先拆分再开 PR，或使用堆叠 PR。默认采用 rebase 合入，让历史由各自可理解的提交组成。
- **禁止使用 `--no-verify` 绕过提交或推送 hooks。** Hook 失败时修复根因。唯一列明的例外是 pre-push 的 rebase 在确认无误的基线上受阻时，`./scripts/opsx-flow.sh push` 对合成的 squash 提交执行强制推送。此路径仍须按上文取得用户明确授权。
- **定期精简 `AGENTS.md`。** 每行规则都会进入每个会话的上下文。由 Python、Ruff、mypy 或 Git 默认行为覆盖的规则，以及已不再必要的规则，都应删除。每次执行 `/opsx-archive` 时复查。

### 编码规则

Ruff（E/F/I/N/W/UP/B/SIM）和 mypy 自动执行的规则见 `pyproject.toml`。本项目还有以下约定。

- 模块、公开类和公开方法都要写英文 docstring。用途一目了然的私有成员（以 `_` 开头）可以省略。
- 只给不易理解的逻辑写行内注释，说明为什么这样写。
- 安装任何新增 Python 包时，都要将其声明在 `pyproject.toml` 中的正确可选依赖组。代码只使用已声明的依赖。
- 新的运行时扩展点（ABC 或 Protocol）须有第二个实现或具名消费者，并在 PR 或用户明确要求的提案（`runtime-pluggability` spec）中注明。

### 命名

| 类别 | 约定 | 示例 |
|---|---|---|
| SQLAlchemy 模型 | `XxxModel` | `AgentModel` |
| Pydantic 模式 | `XxxCreateSchema` / `XxxUpdateSchema` / `XxxReadSchema` | `AgentCreateSchema` |
| **跨层边界端口** | `XxxPort`，仅用于运行时与领域之间的六边形架构边界 | `RuntimePort`、`AgentExecutionPort` |
| **运行时内部扩展点** | 普通名词 + `abc.ABC`，避免添加 `Port`、`Base`、`ABC`、`Abstract` 或 `I` 作为名称标记 | `Worker`、`EventStore`、`RetryStrategy` |
| **测试替身** | `StubXxx` | `StubRuntimePort` |
| **默认实现** | `HecateXxxAdapter`（生产环境的 `RuntimePort` 由 `core/composition/runtime_port_adapter.py` 中的 `_ProductionRuntimePort` 实现） | — |
| **供应商实现** | `<Vendor>XxxAdapter` | `EmailNotificationAdapter` |

### 语言和文档

- Python、TypeScript 等代码产物统一使用英文，包括 docstring、注释和注解。`openspec/changes/` 下的 OpenSpec 变更材料默认使用中文叙述，业务术语、标识符、路径、环境变量和技术名称保留英文。
- `README.md` 和 `docs/**` 中不要用具体数字或日期作为描述性标记。完整规则和例外见 [`docs/design/writing-style.md`](docs/design/writing-style.md)。

## 测试

### 开发中的快速测试（可选，推荐）

开发过程中可使用 `pytest --testmon` 获取快速反馈。它已包含在 `dev` 可选依赖中，会记录各测试涉及的源文件，后续只重跑受改动影响的测试。首次运行会执行完整套件，后续通常只需数秒。

```bash
# 每个 checkout 首次运行时生成 .testmondata
pytest --testmon

# 修改 src/hecate/runtime/compaction.py 后运行
pytest --testmon                 # 只运行涉及该文件的测试
pytest --testmon tests/test_runtime/test_compaction.py   # 进一步缩小范围
```

`--testmon` 用于加快本地开发反馈，属于可选检查。完整套件由 CI 运行。

测试约定见 [`tests/AGENTS.md`](tests/AGENTS.md)，包括 fixture、内存 SQLite、stub 类及禁止使用 factory 的规则。

## 推送前核对与禁止事项

- 推送前确保所有必需检查达到 **0 错误**。本地 pytest 只覆盖本次改动影响的测试，完整套件由 CI 把关。
- 禁止提交 PDF 或大型二进制文件。
- 禁止使用 `as any`、`@ts-ignore` 或等效的类型抑制。
- 调查测试失败的原因后，再判断是否为既有问题。
- `/opsx-apply` 由主智能体直接实施。完整的设计、规范和任务上下文容易使后台智能体超时，因此禁止委派。
