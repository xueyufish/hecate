## ADDED Requirements — 新增需求

### 需求：SandboxExecutor docker exec 支持
系统应扩展 `SandboxExecutor.execute()` 方法，添加可选的 `container_id` 关键字参数。当提供 `container_id` 时，执行器应通过 `docker exec` 在指定的已存在容器内运行工具，直接返回 stdout/stderr/exit_code。当省略 `container_id` 时，执行器应使用现有的 `docker run --detach → docker wait → docker logs → docker rm` 路径（向后兼容）。

#### 场景：在已有容器中执行
- **当** 调用 `execute(tool_name, args, config, container_id="abc123")` 时
- **则** 执行器运行 `docker exec abc123 <command>` 并返回带有执行输出的 `SandboxResult`
- **且** 容器 `abc123` 在执行后保持运行

#### 场景：不带 container_id 执行（向后兼容）
- **当** 调用 `execute(tool_name, args, config)` 而未提供 `container_id` 时
- **则** 执行器通过 `docker run --detach` 创建新容器，执行并销毁它（现有行为）

#### 场景：docker exec 超时
- **当** `docker exec` 调用超过配置的超时时间时
- **则** 执行器终止 exec 进程并返回带有 `timed_out=True` 的 `SandboxResult`
- **且** 容器本身不被销毁（由池管理）

### 需求：SandboxPool execute 使用池化容器
系统应修复 `SandboxPool.execute()`，将已分配容器的 `container_id` 传递给 `SandboxExecutor.execute()`，确保池化容器被实际用于工具执行。

#### 场景：池 execute 使用池化容器
- **当** 调用 `pool.execute("execute_code", {"code": "print(1)"})` 时
- **则** 池分配一个容器，通过 `docker exec` 在该容器内执行工具，并回收该容器
- **且** 执行器不创建单独的容器

### 需求：启动时预热池
系统应在池初始化时预创建 `SANDBOX_POOL_SIZE` 个容器（默认 3），每个容器运行 `sleep infinity` 以保持存活并准备好用于工具执行。

#### 场景：预热创建配置数量的容器
- **当** 使用 `pool_size=3` 初始化池时
- **则** 创建 3 个 Docker 容器，资源限制匹配 `SandboxConfig`
- **且** 每个容器将 `sleep infinity` 作为其入口点运行

#### 场景：预热部分失败继续运行
- **当** 预热期间一个容器创建失败时
- **则** 池记录警告并使用剩余容器继续
- **且** 池在调用 allocate 时按需补充

### 需求：获取时健康检查
系统应在将池化容器交给调用者之前验证其是否存活。健康检查应执行无操作命令（`docker exec <id> true`）并验证退出码为 0。如果容器已死亡，应将其丢弃，池应尝试下一个容器或创建新的容器。

#### 场景：健康容器被分配
- **当** 调用 `allocate()` 且第一个可用容器通过健康检查时
- **则** 容器被标记为 in_use 并返回给调用者

#### 场景：死亡容器被检测并替换
- **当** 调用 `allocate()` 且容器未通过健康检查时
- **则** 死亡容器从池中移除并销毁
- **且** 池尝试下一个可用容器或创建一个新的

### 需求：容器回收带状态清理
系统应在每次使用后通过删除 `/tmp` 中的所有文件来清理容器状态。清理后，容器应标记为可用于复用。如果容器已达到 `SANDBOX_MAX_USES`（默认 50），应销毁而不是回收。

#### 场景：回收清理并归还到池
- **当** 调用 `recycle(container)` 且 `use_count < max_uses` 时
- **则** 容器的 `/tmp` 目录被清理
- **且** 容器标记为池中可用

#### 场景：达到最大使用次数时回收销毁
- **当** 调用 `recycle(container)` 且 `use_count >= max_uses` 时
- **则** 容器通过 `docker rm -f` 销毁
- **且** 容器从池中移除

#### 场景：回收失败销毁容器
- **当** 清理命令失败时
- **则** 容器被销毁以防止状态污染
- **且** 记录警告

### 需求：用于崩溃恢复的 TTL 忙碌标记
系统应跟踪每个容器的分配时间。后台任务应定期检查已 in_use 超过 `SANDBOX_POOL_BUSY_TTL`（默认 1800 秒 / 30 分钟）的容器。陈旧的容器应被强制释放回池。

#### 场景：崩溃恢复释放陈旧容器
- **当** 容器已 in_use 超过忙碌 TTL 时
- **则** 后台任务将容器标记为可用
- **且** 容器在复用前被清理

