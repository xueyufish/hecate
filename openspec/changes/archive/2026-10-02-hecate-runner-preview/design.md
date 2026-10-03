# Design

## Context

- step5b 遗产:`packages/hecate-runtime` 为内核 wheel(依赖仅 httpx/pydantic/sqlalchemy/cryptography,可选能力全在 extras);`RuntimeConfig` + `configure_kernel` 替代平台 settings;`capabilities_status()` 已能报告 memory/sandbox/a2a 等能力不可用;smoke 测试证明内核可在干净 venv 三节点图端到端。
- step3 遗产:HTTP 绑定 `contracts/openapi/execution-backend.http.v0_1.yaml`(`/capabilities`、`POST /runs`、`GET /runs/{ref}`、`/events`、`/events:stream`、`/cancel`、`/artifacts`、`/{interaction}`,problem+json 错误);`ArtifactManifest`(files+sha256、tools+permission、required_capabilities、model_config_ref)在 `src/hecate/contracts/execution/manifest.py`,**主应用内当前无消费方**——迁移无破坏面。
- SC 绑定契约:`platform-scenario-pack` 规定 implemented 条目必须有 `test_sc<nn>_*` 测试且不得再携带 `blocked_by`;独立基线 §5 登记行与 manifest 状态由一致性测试钉住。
- CI 既有模式:wheel job 构建→干净 venv 安装→断言无 full-hecate→仓库外运行冒烟→断言可选能力声明 unsupported。

## Goals / Non-Goals

**Goals**

- `hecate-runner` 宿主包:profile 启动、服务端身份、只读预览门禁、本地证据、健康/关停、stub 模型默认 + endpoint 可配。
- SC01/SC02 翻转为 implemented 并交付对应场景测试(干净 venv、无控制面、HTTP 驱动)。
- manifest 加载能力进入内核,宿主依赖闭包 = hecate-runtime + 内核既有依赖,零新增第三方依赖。

**Non-Goals**

- 不做持久任务、审批流、写工具、后台重试、断连/重连(受管切片)、制品发布门禁——step6/7/10/11 范围;能力声明为 unsupported。
- 不引入 ASGI 服务器或新 HTTP 框架;生产级服务栈随 step6 持久任务一并重估。
- 不修改平台主应用执行链;不改 step3 契约语义(宿主是绑定与身份契约的实现侧消费者)。
- 不宣称业务收益、资源下限或生产支持(技术预览)。

## Decisions

### D1: 包与依赖——`packages/hecate-runner`,零新增第三方依赖

workspace 新成员,依赖 `hecate-runtime`(workspace)即继承 httpx/pydantic/sqlalchemy/cryptography。HTTP 服务用 **stdlib `ThreadingHTTPServer`** + 后台 asyncio 事件循环(`run_coroutine_threadsafe` 驱动内核协程),JSON 手写序列化;不引入 fastapi/uvicorn/starlette。

*备选*:FastAPI + uvicorn。否决——预览档并发规模不需要,且会给宿主闭包增加一整层服务栈依赖,违背"业务 App 只装所需组件"的独立消费主张;step6 持久任务重估服务栈时再议。

### D2: manifest 模块移入内核,主应用单向 re-export

`hecate_runtime.manifest`(自 `src/hecate/contracts/execution/manifest.py` 迁移,含校验逻辑);主应用原路径改为 `from hecate_runtime.manifest import ...` 的兼容转发。理由:manifest 是内核的输入格式,宿主与未来外部后端都要加载它;主应用→内核方向合法(与 distribution spec 的单向规则一致),反向不成立。schema 语义不变,`手写 schema 文件为权威契约源` requirement 不受影响(YAML/OpenAPI 仍在 contracts,Python 类型只是内置映射)。

### D3: profile 目录布局与 secret 引用

```
<profile>/
  agent-manifest.json      # ArtifactManifest;files[] 摘要校验
  runner.json              # bind 地址、evidence_dir、model、tool allowlist、shutdown token ref
  identity.json            # 凭证材料(token 哈希/HMAC key)→ {principal, role: read_only, domains}
  secrets/                 # 可选:被引用的 secret 文件(不进版本库)
```

secret 一律引用(env 变量名或文件路径),`runner.json`/`identity.json` 不内联明文。启动时逐项校验(D2 摘要 + 引用可解析 + allowlist ⊆ manifest read 工具),任一失败 fail-fast(对应 spec "Missing profile artifacts fail fast")。

### D4: HTTP 面复用 step3 绑定 + 宿主扩展命名空间

