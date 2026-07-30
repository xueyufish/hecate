## 1. Config Settings — 配置设置

- [x] 1.1 在 `src/hecate/core/config.py` 的 `Settings` 类中添加 `SANDBOX_MOUNT_MODE: str = "rw"`
- [x] 1.2 为 `SANDBOX_MOUNT_MODE` 添加 `.env.example` 条目并附注释

## 2. SandboxConfig Volume Support — SandboxConfig 卷支持

- [x] 2.1 在 `src/hecate/services/sandbox/executor.py` 的 `SandboxConfig` 数据类中添加 `volumes: dict[str, str] = field(default_factory=dict)` 字段
- [x] 2.2 在 `SandboxExecutor._create_container()` 中，从 `cfg.volumes` 追加 `--volume {host}:{container}` 参数到 `docker_args` 列表，包含来自 `settings.SANDBOX_MOUNT_MODE` 的挂载模式后缀
- [x] 2.3 更新 `SandboxPool.__init__()` 或 `SandboxPool._acquire_new()` 以在创建容器时传播执行器的配置卷（验证现有行为已传递配置）

## 3. Environment Bridge — 环境桥接

- [x] 3.1 创建 `src/hecate/services/sandbox/environment_bridge.py`，包含 `resolve_environment_volumes(env: AgentEnvironment | None) -> dict[str, str]` 函数
- [x] 3.2 实现 DockerEnvironment 分支：导入 `DockerEnvironment`，检查 `isinstance`，返回 `{env._volume_name: "/mnt/env"}`（需要验证 DockerEnvironment 上的卷名属性）
- [x] 3.3 实现 LocalEnvironment 分支：检查 `isinstance`，返回 `{str(env.root_path): "/mnt/env"}`
- [x] 3.4 实现 None 分支：返回 `{}`
- [x] 3.5 更新 `src/hecate/services/sandbox/__init__.py` 以导出 `resolve_environment_volumes`

## 4. BuiltinTools Wiring — BuiltinTools 接线

- [x] 4.1 修改 `src/hecate/services/tool/builtin.py` 中的 `BuiltinTools._execute_code()`，接受可选的 `environment: AgentEnvironment | None` 参数
- [x] 4.2 调用 `resolve_environment_volumes(environment)` 获取卷挂载
- [x] 4.3 将 `SandboxConfig(volumes=volume_mounts)` 传递给 `SandboxExecutor()`
- [x] 4.4 验证调用链：找到 `_execute_code` 的调用位置，确保环境被传递（在 WorkflowExecutionService 或 ToolWorker 中检查工具注册）

## 5. Tests — 测试

- [x] 5.1 创建 `tests/test_services/test_sandbox/test_environment_bridge.py`，包含 `resolve_environment_volumes()` 的单元测试
- [x] 5.2 测试：DockerEnvironment 解析为命名卷映射 `{volume_name: "/mnt/env"}`
- [x] 5.3 测试：LocalEnvironment 解析为主机 bind mount 映射 `{root_path: "/mnt/env"}`
- [x] 5.4 测试：None 环境解析为空字典 `{}`
- [x] 5.5 创建 `tests/test_services/test_sandbox/test_executor_volumes.py`，包含 SandboxExecutor 卷挂载的测试
- [x] 5.6 测试：空卷的 SandboxConfig 在 docker run 命令中不产生 `--volume` 参数
- [x] 5.7 测试：有卷的 SandboxConfig 产生正确的 `--volume host:container:rw` 参数
- [x] 5.8 测试：`SANDBOX_MOUNT_MODE=ro` 的 SandboxConfig 产生 `:ro` 后缀
- [x] 5.9 测试：环境可用时的 execute_code 将卷传递给 SandboxExecutor（基于 mock 的集成测试）
- [x] 5.10 测试：无环境时的 execute_code 传递空卷（向后兼容）

## 6. Documentation — 文档

- [x] 6.1 为 `resolve_environment_volumes()` 和 `SandboxConfig.volumes` 字段添加文档字符串（英文，根据 AGENTS.md）
- [x] 6.2 更新 `src/hecate/services/sandbox/__init__.py` 模块文档字符串以提及环境挂载
- [x] 6.3 在 `_create_container()` 中添加解释卷挂载参数的内联注释

## 7. Verification — 验证

- [x] 7.1 运行 `ruff check src/hecate/ tests/` — 预期 0 错误
- [x] 7.2 运行 `ruff format --check src/ tests/` — 预期全部格式化
- [x] 7.3 运行 `mypy src/` — 预期 0 错误
- [x] 7.4 运行 `python -m pytest tests/test_services/test_sandbox/ -v` — 全部通过
- [x] 7.5 运行 `python -m pytest tests/test_services/test_context/ tests/test_engine/ tests/test_services/test_environment/ -q` — 无回归
