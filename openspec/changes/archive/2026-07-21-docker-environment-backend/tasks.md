## 1. AgentEnvironment ABC: Add exec_shell — 添加 exec_shell

- [x] 1.1 在 `src/hecate/services/environment/environment.py` 中添加 `ExecResult` 数据类，包含字段 `exit_code: int`、`stdout: bytes`、`stderr: bytes`
- [x] 1.2 向 `AgentEnvironment` ABC 添加抽象方法 `exec_shell(self, command: list[str], *, cwd: str | None = None, timeout: float | None = None) -> ExecResult`
- [x] 1.3 在 `LocalEnvironment` 上使用 `asyncio.create_subprocess_exec` 实现 `exec_shell` — 在主机上运行命令，分别捕获 stdout/stderr，使用 `asyncio.wait_for` 处理超时

## 2. DockerEnvironment Implementation — DockerEnvironment 实现

- [x] 2.1 将 `aiodocker` 添加到 `pyproject.toml` 的 `[tools]` 可选依赖组中
- [x] 2.2 在 `src/hecate/services/environment/environment.py`（或如果文件过大的新 `docker_environment.py`）中创建 `DockerEnvironment` 类，实现所有 `AgentEnvironment` 抽象方法
- [x] 2.3 实现容器生命周期：`__init__` 存储 agent_id + 配置；`_ensure_container()` 通过 `aiodocker.Docker()` 客户端懒创建或复用容器
- [x] 2.4 使用 `container.get_archive(path)` → tar 提取 → 返回字节实现 `read_file(path)`（参考：AgentScope `DockerBackend.read_file`）
- [x] 2.5 使用内存 tar 创建 → `container.put_archive(parent_dir, tar_bytes)` 实现 `write_file(path, content)`（参考：AgentScope `DockerBackend.write_file`）
- [x] 2.6 使用 `container.exec(cmd)` → 流式 stdout/stderr → 返回 `ExecResult` 实现 `exec_shell(command)`（参考：AgentScope `DockerBackend.exec_shell`）
- [x] 2.7 通过 `exec_shell` 组合实现 `list_files(path)`、`delete_file(path)`、`exists(path)`（例如 `ls -la`、`rm`、`test -e`）— 在需要时解析输出到 `FileInfo`
- [x] 2.8 实现 `ensure_dirs()` — `exec_shell(["mkdir", "-p", ...])` 用于 sessions/、files/、memory/、skills/
- [x] 2.9 实现适用于容器上下文的 `root_path` 和 `environment_id` 属性（例如 `environment_id` = agent_id、`root_path` = `/env`）
- [x] 2.10 支持可配置的运行时：当设置 `DOCKER_RUNTIME` 时，将 `runtime`（runc/runsc）传递给容器创建

## 3. EnvironmentManager Refactor — EnvironmentManager 重构

- [x] 3.1 向 `src/hecate/core/config.py` 添加 `AGENT_ENV_BACKEND` 设置（类型：`str`，默认：`"local"`，选项：`["local", "docker"]`）
- [x] 3.2 向配置添加 Docker 特定设置：`DOCKER_AGENT_IMAGE`（默认：`"python:3.12-slim"`）、`DOCKER_RUNTIME`（默认：`"runc"`）、`DOCKER_NETWORK_MODE`（默认：`"none"`）、`DOCKER_WARM_POOL_SIZE`（默认：10）、`DOCKER_WARM_POOL_IDLE_TIMEOUT`（默认：3600）
- [x] 3.3 在 `EnvironmentManager.__init__` 中验证 `AGENT_ENV_BACKEND` 值 — 对未识别的值抛出 `ValueError`
- [x] 3.4 重构 `EnvironmentManager.get_or_create()` 以根据 `AGENT_ENV_BACKEND` 选择后端：`"local"` → `LocalEnvironment`、`"docker"` → `DockerEnvironment`
- [x] 3.5 为 Docker 后端实现热池：`close(agent_id)` 将容器移动到空闲列表而非销毁；`get_or_create(agent_id)` 首先检查热池
- [x] 3.6 实现热池驱逐：当池满时，销毁最旧的空闲容器；在每次 `get_or_create` 时清理超过超时的空闲容器
- [x] 3.7 确保 `close_all()` 销毁 docker 后端的全部容器（卷持久存在）

## 4. Tests — 测试

- [x] 4.1 测试 `LocalEnvironment.exec_shell`：基本命令执行、工作目录、超时、stderr 捕获
- [x] 4.2 测试 `ExecResult` 数据类：字段类型、默认值
- [x] 4.3 测试 `DockerEnvironment` 容器创建：镜像拉取、卷挂载、子目录创建 — **需要 Docker 守护进程，如果不可用则跳过**（`pytest.mark.skipif`）
- [x] 4.4 测试 `DockerEnvironment` 文件操作：写入 → 读取往返、list_files、delete_file、exists — **需要 Docker 守护进程**
- [x] 4.5 测试 `DockerEnvironment.exec_shell`：命令在容器内运行、返回正确的 exit_code/stdout/stderr — **需要 Docker 守护进程**
- [x] 4.6 测试 `EnvironmentManager` 后端选择：`"local"` 创建 `LocalEnvironment`、`"docker"` 创建 `DockerEnvironment`、无效值抛出 `ValueError`
- [x] 4.7 测试热池：容器在关闭时移动到池、在重新访问时复用、满时驱逐、超时清理
- [x] 4.8 测试默认配置（`AGENT_ENV_BACKEND` 未设置）保留现有的 `LocalEnvironment` 行为（无回归）

## 5. Documentation — 文档

- [x] 5.1 更新 `src/hecate/services/environment/__init__.py` 导出以包含 `DockerEnvironment` 和 `ExecResult`
- [x] 5.2 为所有新的公共类和方法添加文档字符串（英文，根据编码规则）
- [x] 5.3 在 `config.py` 中为新的 Docker 相关设置添加配置文档注释

## 6. Verification — 验证

- [x] 6.1 运行 `ruff check src/hecate/ tests/` — 预期 0 错误
- [x] 6.2 运行 `ruff format --check src/ tests/` — 预期全部格式化
- [x] 6.3 运行 `mypy src/` — 预期 0 错误
- [x] 6.4 运行 `python -m pytest tests/test_services/test_environment/ -v` — 全部通过（Docker 测试在没有守护进程时跳过）
- [x] 6.5 运行 `python -m pytest tests/ -q` — 无回归
