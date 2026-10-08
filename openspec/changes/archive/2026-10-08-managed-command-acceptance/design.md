# Design

## Context

命令链路的管道已存在:平台侧 `/host/pull` 将 `list_pending_commands` 的命令经 `command_for_host` 翻译进拉取载荷(`src/hecate/channel/api/managed.py`);runner 侧 CLI 绑定持久命令执行器(`__main__.py` 的 `set_command_handler`),`_apply_command` 应用后经 `/managed/host/commands/effect` 上传回执;successor 关联经 `/managed/host/attempts`;本地唤醒语义(一次性 token、revision CAS、新 attempt、幂等/过期拒绝)已有组件级测试(`packages/hecate-runner/tests/test_waiting_wake.py`)。缺口是进程级证据:SC05 harness 已具备全部素材(`managed_stack` fixture、`_serve_over_tcp` 真实 TCP 服务、`runner_harness.py` 的 wheel 构建与 `RunnerInstance.restart`、业务 API 调用计数、`HECATE_STEP6_POSTGRES_URL` 门控),但现有场景不含唤醒链。SC03 证明了**本地**唤醒(runner 自有 `/runs/{id}/resume`),不是受管命令链。

## Goals / Non-Goals

**Goals:**

- 一套真实进程验收:安装 wheel 的 runner 通过真实平台 HTTP 完成"等待→平台命令→唤醒→successor→终态→投影"全链,业务断言用调用计数。
- 套件按存储参数化(SQLite 默认/PG 门控),供 `step6-pg-process-matrix` 直接复用。
- 验收暴露缺口时做最小修复,保持命令语义不变。

**Non-Goals:**

- step7 的企业审批判定/授权语义:唤醒在本验收中是**技术 token**,输入由测试以平台命令提供,不宣称企业审批。
- 租约接管(lease takeover)、中心证据缓冲(step10)——属 6b/6f/step10 门槛。
- SC05 场景整体状态翻转:本变更只更新 slices/gaps;进程级全套(含 PG 矩阵)归 6f。

## Decisions

1. **复用并抽出 SC05 的平台栈 fixture。** 把 `test_sc05_managed_reconnect.py` 中的 `managed_stack` 与 `_serve_over_tcp` 移入共享 helper(`tests/scenarios/tools/managed_platform.py`),SC05 原测试仅改导入、断言零改动;新场景文件消费同一 helper。备选是复制 fixture——拒绝,两份装配必然漂移,SC05 的回归红线(既有断言不改)仍由原文件守住。
2. **命令走真实平台路径。** 唤醒命令经平台任务控制应用服务下达(记录进平台持久命令),由 `/host/pull` 自然投递;不直接造命令行/不绕过平台记录。备选(直插 durable 命令表)会弱化"平台投影仅在宿主事实后变化"的证明,拒绝。
3. **等待工具复用 SC03 的 `approval_required` 权限模式。** 受管 profile 已支持 claim 前持久等待并上传等待事件;唤醒命令提供 `resume`。业务工具面:等待前一次受保护写 + 等待后一次读,调用计数分别断言恰好一次。受管任务的运行查询按宿主身份隔离,等待视图经 runner 本地持久库只读观察。
4. **故障注入用真实进程手段。** runner 重启用 `RunnerInstance.stop(kill=True)` + `restart`;平台重启=停掉 uvicorn task 后用同一 app+存储重新 serve(effect/命令持久性由存储保证);effect 上传丢失=平台侧 one-shot 503 中间件(真实网络故障,非 monkeypatch)。备选(monkeypatch runner 内部)不符合"真实进程"门槛,拒绝。
5. **重复/过期命令的获得方式。** 重复命令由"平台重启后重投"与"同 command_id 同字段重发"产生并断言原回执返回;过期命令经 `issue_command` 的既有 `expires_at` 参数(无新增配置),停服期间越期后投递,断言 runner 拒绝且回执、业务零调用。
6. **PG 参数化对齐 SC05 门控。** 平台 durable 套件与 runner 本地存储分别指向独立 schema/database(保持两端状态所有权);未设 `HECATE_STEP6_POSTGRES_URL` 时 PG 参数化用例显式 skip,报告措辞不得把 skip 记为通过。
7. **验收暴露缺口的修复边界(实施中实际发生两处)。** (a) 已决动作回填不消耗 Lease:`durable.py` 新增 `is_decided_action`,引擎在租约门前窥探账本已决动作,重放路径跳过租约门——否则 successor attempt 以回填名义消耗一次性租约,饿死真实派发。(b) 同 Run 多受保护派发的有界续租等待:引擎 `_lease_refusal` 在 `CredentialError`("nonce already consumed")上等待至多 `_LEASE_REFRESH_WAIT_SECONDS`(5 s,轮询 0.05 s),给通道循环时间安装新拉取的 lease;超时后显式拒绝并留证据。两处均在 runner 侧,不改平台语义。

## Risks / Trade-offs

- [平台重启后 TCP 端口占用/时序不稳] → 重启时显式复用原端口(`serve_over_tcp(port=...)`);测试带超时与明确失败信息。
- [one-shot 503 中间件可能命中错误请求] → 精确匹配 `POST /managed/host/commands/effect` 路径且只生效一次;断言重置计数恰好为 1。
- [共享内存 SQLite 的并发提交撞车] → 平台 app 使用独立文件库(每侧自有连接),测试与 ASGI 不再共享单连接;这是实施中发现的真实故障模式("SQL statements in progress"),修复属测试基础设施。
- [wheel 构建耗时] → `ensure_wheels` 按 dist 目录复用,模块级 `wheel_dist` fixture 串行构建一次(SC03/SC05 先例;并行构建曾失败,保持串行)。

## Migration Plan

无 schema/迁移变更。回滚即回退测试与 runner 两处最小修复;不影响生产路径默认行为。

## Open Questions

(无——实施期的两个开放问题均已在任务 1.2/2.2 内解答:`issue_command` 原生支持 `command_id`/`expires_at`,无需新增 TTL 配置;runner 侧缺口以决策 7 的两处修复收口。)
