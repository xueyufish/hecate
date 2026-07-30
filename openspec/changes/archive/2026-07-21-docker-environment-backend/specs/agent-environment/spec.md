## ADDED Requirements — 新增需求

### 需求：在 AgentEnvironment 中执行 Shell 命令

`AgentEnvironment` ABC 应提供一个 `exec_shell(command, *, cwd, timeout) -> ExecResult` 方法，用于在环境内执行 Shell 命令。`ExecResult` 应包含 `exit_code: int`、`stdout: bytes` 和 `stderr: bytes`。所有实现（`LocalEnvironment`、`DockerEnvironment`）必须实现此方法。

#### 场景：LocalEnvironment exec_shell 在主机上运行
- **当** 在 `LocalEnvironment` 上调用 `exec_shell(["echo", "hello"])` 时
- **则** 命令通过 `asyncio.create_subprocess_exec` 在主机上运行
- **且** 返回的 `ExecResult` 具有 `exit_code=0`、`stdout=b"hello\n"`

#### 场景：带工作目录的 exec_shell
- **当** 调用 `exec_shell(["ls"], cwd="files/")` 时
- **则** 命令以指定目录作为其工作目录执行

#### 场景：带超时的 exec_shell
- **当** 调用 `exec_shell(["sleep", "10"], timeout=1.0)` 时
- **则** 命令在 1 秒后被终止
- **且** 返回的 `ExecResult` 具有 `exit_code=-1` 且 `stderr` 包含超时消息

#### 场景：exec_shell 分别捕获 stderr
- **当** 命令写入 stderr 时
- **则** `ExecResult.stderr` 包含 stderr 输出，`ExecResult.stdout` 仅包含 stdout

### 需求：DockerEnvironment 容器后端

系统应提供 `DockerEnvironment` 实现 `AgentEnvironment`，将 Agent 的文件和进程隔离在 Docker 容器内。每个 Agent 应有自己的容器，带有命名卷（`agent-{agent_id}`）挂载到 `/env`，包含子目录 `sessions/`、`files/`、`memory/`、`skills/`。

#### 场景：DockerEnvironment 在首次访问时创建容器
- **当** 使用 `AGENT_ENV_BACKEND=docker` 调用 `EnvironmentManager.get_or_create(agent_id)` 时
- **则** 创建 Docker 容器，使用镜像 `DOCKER_AGENT_IMAGE`，卷 `agent-{agent_id}` 挂载到 `/env`，运行时 `DOCKER_RUNTIME`
- **且** 容器的 `/env` 目录具有 `sessions/`、`files/`、`memory/`、`skills/` 子目录

#### 场景：DockerEnvironment 复用热容器
- **当** `agent_id` 的容器存在于热池中时
- **则** `get_or_create(agent_id)` 复用该容器而非创建新容器
- **且** TTL 计时器被重置

#### 场景：DockerEnvironment 文件写入和读取
- **当** 在 `DockerEnvironment` 上调用 `write_file("files/report.txt", b"hello")` 时
- **则** 文件被写入容器内的 `/env/files/report.txt`
- **且** `read_file("files/report.txt")` 返回 `b"hello"`

#### 场景：DockerEnvironment exec_shell 在容器内运行
- **当** 在 `DockerEnvironment` 上调用 `exec_shell(["pip", "install", "pandas"])` 时
- **则** 命令通过 `docker exec` 在容器内运行
- **且** 返回的 `ExecResult` 反映容器内命令的退出代码和输出

#### 场景：DockerEnvironment 容器隔离
- **当** Agent A 的容器正在运行时
- **则** Agent A 无法访问 Agent B 的卷或文件
- **且** Agent A 的进程通过容器命名空间与 Agent B 的进程隔离

#### 场景：带 gVisor 运行时的 DockerEnvironment
- **当** 配置了 `DOCKER_RUNTIME=runsc` 时
- **则** 容器以 `runsc` 运行时创建
- **且** 容器内的系统调用被 gVisor 的用户空间内核拦截

### 需求：EnvironmentManager 后端选择

`EnvironmentManager` 应支持通过 `AGENT_ENV_BACKEND` 配置设置选择环境后端。有效值为 `"local"`（默认）和 `"docker"`。

#### 场景：默认后端为 local
- **当** 未设置 `AGENT_ENV_BACKEND` 时
- **则** `EnvironmentManager` 创建 `LocalEnvironment` 实例（现有行为）

