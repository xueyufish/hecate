## Why

P1 的工具执行没有沙箱隔离，恶意或错误的工具调用可能影响宿主系统。P3 需要工具执行沙箱来隔离风险。

## What Changes

- **新增 `SandboxExecutor`**：Docker 容器沙箱执行工具调用
- **新增 `SandboxPool`**：沙箱池管理，支持预热和复用
- **新增资源限制**：CPU/内存/网络/文件系统隔离
- **扩展 `EnginePort`**：新增 `tool_execute_sandbox` 方法

## Capabilities

### New Capabilities

- `sandbox-executor`: Docker 容器沙箱执行
- `sandbox-pool`: 沙箱池管理（预热/复用/回收）

### Modified Capabilities

（无）

## Impact

- **新增代码**：`services/sandbox/` 目录，约 500-700 行
- **新增依赖**：`docker`（Docker Python SDK）
- **修改代码**：`engine/ports.py`（新增可选沙箱方法）
- **架构影响**：工具执行分为直接执行和沙箱执行两种模式
