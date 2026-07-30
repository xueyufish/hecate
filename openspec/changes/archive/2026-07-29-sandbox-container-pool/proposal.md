## Why — 动机

当前的沙箱执行模型为每次工具调用都创建一个新的 Docker 容器（`docker run --detach → docker wait → docker logs → docker rm`），导致每次执行产生 2-5 秒的冷启动延迟。对于需要频繁执行代码的 Agent 工作流（数据分析、代码生成、迭代测试），这一开销占据了主导执行时间。预热并复用容器可以消除这一成本——行业基准测试显示 10 倍性能提升（LLM Sandbox, Polpo）。

现有的 `SandboxPool` 类（`services/sandbox/pool.py`, 223 行）存在一个根本设计缺陷：其 `execute()` 方法从池中分配了一个容器，但随后委托给 `SandboxExecutor.execute()`，后者又创建了一个完全独立的容器。池化容器从未被实际用于执行。本次变更修复了此缺陷并将其接入生产环境。

## What Changes — 变更内容

- **修复 SandboxExecutor**：为 `execute()` 方法添加可选的 `container_id` 参数。当提供该参数时，使用 `docker exec` 在已有的运行中容器内执行，而非 `docker run --detach`。单一统一方法，向后兼容。
- **修复 SandboxPool.execute()**：使用 `executor.execute(..., container_id=container.container_id)` 替代 `executor.execute(...)`，确保池化容器被实际用于执行。
- **添加生产级池特性**：获取时的健康检查、TTL 忙碌标记（崩溃恢复）、空闲清理、每工作空间并发限制、耗尽策略（WAIT/TEMPORARY）。
- **接入执行路径**：`builtin.py::_execute_code()` 和 `port.tool_execute_sandbox()` 服务适配器在启用时使用 `SandboxPool`。
- **配置**：新增环境变量 `SANDBOX_POOL_ENABLED`（默认 false）、`SANDBOX_POOL_SIZE`（默认 3）、`SANDBOX_MAX_USES`（默认 50）、`SANDBOX_POOL_IDLE_TIMEOUT`（默认 300s）、`SANDBOX_POOL_ACQUIRE_TIMEOUT`（默认 30s）。
- **生命周期管理**：应用启动时预热池，应用关闭时销毁池（main.py lifespan 集成）。

## Capabilities — 能力

### 新增能力

- `sandbox-container-pool`：用于沙箱工具执行的预热 Docker 容器池——预热、分配、通过 `docker exec` 执行、回收（清理 + 归还）、达到最大使用次数后退役。获取时健康检查、TTL 忙碌标记用于崩溃恢复、空闲清理、每工作空间并发限制、可配置的耗尽策略（WAIT/TEMPORARY）。默认关闭，通过 `SANDBOX_POOL_ENABLED=true` 启用。

### 被修改的能力

（无——现有的 `execution-security` spec 中关于工具审批和风险授权的需求保持不变；池化是安全决策层之下的性能优化层）

## Impact — 影响

- **代码**：`services/sandbox/pool.py`（修复 + 增强）、`services/sandbox/executor.py`（添加 container_id 路径）、`services/tool/builtin.py`（接入池）、`engine/ports.py` + 服务适配器（将池接入 tool_execute_sandbox）、`core/config.py`（新设置项）、`main.py`（生命周期集成）
- **测试**：更新现有的 `tests/test_services/test_sandbox/test_pool.py`（修复了验证设计缺陷的失败测试），新增健康检查、TTL、空闲清理、耗尽策略的测试以及集成测试
- **依赖**：无新增（Docker CLI 已是 SandboxExecutor 的必需依赖）
- **性能**：消除了每次沙箱工具执行 2-5 秒的容器创建开销；预热获取 < 100ms
- **安全**：无变化——池化容器使用与每次执行容器相同的资源限制（CPU/内存/网络/只读文件系统）；回收时清理 `/tmp`；`read_only_fs=True` 限制可写表面区域
