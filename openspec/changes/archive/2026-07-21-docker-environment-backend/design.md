## Context — 背景

Hecate 的 `AgentEnvironment` ABC（在 1.3.15 中发布）为 Agent 的持久执行上下文提供了统一抽象 — 文件、内存、会话、技能。唯一的实现 `LocalEnvironment` 将数据存储在主机文件系统的 `{WORKSPACE_ROOT}/{agent_id}/`。这适用于单租户开发，但不提供进程隔离：所有 Agent 共享同一 OS，容器逃逸或恶意工具可能跨越 Agent 边界。

对 7 个平台（AgentScope、DeerFlow、Bedrock AgentCore、Google Gemini、Huawei AgentArts、Claude Code、Palantir/Salesforce）的行业研究显示了两种隔离理念：

1. **容器/VM 隔离**（AgentScope、DeerFlow、Bedrock、Google、Huawei）— 每个 Agent 或会话获得自己的容器或 microVM，具有隔离的文件系统和进程。
2. **平台级治理**（Palantir、Salesforce）— Agent 在共享 K8s 上运行，采用数据级安全控制。

Hecate 同时支持自托管私有部署和 SaaS，Agent 可以执行用户提供的代码（Python、shell）。这使得 Hecate 明确属于容器/VM 隔离阵营。然而，构建自定义 microVM 平台（如 Bedrock 的 Firecracker）超出了范围 — 工程成本太高。经过 AgentScope 和 DeerFlow 验证的务实路径是使用 `aiodocker` 异步库自建 Docker 容器后端。

**研究基础**：
- **AgentScope**：`DockerBackend` 使用 `aiodocker`（`exec`、`get_archive`、`put_archive`）+ `BackendBase` ABC（`exec_shell`、`read_file`、`write_file`）。`SandboxedWorkspaceBase` 模板方法用于生命周期。`WorkspaceManager` 带基于 TTL 的缓存。
- **DeerFlow**：`SandboxProvider` 抽象，带 `LocalSandboxProvider` / `AioSandboxProvider`（Docker）。带有 LRU 驱逐和 `keep_alive_seconds` 的热池。`Sandbox` 接口：`execute_command`、`read_file`、`write_file`、`list_dir`。用于线程隔离的虚拟路径映射。
- **Docker Sandbox (sbx)**：Docker Inc. 的官方 microVM 沙箱产品。没有可比较的平台使用它 — 它是一个面向开发者的工具，不是平台后端。常规 Docker 容器（命名空间隔离）是 Agent 平台的行业标准。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 向 `AgentEnvironment` ABC 添加 `exec_shell()` 用于在环境内执行 Shell 命令
- 使用 `aiodocker` 实现 `DockerEnvironment`，每个 Agent 使用持久卷
- 重构 `EnvironmentManager` 以支持通过配置选择后端（`local` / `docker`）
- 可选支持 gVisor 运行时（`runsc`）以实现更强的隔离
- 用于容器复用的热池以减少冷启动延迟
- 对现有 `LocalEnvironment` 调用者零破坏性变更

**非目标：**
- E2B 云沙箱后端（与私有部署定位冲突 — 数据会离开机房）
- AerolVM / ForgeVM 集成（所有主流平台自建；外部依赖增加运维负担）
- MCP 网关在容器内（推迟到后续；当前 MCP 服务器在 Hecate 主进程中运行）
- Firecracker / Kata Containers 后端（作为 6.40 和 6.32a 跟踪 — 在此基础之上构建的未来特性）
- K8s 原生后端（作为未来部署特性跟踪）
- 每会话隔离（环境是每 Agent 的，匹配现有的 1.3.15 设计；每会话状态是 1.3.16 的领域）

## Decisions — 决策

### 决策 1：使用 aiodocker 自建，而非外部服务

**选择**：使用 `aiodocker` 库直接管理 Docker 容器。

**理由**：每个主流平台都自建沙箱基础设施。AgentScope 和 DeerFlow 都直接使用 Docker 守护进程 API。外部服务（E2B、AerolVM、ForgeVM）要么是纯云端的（数据主权冲突），要么增加了一个需要部署的额外服务（运维负担）。使用 `aiodocker` 自建提供了完全控制，零外部依赖，并且已由 AgentScope 验证。

**考虑的替代方案**：
- 通过子进程使用 `docker` CLI（现有的 `SandboxExecutor` 9.4c 方法）：拒绝 — 每次操作的子进程开销，更难管理流式输出，不支持异步。
- Docker SDK（`docker-py`）：拒绝 — 仅同步，需要线程池包装器。
- AerolVM/ForgeVM：拒绝 — 所有主流平台自建；增加外部服务依赖。

### 决策 2：使用命名卷的长运行容器，而非临时容器

**选择**：每个 Agent 获得一个带有命名卷（`agent-{agent_id}`）挂载到 `/env` 的长运行 Docker 容器。

**理由**：
- 匹配 `AgentEnvironment` 的持久化语义（文件跨会话存活）
- Bedrock 使用此模型（会话范围的 microVM 带持久存储）
- DeerFlow 使用 `keep_alive_seconds: 3600` 实现容器复用
- AgentScope 的 `WorkspaceManager` 使用 TTL 驱逐保持容器存活
- 替代方案（每次请求临时）需要每次操作进行卷快照/恢复 — 太慢

