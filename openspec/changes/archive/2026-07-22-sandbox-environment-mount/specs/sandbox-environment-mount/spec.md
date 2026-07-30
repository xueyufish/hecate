## ADDED Requirements — 新增需求

### 需求：SandboxConfig 支持卷挂载

`SandboxConfig` 数据类应包含一个 `volumes` 字段，类型为 `dict[str, str]`，其中键是主机路径或 Docker 卷名，值是容器挂载路径。该字段应默认为空字典，保持向后兼容。

#### 场景：不带卷的 SandboxConfig
- **当** 构造 `SandboxConfig()` 而未指定 `volumes` 时
- **则** `volumes` 应为空字典 `{}`
- **且** 沙箱容器应不带 `--volume` 参数创建

#### 场景：带环境卷的 SandboxConfig
- **当** 构造 `SandboxConfig(volumes={"agent-abc123": "/mnt/env"})` 时
- **则** `volumes` 应包含 `{"agent-abc123": "/mnt/env"}`
- **且** 沙箱容器应使用 `--volume agent-abc123:/mnt/env` 创建

### 需求：SandboxExecutor 在配置时挂载卷

`SandboxExecutor._create_container()` 应为 `SandboxConfig.volumes` 中的每个条目追加 `--volume {host}:{container}` 参数到 `docker run` 命令。

#### 场景：单个卷挂载
- **当** `SandboxConfig(volumes={"/workspace/agent-1": "/mnt/env"})` 传递给 `SandboxExecutor`
- **且** 调用 `execute("run_code", {"code": "print('hi')"})`
- **则** `docker run` 命令应包含 `--volume /workspace/agent-1:/mnt/env`
- **且** 容器应在 `/mnt/env` 下可以访问 Agent 的文件

#### 场景：多个卷挂载
- **当** 传递 `SandboxConfig(volumes={"/data": "/mnt/data", "/config": "/mnt/config"})` 时
- **则** `docker run` 命令应包含 `--volume /data:/mnt/data` 和 `--volume /config:/mnt/config`

#### 场景：空 volume 不产生挂载参数
- **当** 传递 `SandboxConfig(volumes={})` 时
- **则** `docker run` 命令不应包含任何 `--volume` 参数

### 需求：环境桥接从 AgentEnvironment 解析卷挂载

一个 `resolve_environment_volumes()` 函数应接受一个 `AgentEnvironment`（或 None）并返回 `dict[str, str]` 映射用于 SandboxConfig 卷。该函数应不同地处理 DockerEnvironment 和 LocalEnvironment。

#### 场景：DockerEnvironment 解析为命名卷
- **当** 使用 `DockerEnvironment(agent_id="agent-abc")` 调用 `resolve_environment_volumes()` 时
- **则** 返回值应为 `{"agent-abc": "/mnt/env"}`
- **且** 键应为环境容器使用的 Docker 卷名

#### 场景：LocalEnvironment 解析为主机 bind mount
- **当** 使用 `LocalEnvironment(root="/workspace/agent-1")` 调用 `resolve_environment_volumes()` 时
- **则** 返回值应为 `{"/workspace/agent-1": "/mnt/env"}`
- **且** 键应为绝对主机路径

#### 场景：无环境返回空映射
- **当** 调用 `resolve_environment_volumes(None)` 时
- **则** 返回值应为 `{}`

### 需求：沙箱挂载模式可配置

`SANDBOX_MOUNT_MODE` 配置设置应控制 Docker 挂载权限后缀。有效值为 `"rw"`（读写，默认）和 `"ro"`（只读）。

#### 场景：默认挂载模式为 rw
- **当** 未设置 `SANDBOX_MOUNT_MODE` 时
- **则** 卷挂载应包含 `:rw` 后缀（例如 `--volume agent-x:/mnt/env:rw`）

#### 场景：挂载模式 ro
- **当** 设置 `SANDBOX_MOUNT_MODE=ro` 时
- **则** 卷挂载应包含 `:ro` 后缀（例如 `--volume agent-x:/mnt/env:ro`）

### 需求：SandboxPool 传播卷配置

`SandboxPool` 应在通过池创建新容器时传播执行器的 `SandboxConfig.volumes`。

#### 场景：池使用执行器配置的卷
- **当** 构造 `SandboxPool(executor=SandboxExecutor(config=SandboxConfig(volumes={"/data": "/mnt/env"})))` 时
- **且** 池创建新容器
- **则** 容器应在 `/mnt/env` 下挂载 `/data`

### 需求：BuiltinTools 将环境挂载传递给 SandboxExecutor

`BuiltinTools._execute_code()` 应在 AgentEnvironment 可用时解析 Agent 的环境卷挂载并将其传递给 SandboxExecutor。

#### 场景：execute_code 时环境可用
- **当** 调用 `_execute_code()` 且 `AgentEnvironment` 可用时
- **则** 应调用 `resolve_environment_volumes(env)` 获取卷挂载
- **且** 将 `SandboxConfig(volumes=volume_mounts)` 传递给 SandboxExecutor
- **且** 沙箱容器应在 `/mnt/env` 下挂载环境

#### 场景：execute_code 时无环境
- **当** 调用 `_execute_code()` 且没有 `AgentEnvironment` 可用时
- **则** 应将 `SandboxConfig(volumes={})` 传递给 SandboxExecutor
- **且** 沙箱容器应没有卷挂载（向后兼容）

#### 场景：Agent 通过环境读取沙箱输出
- **当** 沙箱将 `output.txt` 写入 `/mnt/env/files/output.txt`
- **且** 沙箱容器完成时
- **则** Agent 应能够通过其环境上的 `read_file("files/output.txt")` 读取该文件
