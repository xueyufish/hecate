## Why — 为什么

Hecate 的 `AgentEnvironment` ABC（1.3.15）仅附带 `LocalEnvironment` — 一种基于文件系统的实现，不提供 Agent 之间的进程级隔离。在运行用户提供代码的多租户部署中，容器逃逸或路径遍历可能将一个租户的文件暴露给另一个。每个可比较的平台（AgentScope、DeerFlow、Bedrock AgentCore、Google Gemini）都提供容器化或 microVM 隔离的执行环境。此变更添加了 `DockerEnvironment` 后端，以便 Agent 可以在具有隔离文件系统、进程和可选的 gVisor 加固的 Docker 容器内运行 — 全部自托管，无外部云依赖。

## What Changes — 变更内容

- **向 `AgentEnvironment` ABC 添加 `exec_shell()`** — 现有的 ABC 只有文件 I/O 方法（read/write/list/delete/exists/ensure_dirs）。容器后端需要 Shell 执行来操作（通过 `docker exec` 进行文件操作、包安装、工具设置）。添加 `exec_shell(command) -> ExecResult` 使 Hecate 与 AgentScope 的 `BackendBase` 和 DeerFlow 的 `Sandbox` 接口达到同等水平。
- **添加 `ExecResult` 数据类** — `exec_shell` 的结构化返回类型：`exit_code`、`stdout`、`stderr`。
- **实现 `DockerEnvironment`** — 新的 `AgentEnvironment` 实现，后端由 Docker 容器通过 `aiodocker` 支持。每个 Agent 拥有自己的长运行容器，带有用于持久文件系统的命名卷（sessions/、files/、memory/、skills/）。文件操作使用 Docker 的 `exec` / `get_archive` / `put_archive` API。容器生命周期通过热池复用管理。
- **实现 `LocalEnvironment.exec_shell()`** — 现有的 `LocalEnvironment` 通过 `asyncio.create_subprocess_exec` 在主机上获得 `exec_shell` 实现。
- **重构 `EnvironmentManager` 以支持后端选择** — 当前硬编码为 `LocalEnvironment`。添加配置驱动的后端选择（`AGENT_ENV_BACKEND=local|docker`）和用于容器复用的热池（参考现有的 `SandboxPool` 9.4d 模式）。
- **添加 `aiodocker` 依赖** — 用于 Python 的异步 Docker API 客户端。添加到 `pyproject.toml` 的 `[tools]` 可选依赖组。
- **添加 Docker 配置** — 新设置：`AGENT_ENV_BACKEND`、`DOCKER_AGENT_IMAGE`、`DOCKER_RUNTIME`（runc/runsc）、`DOCKER_NETWORK_MODE`。
- **可选的 gVisor 支持** — 当 `DOCKER_RUNTIME=runsc` 时，容器使用 gVisor 用户空间内核实现更强的隔离。需要主机上安装 `runsc`。

## Capabilities — 能力

### 新能力

_(无 — 所有能力引用现有规范)_

### 修改的能力

- `agent-environment`：向 `AgentEnvironment` ABC 添加 `exec_shell` 抽象方法。在 `LocalEnvironment` 旁边添加 `DockerEnvironment` 作为第二个后端实现。添加 `ExecResult` 返回类型。重构 `EnvironmentManager` 以支持后端选择和热池。

## Impact — 影响

- **修改的文件**：
  - `src/hecate/services/environment/environment.py` — 向 ABC + `LocalEnvironment` 添加 `exec_shell`；添加 `ExecResult` 数据类；添加 `DockerEnvironment` 类
  - `src/hecate/services/environment/manager.py` — 为后端选择重构 `get_or_create()`；添加热池逻辑
  - `src/hecate/core/config.py` — 新设置：`AGENT_ENV_BACKEND`、`DOCKER_AGENT_IMAGE`、`DOCKER_RUNTIME`、`DOCKER_NETWORK_MODE`
  - `pyproject.toml` — 将 `aiodocker` 添加到 `[tools]` 组
- **新文件**：
  - `src/hecate/services/environment/docker_environment.py` — `DockerEnvironment` 实现（如果大小允许也可与 `environment.py` 放在一起）
  - `tests/test_services/test_environment/test_docker_environment.py` — `DockerEnvironment` 的单元测试
  - `tests/test_services/test_environment/test_exec_shell.py` — 跨后端 `exec_shell` 的测试
- **无破坏性变更**：`AGENT_ENV_BACKEND` 默认为 `"local"`。现有的 `LocalEnvironment` 调用者看不到行为变化。`exec_shell` 是 ABC 的新增功能。
- **新依赖**：`aiodocker`（异步 Docker 客户端）。仅在 `AGENT_ENV_BACKEND=docker` 时导入。
- **基础设施要求**：使用 `docker` 后端时 Docker 守护进程必须可用。gVisor（`runsc`）可选用于更强的隔离。