**容器生命周期**：
```
get_or_create(agent_id)
  → 检查热池中是否有空闲容器
  → 找到：复用（重置 TTL 计时器）
  → 未找到：docker.containers.create() + start()

close(agent_id)
  → 停止容器，移到热池（不销毁）
  → 热池满：销毁容器（卷持久化）

close_all()
  → 销毁所有容器（卷保留以供将来复用）
```

### 决策 3：通过 Docker exec 和 tar 归档进行文件 I/O

**选择**：文件操作使用 Docker 的容器 API：
- `read_file`：`container.get_archive(path)` → 从 tar 提取 → 返回字节
- `write_file`：在内存中创建 tar → `container.put_archive(parent_dir, tar_bytes)`
- `exec_shell`：`container.exec(cmd)` → 流式 stdout/stderr → 返回 `ExecResult`
- `list_files`、`delete_file`、`exists`：通过 `exec_shell` 实现（例如 `ls -la`、`rm`、`test -e`）

**理由**：这正是 AgentScope 的 `DockerBackend` 模式。三个原语（`exec_shell`、`read_file`、`write_file`）已足够 — 所有其他文件操作从 `exec_shell` 组合而来。这使实现保持小巧且经过测试。

### 决策 4：向 AgentEnvironment ABC 添加 exec_shell

**选择**：用 `exec_shell(command, *, cwd, timeout) -> ExecResult` 扩展现有 ABC。

**理由**：
- 容器后端根本上需要 Shell 执行（安装包、运行设置脚本、操作文件）
- AgentScope 的 `BackendBase` 证明 3 个原语（`exec_shell` + `read_file` + `write_file`）是最小干净的接口
- `LocalEnvironment` 可以通过 `asyncio.create_subprocess_exec` 简单实现
- 未来后端（gVisor、Kata、Firecracker）也都需要它

**替代方案**：保持 ABC 仅文件，将 `exec_shell` 放在单独的 `SandboxBackend` ABC 上。拒绝 — 为同一概念设两个 ABC 令人困惑；DeerFlow 和 AgentScope 都将它们统一。

### 决策 5：通过配置而非 DI 参数进行后端选择

**选择**：`EnvironmentManager` 读取 `settings.AGENT_ENV_BACKEND`（`"local"` 或 `"docker"`）来选择后端工厂。

**理由**：匹配 Hecate 现有的配置驱动模式（`settings.AGENT_ENV_TTL`、`settings.WORKSPACE_ROOT`）。保持 `EnvironmentManager` 构造函数签名稳定。用户通过更改 `.env` 而非代码来切换后端。

### 决策 6：受现有 SandboxPool (9.4d) 启发的热池

**选择**：在 `EnvironmentManager` 内部实现轻量级热池 — 关闭的容器进入空闲列表，具有可配置的最大大小和空闲超时。同一 Agent 的下一次 `get_or_create` 调用时复用。

**理由**：避免了每次会话约 200ms 的容器冷启动。由 DeerFlow（`keep_alive_seconds`）和 AgentScope（`WorkspaceManager` TTL）验证的模式。现有的 `SandboxPool`（9.4d）验证了池概念，但专为临时工具执行设计，而非持久环境 — 不同的生命周期，因此独立的池更清晰。

## Risks / Trade-offs — 风险 / 权衡

- **[Docker 守护进程依赖]** — `docker` 后端需要主机上的 Docker 守护进程。缓解：`local` 后端保持默认；Docker 通过 `AGENT_ENV_BACKEND=docker` 可选加入。CI/测试套件使用 `local` 后端。
- **[仅单实例]** — 与 `LocalEnvironment` 一样，Docker 热池是进程内的。多实例 Hecate 部署各自管理自己的容器。缓解：容器除了卷之外是无状态的；任何实例都可以拾取卷。完整的多实例协调是未来的 K8s 特性。
- **[容器冷启动约 200ms]** — 容器的首次创建需要约 200ms。缓解：热池保持空闲容器存活以供复用。后续的 `get_or_create` 调用是即时的。
- **[aiodocker 版本兼容性]** — `aiodocker` API 可能在版本之间变化。缓解：在 `pyproject.toml` 中固定版本；将 Docker API 调用包装在 `DockerEnvironment` 方法中，使适配器变更局部化。
- **[gVisor 可用性]** — `runsc` 运行时并非在所有主机上都可用（需要 Linux 4.x+、特定的内核配置）。缓解：`DOCKER_RUNTIME` 默认为 `runc`；gVisor 是显式可选加入的。在配置注释中记录先决条件。
- **[ABC 破坏风险]** — 向 `AgentEnvironment` ABC 添加 `exec_shell` 意味着所有实现必须提供它。缓解：只有两个实现存在（`LocalEnvironment` + 新的 `DockerEnvironment`）；两者都在此变更中发布。第三方实现（如果有）需要更新，但 ABC 是 Hecate 内部的。