核心面按 `execution-backend.http.v0_1.yaml`:`/capabilities`(能力声明,含 unsupported 项)、`POST /runs`(提交只读执行)、`GET /runs/{ref}`、`/events`(游标读取;stream 预览档返回 unsupported,单机内存游标即够)、`/cancel`(协作式:无在途外部写,直接终态)、`/artifacts`。宿主扩展放 `/healthz`、`/v1/evidence`(按 outcome/principal 过滤)、`/admin/shutdown`(需 shutdown token)。错误统一 problem+json 六类语义。默认绑定 `127.0.0.1`。

### D5: 执行装配与并发模型

启动时按 manifest `entry` 与 allowlist 编译最小固定图(模型 worker + read 工具 worker),经 `configure_kernel(RuntimeConfig(...))` 安装内核配置;每个请求在后台 asyncio 循环里独立执行,状态/事件存内存 + 追加证据;checkpoint 用 `InMemoryCheckpointStore`——**持久化即 unsupported**。并发上限 1(预览档串行),队列满返回明确错误而非排队堆积。

### D6: 身份与工具调用链

请求凭证 → `identity.json` 验证(常量时间比较)→ 服务端解析 `{principal, role, domains}`;工具 worker 调业务 API(httpx)时以服务端解析结果构造调用上下文(带 principal 与域作用域),**不转发**原始请求体里的任何自报字段。业务 API stub(`StubInventoryApi` 的 HTTP 化包装,由 harness 提供)自行执行域隔离/角色/审批规则——平台侧只做"动作允许",业务侧保有自己的状态机(方案 §一边界)。

### D7: 证据存储

`evidence_dir` 下按天滚动的 JSONL 追加文件;记录 `{ts, kind: execution|denial, principal, ref, outcome, detail}`;`/v1/evidence` 只读查询。写入失败 = fail-closed:拒绝接受新执行(SC06 的先声,完整语义 step6/7)。

### D8: SC 测试 harness——干净 venv 子进程

`tests/scenarios/tools/runner_harness.py`:`uv build --package hecate-runtime`、`--package hecate-runner` → `uv venv` 临时环境 → 安装两 wheel → 写 profile(含 write 工具的变体 manifest 供负例)→ 启动 stub 业务 API(线程内 `StubInventoryApi`)→ 子进程启动 `hecate-runner` → 返回 HTTP client 与控制句柄;工作目录设在临时目录(无仓库路径)。测试函数:`test_sc01_cold_start.py`(健康、提交只读执行、结果引用、证据查询、无控制面配置)、`test_sc02_inventory_read.py`(授权域读取成功、跨域拒绝、自报角色忽略、未知凭证拒绝+证据、无效参数先于分发拒绝、write manifest 启动失败)。`uv` 缺失时 skip 并在 CI 保证存在(主 job 补 `pip install uv`)——skip 不是永久完成态,CI 是权威。

### D9: manifest 翻转与基线登记同步

`sc_scenarios` 中 SC01/SC02:`status: implemented`、删除 `blocked_by`(一致性测试强制)、`responsible_step` 注记保留;独立基线 §5 两行改为带日期的"预览已交付(5c)"并加一行更新说明——保持快照性质(不重写历史结论,追加带日期注记)。SC08 本步不翻转(遥测/数据流全量检查属 step16 认证),在 PR 描述登记。

## Risks / Trade-offs

- [stdlib HTTP 服务安全面] → 默认绑定 127.0.0.1、请求体大小上限、JSON 深度上限;README 明示预览档不建议暴露公网。
- [干净 venv 子进程测试在 Windows 的路径/引号脆弱性] → harness 集中处理路径规范化与超时(启动轮询上限 30s),全部子进程断言收敛在 harness 一处。
- [manifest 迁移引入兼容断裂] → 主应用原路径 re-export + 全仓 grep 验证无直接深路径引用;契约 YAML 不动。
- [并发=1 被误解为产品限制] → `/capabilities` 与 README 明示预览档串行,step6 持久任务时重估。
- [skip-无-uv 被当完成] → CI 主 job 装 uv 并显式运行这两个文件;本地无 uv 时 skip 带 reason,不产生"已验收"表述。

## Migration Plan

纯新增包 + 内核加模块 + 主应用改 re-export:合入即生效,回退 = revert 单 PR。manifest 兼容转发保留至 step19 清理窗口。SC 翻转随本 change 生效;若回退,manifest 一致性测试会因 implemented 无测试而失败——即回退必须连测试一起回退,防半翻转。

## Open Questions

(无——包名(ADR-035)、绑定(step3)、SC 翻转规则(platform-scenario-pack)均已既定;owner 指派沿用暂缓决定,apply 启动时补。)
