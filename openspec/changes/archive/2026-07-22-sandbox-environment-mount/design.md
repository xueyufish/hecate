## Context — 背景

Hecate 有两个基于 Docker 的子系统，目前独立运行：

**SandboxExecutor**（`services/sandbox/executor.py`）：通过 `execute_code` 工具创建用于代码执行的临时 Docker 容器。容器使用 `docker run --rm` 创建，每次执行后销毁。没有持久存储，没有卷挂载，不了解 Agent 的环境。

**AgentEnvironment**（`services/environment/`）：长运行容器（DockerEnvironment）或主机目录（LocalEnvironment），每个 Agent 在 `/env` 下有持久存储，包含 `sessions/`、`files/`、`memory/`、`skills/`。Agent 通过 `write_file()` 写入文件，通过 `exec_shell()` 执行命令。

差距：Agent 无法将 `solution.py` 写入其环境，然后在隔离的沙箱中执行 `python solution.py`。两个系统没有连接。

**行业模式**（来自研究）：
- Amazon Bedrock AgentCore：`/mnt/workspace` 共享卷，14 天 TTL
- deer-flow：每个线程的 `/mnt/user-data/workspace/` bind mount
- private-gpt：`SessionMountDef`，使用规范路径（`/home/agent/workspace/`）
- OpenHands：每个会话的 `/workspace` bind mount
- Sage（bwrap）：`--ro-bind sandbox_agent_workspace`

所有方案都使用 bind mount 或共享卷将 Agent 的持久文件连接到沙箱的执行上下文。

**约束条件：**
- SandboxExecutor 在 `services/`，AgentEnvironment 在 `services/` — 同一层，没有分层违规。
- 引擎层（`engine/workers/tool_worker.py`）调用创建 SandboxExecutor 的工具服务 — 桥接必须在服务层而非引擎层进行。
- DockerEnvironment 使用命名 Docker 卷（`agent-{agent_id}`）；LocalEnvironment 使用主机目录（`{WORKSPACE_ROOT}/{agent_id}/`）。桥接必须同时处理两者。
- SandboxExecutor 使用 `docker run` CLI，而非 `aiodocker`。卷挂载必须表示为 `--volume` CLI 参数。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 允许 SandboxExecutor 将 Agent 的环境卷/目录挂载到沙箱容器内的 `/mnt/env`
- 同时支持 DockerEnvironment（共享 Docker 卷）和 LocalEnvironment（主机 bind mount）
- 默认 `rw` 挂载，以便沙箱可以将输出文件写回环境
- 每次执行可选加入卷挂载（并非所有沙箱调用都需要环境访问）
- 保持 SandboxConfig 向后兼容 — 没有卷的现有调用者不受影响

**非目标：**
- 将环境挂载到非 Docker 沙箱后端（bwrap、macOS sandbox-exec）— 未来工作
- 只读挂载 — 默认 `rw` 用于代码执行用例；`ro` 支持推迟
- 沙箱热池与环境集成 — 池管理的是容器，而非卷
- 更改 SandboxExecutor 的容器生命周期（仍然是临时 `--rm`）— 仅添加卷挂载

## Decisions — 决策

### 决策 1：向 SandboxConfig 添加 volumes 字典

**选择：** 向 `SandboxConfig` 添加 `volumes: dict[str, str]`，其中键是主机路径或卷名，值是容器挂载路径。

**理由：** 这是最小、Docker 原生的抽象。`--volume host_path:container_path` 是标准的 Docker 挂载语法。字典自然表达了映射关系。

**考虑的替代方案：**
- *单独的 `environment_volume` 字段*：灵活性较低；无法挂载多个卷。拒绝。
- *字符串列表 `["agent-x:/mnt/env"]`*：难以编程式组合。拒绝。
- *专用的 `MountSpec` 数据类*：对字典来说过度设计。暂时拒绝。

### 决策 2：通过 `environment_bridge.py` 模块桥接

**选择：** 创建 `services/sandbox/environment_bridge.py`，其中包含 `resolve_environment_volumes()` 函数，接受 `AgentEnvironment` 并返回 `dict[str, str]` 卷映射。

