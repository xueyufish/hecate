## ADDED Requirements

### Requirement: /health/live 端点返回进程存活状态

`src/hecate/main.py` SHALL 提供 `GET /health/live` 端点，返回 HTTP 200 + `{"status":"alive"}` 表示进程存活。该端点 SHALL NOT 检查任何外部依赖（DB/Redis/Qdrant）——仅检测 Python 进程是否在运行且 event loop 未阻塞。

K8s `livenessProbe` SHALL 指向此端点。liveness 失败 → K8s 重启 Pod。

#### Scenario: 进程存活时返回 200
- **WHEN** `GET /health/live` 被调用且 Python 进程正常运行
- **THEN** 返回 HTTP 200 + `{"status":"alive"}`

#### Scenario: 不检查外部依赖
- **WHEN** DB / Redis / Qdrant 全部不可达但 Python 进程存活
- **THEN** `GET /health/live` 仍返回 HTTP 200（外部依赖不可达不是进程死亡）

### Requirement: /health/ready 端点返回可服务状态含 SIGTERM flag

`src/hecate/main.py` SHALL 提供 `GET /health/ready` 端点，检查进程是否可以接收新请求。检查项 SHALL 包含：
1. 全局 `SHOULD_ACCEPT_TRAFFIC` flag 为 `True`（SIGTERM 后置为 `False`）
2. 数据库连接可达（执行 `SELECT 1`）
3. Redis 连接可达（如 `SESSION_STATE_STORE_BACKEND != "memory"`，执行 `PING`）
4. Qdrant 连接可达（执行 health API 调用）

所有检查通过 → HTTP 200 + `{"status":"ready","checks":{...}}`。任一失败 → HTTP 503 + `{"status":"not_ready","checks":{...},"failed":[...]}`。

K8s `readinessProbe` SHALL 指向此端点。readiness 失败 → K8s 从 Endpoints 摘除 Pod（不重启，只停止路由流量）。

#### Scenario: 所有依赖健康时返回 200
- **WHEN** `GET /health/ready` 被调用且 DB/Redis/Qdrant 全部可达且 `SHOULD_ACCEPT_TRAFFIC == True`
- **THEN** 返回 HTTP 200 + 每项检查的详细状态

#### Scenario: DB 不可达时返回 503
- **WHEN** 数据库连接失败（连接超时 / 认证错误 / 网络不可达）
- **THEN** 返回 HTTP 503 + `failed: ["database"]`

#### Scenario: SIGTERM 后返回 503
- **WHEN** 进程收到 SIGTERM 信号后，`GET /health/ready` 被调用
- **THEN** `SHOULD_ACCEPT_TRAFFIC == False`
- **THEN** 返回 HTTP 503 + `failed: ["draining"]`

### Requirement: /health/startup 端点返回初始化完成状态

`src/hecate/main.py` SHALL 提供 `GET /health/startup` 端点。在 lifespan 初始化完成前返回 HTTP 503。lifespan 初始化完成后（DB pool 创建、Redis 连接、sandbox pool 启动、session state store 初始化、event store 初始化）返回 HTTP 200。

K8s `startupProbe` SHALL 指向此端点。startup 通过前，K8s 不执行 liveness/readiness 探测，避免慢启动应用被误杀重启。

#### Scenario: 初始化中返回 503
- **WHEN** FastAPI lifespan 尚未完成（DB/Redis/sandbox 正在初始化）
- **THEN** `GET /health/startup` 返回 HTTP 503

#### Scenario: 初始化完成后返回 200
- **WHEN** lifespan yield 完成（所有 startup 初始化步骤执行完毕）
- **THEN** `GET /health/startup` 返回 HTTP 200

### Requirement: SIGTERM 触发 graceful shutdown 流量排干

`src/hecate/main.py` SHALL 注册 `signal.SIGTERM` handler。收到 SIGTERM 时按以下顺序执行：

1. 立即将全局 `SHOULD_ACCEPT_TRAFFIC` 置为 `False`
2. `/health/ready` 开始返回 503（K8s/LB 探测到后摘除 Pod）
3. 等待在途请求完成（track `ACTIVE_REQUESTS` 计数器，等待降至 0 或超时 `SHUTDOWN_DRAIN_TIMEOUT`，默认 30s）
4. 执行 lifespan shutdown（关闭 DB pool、Redis 连接、sandbox pool、event store）
5. 进程退出

`SHUTDOWN_DRAIN_TIMEOUT` SHALL 可通过环境变量配置（默认 30 秒）。超时后强制关闭剩余连接。

#### Scenario: SIGTERM 后 readiness 立即返回 503
- **WHEN** 进程收到 SIGTERM
- **THEN** 在 < 1ms 内 `SHOULD_ACCEPT_TRAFFIC == False`
- **THEN** 下一次 `GET /health/ready` 返回 503

#### Scenario: 在途请求完成后才关闭连接
- **WHEN** SIGTERM 收到时有 3 个在途请求（`ACTIVE_REQUESTS == 3`）
- **THEN** 等待 3 个请求全部完成（或 `SHUTDOWN_DRAIN_TIMEOUT` 超时）
- **THEN** 才执行 lifespan shutdown 关闭 DB/Redis 连接

#### Scenario: 超时后强制关闭
- **WHEN** 在途请求在 `SHUTDOWN_DRAIN_TIMEOUT` 内未完成
- **THEN** 强制执行 lifespan shutdown，剩余请求收到连接关闭错误

### Requirement: /version API 返回构建信息

`src/hecate/main.py` SHALL 提供 `GET /version` 端点，返回 JSON：

```json
{
  "version": "0.1.0",
  "commit": "abc1234",
  "alembic_head": "h9c0d1e2f3a4",
  "python": "3.12.13",
  "build_date": "2026-08-07T10:00:00Z"
}
```

`version` SHALL 来自 `hecate.__version__`。`commit` SHALL 来自环境变量 `GIT_COMMIT`（构建时注入），未设置时为 `"unknown"`。`alembic_head` SHALL 从 Alembic 配置读取当前 head revision。`build_date` SHALL 来自环境变量 `BUILD_DATE`，未设置时为 `"unknown"`。

#### Scenario: 返回完整构建信息
- **WHEN** `GET /version` 被调用
- **THEN** 返回 HTTP 200 + version / commit / alembic_head / python / build_date

#### Scenario: 未注入 commit 时返回 unknown
- **WHEN** 环境变量 `GIT_COMMIT` 未设置
- **THEN** `commit` 字段为 `"unknown"`
