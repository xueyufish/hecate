## 1. SandboxExecutor: docker exec support — docker exec 支持

- [x] 1.1 向 `SandboxExecutor.execute()` 添加可选关键字参数 `container_id: str | None = None`
- [x] 1.2 使用带超时处理的 `docker exec` 实现 `_exec_in_container(container_id, tool_name, args, config)` 私有方法
- [x] 1.3 在 `execute()` 中路由：如果提供了 `container_id` → `_exec_in_container()`，否则 → 现有的 `_create_and_run()` 路径
- [x] 1.4 确保 `docker exec` 超时终止 exec 进程但不销毁容器
- [x] 1.5 更新现有的 SandboxExecutor 测试以覆盖两个路径（带和不带 container_id）

## 2. SandboxPool: fix execute + add production features — 修复 execute + 添加生产级功能

- [x] 2.1 修复 `SandboxPool.execute()` 以传递 `container_id=container.container_id` 给 `executor.execute()`
- [x] 2.2 添加获取时健康检查：在将容器返回给调用者之前执行 `docker exec <id> true`；丢弃死亡容器
- [x] 2.3 向 `PooledContainer` 添加 `allocated_at` 时间戳用于 TTL 忙碌标记跟踪
- [x] 2.4 添加 `_reap_stale_containers()` 后台任务：强制释放 in_use 超过 `SANDBOX_POOL_BUSY_TTL` 的容器
- [x] 2.5 添加空闲清理：跟踪 `last_used_at`，销毁超过 `SANDBOX_POOL_IDLE_TIMEOUT` 的多余空闲容器
- [x] 2.6 添加耗尽策略支持：`wait`（带超时阻塞）和 `temporary`（在池外创建并销毁）
- [x] 2.7 添加 WAIT 超时的 `PoolExhaustedError` 异常
- [x] 2.8 更新 `_create_fresh_container()` 以使用 `sleep infinity` 入口点（已完成，验证）

## 3. Configuration — 配置

- [x] 3.1 向 `core/config.py` 添加设置：`SANDBOX_POOL_ENABLED`（bool，默认 False）
- [x] 3.2 添加 `SANDBOX_POOL_SIZE`（int，默认 3）、`SANDBOX_MAX_USES`（int，默认 50）
- [x] 3.3 添加 `SANDBOX_POOL_IDLE_TIMEOUT`（int，默认 300）、`SANDBOX_POOL_ACQUIRE_TIMEOUT`（int，默认 30）
- [x] 3.4 添加 `SANDBOX_POOL_BUSY_TTL`（int，默认 1800）
- [x] 3.5 添加 `SANDBOX_POOL_EXHAUSTION_STRATEGY`（str，默认 "wait"）带验证（无效值时回退到 "wait"）
- [x] 3.6 使用所有新的沙箱池变量和注释更新 `.env.example`

## 4. Wiring: execution path integration — 接线：执行路径集成

- [x] 4.1 在 `services/sandbox/__init__.py` 中创建 `get_sandbox_pool()` 单例访问器 — 返回池实例或 None（如果禁用）
- [x] 4.2 接入 `builtin.py::_execute_code()`：启用时使用池，禁用时回退到直接 SandboxExecutor
- [x] 4.3 接入 `port.tool_execute_sandbox()` 服务适配器：启用时使用池，禁用时回退到直接 SandboxExecutor
- [x] 4.4 向 `main.py` 添加池生命周期：启动时预热，清理时关闭（lifespan 上下文）

## 5. Tests: fix broken tests + add new coverage — 测试：修复损坏的测试 + 添加新覆盖

- [x] 5.1 修复 `test_execute_delegates_to_executor` — 更新以验证 `container_id` 被传递给执行器
- [x] 5.2 添加测试：获取时健康检查检测死亡容器并替换
- [x] 5.3 添加测试：获取时健康检查健康容器通过
- [x] 5.4 添加测试：TTL 忙碌标记回收陈旧的 in_use 容器
- [x] 5.5 添加测试：空闲清理在超时后销毁多余的空闲容器
- [x] 5.6 添加测试：WAIT 耗尽策略阻塞后在超时内成功
- [x] 5.7 添加测试：WAIT 耗尽策略在超时时抛出 PoolExhaustedError
- [x] 5.8 添加测试：TEMPORARY 耗尽策略创建并销毁临时容器
- [x] 5.9 添加测试：回收在达到 max_uses 时销毁容器
- [x] 5.10 添加测试：回收失败销毁容器（防止状态污染）
- [x] 5.11 添加测试：带 container_id 的 SandboxExecutor.execute() 使用 docker exec 路径
- [x] 5.12 添加测试：不带 container_id 的 SandboxExecutor.execute() 使用 docker run 路径（向后兼容）
- [x] 5.13 添加测试：池默认禁用 — 不创建 SandboxPool 实例
- [x] 5.14 添加测试：优雅关闭销毁所有容器

## 6. Verification — 验证

- [x] 6.1 运行 `ruff check src/hecate/ tests/` — 零错误
- [x] 6.2 运行 `ruff format --check src/ tests/` — 零错误
- [x] 6.3 运行 `mypy src/` — 零错误
- [x] 6.4 运行 `python -m pytest tests/ -q` — 所有测试通过，无回归