**理由：** 将 AgentEnvironment 解析为 Docker 卷挂载的逻辑在 DockerEnvironment（卷名）和 LocalEnvironment（主机路径）之间有所不同。专用模块封装了这一点，而不污染 SandboxExecutor 或 AgentEnvironment。

**解析逻辑：**
- DockerEnvironment → `{"agent-{agent_id}": "/mnt/env"}`（Docker 命名卷）
- LocalEnvironment → `{"{root_path}": "/mnt/env"}`（主机 bind mount）
- None → `{}`（不挂载）

### 决策 3：挂载路径为 `/mnt/env`

**选择：** 所有环境挂载都指向沙箱容器内的 `/mnt/env`。

**理由：**
- 与 Bedrock 的 `/mnt/workspace` 约定一致
- 不与沙箱镜像的工作目录冲突
- Agent/工具可以统一引用 `/mnt/env/files/solution.py`，无论使用哪种后端

### 决策 4：挂载模式默认为 rw

**选择：** `SANDBOX_MOUNT_MODE` 配置设置默认值为 `"rw"`。作为 `:rw` 后缀传递给 `--volume` 参数。

**理由：** 代码执行需要写入输出文件。Bedrock、private-gpt 和 deer-flow 都使用读写挂载。只读是例外，而非默认。

### 决策 5：桥接在工具执行层进行

**选择：** `BuiltinTools._execute_code()` 解析环境挂载并将其传递给 SandboxExecutor。

**理由：** 这是今天构造 `SandboxExecutor` 的地方。工具层已经可以访问环境（通过 WorkflowExecutionService）。无需引擎层更改。

**流程：**
```
ToolWorker.execute()
  → BuiltinTools._execute_code(args, environment=env)
    → volumes = resolve_environment_volumes(env)
    → SandboxExecutor(config=SandboxConfig(volumes=volumes))
      → _create_container() 添加 --volume 参数
```

### 决策 6：无需引擎层更改

**选择：** PregelRuntime、LLMWorker 和 ToolWorker 保持不变。桥接完全在服务层。

**理由：** 引擎零外部依赖（AGENTS.md）。SandboxExecutor 是一个服务。桥接必须位于 services/ 并由构造 SandboxExecutor 的服务层进行布线。

## Risks / Trade-offs — 风险 / 权衡

- **[Docker 卷名冲突]** 如果两个 Agent 共享卷名前缀，可能发生交叉污染。→ 缓解：卷名是 `agent-{agent_id}` 带 UUID — 冲突概率接近零。
- **[rw 挂载允许沙箱破坏环境]** 恶意或有 Bug 的沙箱执行可能修改 Agent 文件。→ 缓解：与 Bedrock 的设计一致；未来的工作可以添加 `ro` 模式或文件系统级限制。
- **[LocalEnvironment bind mount 仅在沙箱与主机同一节点时有效]** 在多主机部署中，主机路径在沙箱机器上可能不存在。→ 缓解：这是已知的限制；多主机部署必须使用 DockerEnvironment。
- **[SandboxExecutor 使用 `docker run` CLI，而非 aiodocker]** 卷参数必须格式化为 CLI 字符串。→ 缓解：在 `_create_container()` 中进行直接的字符串格式化。
- **[不存在要修改的沙箱 spec]** 提案将 `sandbox-executor` 列为修改的能力，但没有主 spec 存在。→ 缓解：仅创建 `sandbox-environment-mount` 新能力 spec；sandbox-executor 的更改仅是实现层面的。

## Migration Plan — 迁移计划

无需迁移。这纯粹是增量添加：
1. `SandboxConfig.volumes` 默认为 `{}` — 未指定时不挂载卷。
2. 现有的 `BuiltinTools._execute_code()` 调用者继续工作不变。
3. 环境挂载是可选的：仅在 AgentEnvironment 可用时激活。

**回滚：** 从 SandboxConfig 中删除 `volumes` 字段。沙箱容器恢复为无挂载行为。

## Open Questions — 开放问题

无 — 所有设计决策在探索期间已解决。