#### 场景：活动容器不受影响
- **当** 容器已 in_use 少于忙碌 TTL 时
- **则** 后台任务不修改其状态

### 需求：空闲清理
系统应监控空闲容器数量。当空闲容器数量超过 `SANDBOX_POOL_SIZE` 且空闲容器已空闲超过 `SANDBOX_POOL_IDLE_TIMEOUT`（默认 300 秒）时，多余的容器应被销毁。

#### 场景：超时后修剪多余的空闲容器
- **当** 池有 5 个空闲容器、`pool_size=3` 且 2 个容器已空闲超过 300 秒时
- **则** 2 个多余的容器被销毁
- **且** 池返回到 `pool_size`

#### 场景：最近使用的容器不被修剪
- **当** 容器已空闲少于空闲超时时间时
- **则** 即使池超过 `pool_size`，它也不被修剪

### 需求：耗尽策略
系统应支持两种池耗尽策略，通过 `SANDBOX_POOL_EXHAUSTION_STRATEGY`（默认 `wait`）配置：

- `wait`：阻塞直到容器可用，最多等待 `SANDBOX_POOL_ACQUIRE_TIMEOUT` 秒（默认 30）。超时时抛出 `PoolExhaustedError`。
- `temporary`：在池外创建临时容器，使用它，执行后销毁。

#### 场景：WAIT 策略阻塞后成功
- **当** 池耗尽且配置了 `wait` 策略时
- **则** `allocate()` 阻塞直到容器被回收或超时
- **且** 如果容器在超时内变为可用，则将其返回

#### 场景：WAIT 策略超时
- **当** 池耗尽且没有容器在 `SANDBOX_POOL_ACQUIRE_TIMEOUT` 内变为可用时
- **则** `allocate()` 抛出 `PoolExhaustedError`

#### 场景：TEMPORARY 策略创建临时容器
- **当** 池耗尽且配置了 `temporary` 策略时
- **则** 在池外创建新容器，用于执行，然后销毁
- **且** 容器不归还到池

### 需求：池默认禁用
系统应默认 `SANDBOX_POOL_ENABLED=false`。禁用时，不实例化池，`builtin.py::_execute_code()` 和 `port.tool_execute_sandbox()` 直接使用 `SandboxExecutor`（现有的每次执行创建容器的行为）。

#### 场景：禁用时无开销
- **当** `SANDBOX_POOL_ENABLED=false` 时
- **则** 不创建 `SandboxPool` 实例
- **且** 所有沙箱执行使用 `SandboxExecutor.execute()` 而不带 `container_id`

#### 场景：启动时启用池
- **当** `SANDBOX_POOL_ENABLED=true` 且应用程序启动时
- **则** 池初始化、预热并注册供使用
- **且** 所有沙箱执行通过池路由

### 需求：优雅关闭刷新池
系统应在应用程序关闭时销毁所有池化容器。当前状态为 in_use 的容器应在当前执行完成或忙碌 TTL 到期后销毁，以先到者为准。

#### 场景：关闭销毁所有容器
- **当** 应用程序收到关闭信号时
- **则** 所有空闲容器立即销毁
- **且** in_use 容器在执行完成或忙碌 TTL 到期后销毁

### 需求：通过环境变量配置
系统应通过环境变量提供以下配置选项，通过 `core/config.py` Settings 加载：

| 变量 | 默认值 | 描述 |
|----------|---------|-------------|
| `SANDBOX_POOL_ENABLED` | `false` | 启用/禁用沙箱容器池 |
| `SANDBOX_POOL_SIZE` | `3` | 预热的容器数量 |
| `SANDBOX_MAX_USES` | `50` | 容器退役前的最大使用次数 |
| `SANDBOX_POOL_IDLE_TIMEOUT` | `300` | 修剪多余空闲容器前的等待秒数 |
| `SANDBOX_POOL_ACQUIRE_TIMEOUT` | `30` | 池耗尽时的等待秒数（WAIT 策略） |
| `SANDBOX_POOL_BUSY_TTL` | `1800` | 强制释放陈旧 in_use 容器前的秒数 |
| `SANDBOX_POOL_EXHAUSTION_STRATEGY` | `wait` | 耗尽策略：`wait` 或 `temporary` |

#### 场景：自定义池大小
- **当** 设置 `SANDBOX_POOL_SIZE=10` 时
- **则** 池在启动时预热 10 个容器

#### 场景：无效的耗尽策略
- **当** 设置 `SANDBOX_POOL_EXHAUSTION_STRATEGY=invalid` 时
- **则** 系统回退到 `wait` 策略并记录警告
