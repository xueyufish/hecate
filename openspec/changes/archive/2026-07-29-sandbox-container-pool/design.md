## Context — 背景

沙箱执行系统（9.4c Docker Sandbox Executor）目前每次工具执行都通过 `docker run --detach → docker wait → docker logs → docker rm` 创建一个新的 Docker 容器。这增加了每次执行 2-5 秒的冷启动延迟。

现有的 `SandboxPool` 类（`services/sandbox/pool.py`，223 行）被编写用来解决这个问题，但存在一个根本设计缺陷：其 `execute()` 方法从池中分配一个容器，然后委托给 `SandboxExecutor.execute()`，后者又创建一个完全独立的容器。池化容器从未被使用。

此变更修复了池并基于对 14 个项目/平台（LLM Sandbox、Polpo、E2B、Modal、Bedrock AgentCore、K8s agent-sandbox、container-pool 等）的行业研究添加了生产级功能。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 修复 SandboxPool.execute() 的根本缺陷 — 池化容器必须通过 `docker exec` 用于执行
- 添加生产级池功能：健康检查、TTL 忙碌标记、空闲清理、耗尽策略
- 将池接入两个执行路径（内置代码执行 + port.tool_execute_sandbox）
- 使池可选加入，带合理的默认值
- 禁用池时零行为变更（所有测试不受影响）

**非目标：**
- 每工作空间物理容器隔离（未来增强；全局池配合 `/tmp` 清理对 MVP 足够）
- 分布式池（Redis 支持的多节点）— 单进程 `asyncio` 池对单主机 Docker 部署足够
- DockerEnvironment（9.13 路径）的池 — 那是每 Agent 持久容器，不是工具执行池
- 每个工具的容器镜像自定义 — 所有池化容器使用单一 `hecate-sandbox:latest` 镜像
- 池的指标/可观测性仪表板 — 仅基本日志记录；指标端点推迟

## Decisions — 决策

### D1：路线 A — 独立池化层（不合并到 DockerEnvironment）

**决策**：SandboxPool 是 SandboxExecutor 之上的独立层，不合并到 DockerEnvironment。

**理由**：所有 14 个研究的项目/平台都使用独立的池化层。没有一个将池管理合并到执行环境中。关键原因：单一职责、可选优化、独立可测试性、后端可替换性。

**考虑的替代方案**：路线 B（将池合并到 DockerEnvironment）— 拒绝，因为 DockerEnvironment 是每 Agent 持久容器，而 SandboxPool 是全局共享的工具执行池。它们解决不同的问题，具有不同的生命周期。

### D2：统一的 execute() 方法带可选 container_id

**决策**：SandboxExecutor.execute() 获得一个可选的 `container_id` 关键字参数。当提供时，执行使用现有容器上的 `docker exec`。当省略时，执行使用现有的 `docker run --detach` 路径。

```python
async def execute(
    self,
    tool_name: str,
    args: dict[str, Any],
    config: SandboxConfig | None = None,
    *,
    container_id: str | None = None,
) -> SandboxResult:
```

**理由**：单一 API 表面，向后兼容，调用者不需要知道两个方法。LLM Sandbox 使用这种精确模式（SandboxDockerSession 连接到 container_id，委托操作）。

**考虑的替代方案**：两个独立方法（`execute()` + `execute_in_container()`）— 拒绝，因为不必要地扩大了 API 表面。区别是内部实现细节。

### D3：全局池范围带每工作空间并发限制

**决策**：一个全局 SandboxPool 实例。所有工具执行共享同一池，无论工作空间如何。每工作空间并发限制防止任何单个工作空间独占池。

**理由**：E2B 使用全局池带每团队并发预留 — 全局池最大化利用率，并发限制防止饥饿。对于使用 `read_only_fs=True` 和 `/tmp` 清理的自托管部署，每工作空间物理隔离是过度设计。

**考虑的替代方案**：每工作空间池 — 由于容器数量爆炸（工作空间数 × 池大小）而拒绝。给定 `read_only_fs=True`，全局池 + `/tmp` 清理足够。

### D4：默认 WAIT 耗尽策略，可配置 TEMPORARY

**决策**：当池耗尽时，默认行为是 WAIT，带 30 秒超时。可配置为 TEMPORARY（在池外创建并销毁）。

**理由**：库项目（LLM Sandbox、container-pool、SQLAlchemy QueuePool）默认使用 WAIT。云平台（Polpo、Modal、Bedrock）默认使用 TEMPORARY，因为它们有弹性资源。我们是自托管的，Docker 容量有限 — WAIT 更安全。30 秒超时匹配 SQLAlchemy 和 LLM Sandbox 的默认值。

### D5：默认禁用（可选加入）

**决策**：`SANDBOX_POOL_ENABLED=false` 默认值。用户显式启用。

**理由**：LLM Sandbox、container-pool 和 Modal 都默认无池。首次发布应该保守。有 Docker 并想要性能的用户可选加入。稳定版本可以将默认值改为 true。

### D6：获取时通过 `docker exec <id> true` 进行健康检查

**决策**：在将池化容器交给调用者之前，通过执行无操作命令验证其是否存活。如果死亡，丢弃并尝试下一个或创建新的。

**理由**：Polpo 的生产博客记录了这至关重要 — "池中的沙箱可能已死亡。" 他们的模式：获取 → isAlive → 使用或丢弃。LLM Sandbox 执行定期 + 获取时检查。SQLAlchemy 的 `pre_ping`（SELECT 1）是数据库等价物。

### D7：用于崩溃恢复的 TTL 忙碌标记

**决策**：当容器被分配时，记录一个时间戳。后台任务检查已 in_use 超过 30 分钟（可配置）的容器并强制释放它们。

**理由**：Polpo 使用 30 分钟 TTL 忙碌标记和 Redis SET。防止崩溃进程永久将容器从流通中移除 — "一种最终排空整个池的缓慢泄漏。"

### D8：与 9.13 沙箱强制实施共存

**决策**：SandboxPool 和 DockerEnvironment 共存而不冲突。

- enforcement=true + shell 工具 → DockerEnvironment.exec_shell()（每 Agent 持久容器，不变）
- enforcement=true + code 工具 → SandboxPool（全局池化容器）
- enforcement=false + sandbox 工具 → SandboxPool

**理由**：DockerEnvironment 和 SandboxPool 解决不同的问题。DockerEnvironment 提供每 Agent 有状态执行（文件在 Agent 会话内跨调用持久化）。SandboxPool 提供无状态隔离执行（在两次使用之间清理）。它们自然服务于不同的工具类型。

## Risks / Trade-offs — 风险 / 权衡

**[执行间状态泄漏]** → 缓解：`read_only_fs=True` 将可写表面限制为仅 `/tmp`；`_clean_container()` 在回收时删除所有 `/tmp` 内容；`docker exec` 不修改基础环境变量。

**[容器在池中死亡]** → 缓解：获取时的健康检查透明地检测死亡容器；死亡容器被丢弃并替换。

**[进程崩溃使容器标记为 in_use]** → 缓解：TTL 忙碌标记（30 分钟）强制释放陈旧的分配；池关闭时销毁所有容器，无论状态如何。

**[高并发下池耗尽]** → 缓解：WAIT 策略带 30 秒超时防止无界资源消耗；每工作空间并发限制防止任何单个工作空间独占；用户可以切换到 TEMPORARY 以获得弹性容量。

**[Docker 守护进程不可用]** → 缓解：池默认禁用；预热失败记录日志但不阻塞启动；分配回退到按需创建，优雅地失败。