#### 场景：选择 Docker 后端
- **当** 设置 `AGENT_ENV_BACKEND=docker` 时
- **则** `EnvironmentManager` 创建 `DockerEnvironment` 实例

#### 场景：在启动时拒绝无效后端
- **当** `AGENT_ENV_BACKEND` 设置为未识别的值（例如 `"e2b"`）时
- **则** 系统在 `EnvironmentManager` 初始化时抛出 `ValueError`

### 需求：用于容器复用的热池

`EnvironmentManager` 应维护一个空闲 Docker 容器的热池，以减少冷启动延迟。当容器关闭时，它移动到热池而不是被销毁。热池具有可配置的最大大小和空闲超时。

#### 场景：关闭时容器移动到热池
- **当** 调用 `close(agent_id)` 时
- **则** 容器被停止但不被销毁
- **且** 它被放入热池以备将来复用

#### 场景：重新访问时的热池复用
- **当** 在关闭后调用 `get_or_create(agent_id)` 时
- **且** 容器仍在热池中
- **则** 容器被重启并复用

#### 场景：热池满时驱逐
- **当** 热池达到最大容量时
- **且** 需要驱逐新容器
- **则** 最旧的空闲容器被销毁（其卷持久存在）

#### 场景：热池空闲超时
- **当** 容器在热池中空闲超过配置的超时时
- **则** 容器在下一次清理时被销毁（其卷持久存在）

## MODIFIED Requirements — 修改的需求

### 需求：AgentEnvironment 抽象

系统应提供一个 `AgentEnvironment` ABC，表示 Agent 的持久执行环境。每个环境作用于单个 Agent，包含会话、文件、内存和技能的子目录。ABC 应包含 `exec_shell(command, *, cwd, timeout) -> ExecResult` 方法，用于在环境内执行 Shell 命令。

#### 场景：环境包含必需的子目录
- **当** 创建 Agent 环境时
- **则** 环境包含 `sessions/`、`files/`、`memory/` 和 `skills/` 子目录

#### 场景：环境作用于 Agent
- **当** 访问 Agent A 的环境时
- **则** Agent A 无法访问 Agent B 的环境文件

#### 场景：exec_shell 在所有实现上可用
- **当** 使用任何 `AgentEnvironment` 实现时
- **则** `exec_shell(command)` 可用并返回 `ExecResult`

### 需求：LocalEnvironment 文件系统实现

系统应提供一个 `LocalEnvironment` 实现，将 Agent 数据存储在本地文件系统的 `{WORKSPACE_ROOT}/{agent_id}/`。`LocalEnvironment` 应通过 `asyncio.create_subprocess_exec` 在主机上运行命令来实现 `exec_shell`。

#### 场景：文件写入和读取
- **当** 文件写入环境的 `files/report.txt` 时
- **则** 该文件可以以相同内容被读取回来

#### 场景：文件列表
- **当** 文件存在于 `files/` 子目录中时
- **则** `list_files("files/")` 返回文件列表及元数据

#### 场景：文件删除
- **当** 文件从环境中被删除时
- **则** 后续的 `exists()` 返回 False

#### 场景：exec_shell 在主机上运行
- **当** 调用 `exec_shell(["whoami"])` 时
- **则** 命令在主机上运行并返回主机用户

### 需求：EnvironmentManager 生命周期

系统应提供一个 `EnvironmentManager`，使用懒创建、基于 TTL 的驱逐和可配置的后端选择（`AGENT_ENV_BACKEND`）来管理环境生命周期。当 `AGENT_ENV_BACKEND=docker` 时，管理器应维护一个空闲容器的热池以供复用。

#### 场景：首次使用懒创建
- **当** 为没有现有环境的 Agent 调用 `get_environment(agent_id)` 时
- **则** 创建并返回一个新环境

#### 场景：缓存环境复用
- **当** 为同一 Agent 调用两次 `get_environment(agent_id)` 时
- **则** 返回同一环境实例（缓存）

#### 场景：TTL 驱逐
- **当** 环境空闲时间超过配置的 TTL 时
- **则** 下次访问时环境被关闭并从缓存中移除

#### 场景：交互时 TTL 重置
- **当** 对环境执行文件操作时
- **则** 环境的 TTL 计时器被重置

#### 场景：关闭所有环境
- **当** 调用 `close_all()`（例如应用关闭时）
- **则** 所有缓存的环境被关闭

#### 场景：通过配置选择后端
- **当** 设置 `AGENT_ENV_BACKEND=docker` 时
- **则** 管理器创建 `DockerEnvironment` 实例而非 `LocalEnvironment`
