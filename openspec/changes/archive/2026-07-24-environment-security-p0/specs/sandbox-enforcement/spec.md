## ADDED Requirements — 新增需求

### 需求：EXECUTE_SANDBOX 路由到 DockerEnvironment
系统应在 `ToolAccessPolicy.evaluate()` 返回 `EXECUTE_SANDBOX` 且沙箱强制实施启用时，将工具执行路由到 DockerEnvironment。路由应适用于 shell/exec 工具和 `sandbox_enabled` 的 MCP 工具。

#### 场景：带 EXECUTE_SANDBOX 的 Shell 工具路由到容器
- **当** `AGENT_ENV_SANDBOX_ENFORCEMENT=true` 且 `ToolAccessPolicy.evaluate()` 为工具 `bash` 返回 `EXECUTE_SANDBOX` 时
- **则** 工具通过 `exec_shell()` 在 Agent 的 DockerEnvironment 容器内执行
- **且** 工具不直接在主机上执行

#### 场景：沙箱强制实施默认禁用
- **当** 未设置 `AGENT_ENV_SANDBOX_ENFORCEMENT`（默认 `false`）时
- **则** `EXECUTE_SANDBOX` 决策被视为 `EXECUTE`（当前行为，向后兼容）

#### 场景：Python 内置工具不路由到容器
- **当** 沙箱强制实施启用且工具 `read_file` 得到 `EXECUTE_SANDBOX` 时
- **则** 工具直接执行（由 WorkspaceBoundaryPolicy 管理）
- **且** Python 函数工具不发生容器路由

#### 场景：带 sandbox_enabled 的 MCP 工具路由到容器
- **当** 沙箱强制实施启用且 `sandbox_enabled=True` 的 MCP 工具得到 `EXECUTE_SANDBOX` 时
- **则** MCP 工具调用在 Agent 的 DockerEnvironment 容器内执行

### 需求：容器退出验证
系统应在沙箱工具执行后验证容器健康状态。如果容器进程异常退出，系统应发出安全审计事件。

#### 场景：正常容器退出
- **当** 沙箱工具完成且容器仍在运行，返回码正常时
- **则** 不发出异常事件

#### 场景：检测到异常容器退出
- **当** 沙箱工具完成但 `proc.returncode` 指示容器进程被杀死（OOM、段错误）时
- **则** 发出 `decision="sandbox_anomaly"` 的 `SecurityAuditEvent`
- **且** 使用容器 ID 和退出码记录 WARNING

### 需求：安全配置更改时热池配置失效
系统应在 Agent 的安全配置更改时使热池容器失效。

#### 场景：Agent 更新时安全配置哈希更改
- **当** Agent 的安全配置（网络策略、凭证范围、沙箱强制实施）被更新时
- **则** Agent 的 `security_config_hash` 更改
- **且** 该 Agent 的任何热池容器被销毁（不复用）
- **且** 下一次 `get_or_create()` 使用更新后的配置创建新容器

#### 场景：不相关的配置更改不导致失效
- **当** Agent 的非安全配置（例如显示名称）被更新时
- **则** `security_config_hash` 不更改
- **且** 热池容器保持有效以供复用
