## Why — 为什么

SandboxExecutor（9.4c）和 AgentEnvironment（1.3.15）是两个基于 Docker 的子系统，目前完全隔离运行。SandboxExecutor 启动没有持久存储的临时容器，而 DockerEnvironment 维护带有命名卷的长运行容器。这意味着 Agent 无法将代码写入其环境然后在隔离的沙箱中执行该代码 — 两个系统是断开的。

这一差距阻碍了核心"编程 Agent"工作流：将代码写入环境 → 在沙箱中执行 → 读取结果。Amazon Bedrock AgentCore 通过 `/mnt/workspace` 共享卷解决这个问题；deer-flow 使用 `/mnt/user-data/workspace/` bind mount；private-gpt 使用 `SessionMountDef` 和共享卷。Hecate 需要相同的能力。

## What Changes — 变更内容

- **修改**：`SandboxConfig` 新增 `volumes` 字段，用于指定要附加到沙箱容器的 Docker 卷/bind 挂载。
- **修改**：`SandboxExecutor._create_container()` 从 `SandboxConfig.volumes` 追加 `--volume` 参数到 `docker run` 命令。
- **修改**：`SandboxPool` 在创建容器时传播其执行器配置中的 `volumes`。
- **新增**：`services/sandbox/environment_bridge.py` 中的 `SandboxEnvironmentConfig` 构建器，为 DockerEnvironment（共享卷）和 LocalEnvironment（bind mount）场景构造卷挂载配置。
- **修改**：`ToolWorker` / `BuiltinTools._execute_code()` 在 AgentEnvironment 可用时将 Agent 的环境挂载配置传递给 SandboxExecutor。
- **新增**：配置设置 `SANDBOX_MOUNT_MODE`（默认 `"rw"`）— 控制沙箱内环境挂载的读/写权限。

## Capabilities — 能力

### 新能力
- `sandbox-environment-mount`：将 AgentEnvironment 挂载到 SandboxExecutor 容器的能力，涵盖卷解析（Docker 卷 vs bind mount）、挂载路径（`/mnt/env`）、权限和生命周期协调。

### 修改的能力
- `sandbox-executor`：SandboxConfig 新增 `volumes` 字典；`_create_container()` 挂载它们。对现有沙箱语义无行为变化 — 纯增量添加。

## Impact — 影响

- **代码**：
  - `src/hecate/services/sandbox/executor.py`（修改）— `SandboxConfig` + `_create_container()`
  - `src/hecate/services/sandbox/pool.py`（修改）— 传播 volumes
  - `src/hecate/services/sandbox/environment_bridge.py`（新增）— 卷挂载构建器
  - `src/hecate/services/tool/builtin.py`（修改）— 将环境挂载传递给沙箱
  - `src/hecate/core/config.py`（修改）— 新设置
- **API**：无外部 API 变更。内部 `SandboxConfig` 新增可选 `volumes` 字段。
- **依赖**：无新的外部依赖。使用现有的 Docker `--volume` 标志。
- **存储**：沙箱容器在 `/mnt/env` 下对 Agent 的环境卷获得读写访问权限。
