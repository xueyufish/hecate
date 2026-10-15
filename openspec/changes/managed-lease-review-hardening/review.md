# #251–#253 复核与修正

## 基线与范围

origin/main `d46aa4673a0ac47a5307c5ebeb61dffeb2f7e735`。#251 为此前 step5d-review-hardening 修正，#252 为其主规格同步/归档；#253 为 managed-lease-profile。按原 change 的 proposal/design/specs/tasks、主规格和演进方案 step7 逐项追踪真实消费路径。本 change 经用户授权执行 propose/apply，保留缺省 lease_policy 的历史兼容性，不启用受保护写入。

## 发现、修正与证据

| 优先级 | 发现及实际影响 | 修正与验证 |
|---|---|---|
| P1 | CLI 的审批桥只跳过引擎租约私有门；随后受管 profile 因过期租约拒绝，声明的离线审批无法执行 | 消费关联传给完整治理链，由真实审批 ledger/gate 重验；CLI 装配派发回归验证批准恰好一次，缺审批/过期/任务不匹配/未登记签发者/撤销/硬拒绝零调用 |
| P1 | 审批优先路径完全跳过租约 scope，能用本地更宽数据域执行平台未授权域；甚至空租约门也可放行 | 本地审批只能在最后已验证租约 scope、身份与策略版本边界内补位；过期不消耗 nonce，重启空门需先成功 pull；真实派发越界负例 |
| P1 | control_plane 配置后，治理 profile 对独立来源写动作也要求受管租约，破坏可选叠加语义 | provenance 由引擎传入，独立来源沿用 inner local profile；同一 CLI 装配的独立派发回归 |
| P1 | 平台登记的 max_staleness_seconds 从未用于签发，两侧较短窗口未实际生效 | 签名 exp 取 TTL 与平台窗口的较小值，宿主仍执行自身窗口；签发回归断言实际期限 |
| P1 | LeaseGate 仅在已配置窗口且有 iat 时检查可信时间，缺省窗口可因回拨延长授权；缺 iat 静默忽略窗口，坏 iat 可抛非契约异常；NaN/Infinity 可进入配置 | 所有期限采用可信时间，窗口必须有合法签发时间；两侧拒绝非有限窗口；回拨、缺失/坏 iat 和配置负例 |
| P1 | ManagedRevocationView 未在 CLI 装配，通道也不保留明确的 enrollment/trust-root 失效事实 | 记录 enrollment-inactive 并接入治理撤销底线，不能靠本地审批覆盖；独立来源不继承受管撤销；CLI/通道回归 |
| P2 | 配置指纹只覆盖模型与工具等少数字段，改治理规则、审批人、撤销、scope 或 lease_policy 不会触发漂移 | 从本地配置和身份声明及已加载执行定义计算指纹，运行期治理变更进入上报；秘密引用保持引用，不读取秘密文件内容；变更/秘密轮换回归 |
| P2 | accept 的 config-drift 409 被 tolerate 分支吞掉；任何上传成功都会清除漂移标记；401 后旧凭据一直重用 | 先识别漂移，再处理 tolerated response；仅 pull/accept 成功清除；401 清理凭据待下一次交换；MockTransport 通道回归 |
| P1 | 原迁移以 workspace_id IS :ws 比较 PostgreSQL 非空 UUID，历史重复分支无法升级 | 使用类型化、跨方言 null-safe 表达式；真实 PostgreSQL 历史重复升级通过 |
| P1 | 原迁移以最小主键当最早行，但生产主键是随机 UUID，测试却用自增整数 | 测试改真实 UUID，修复按 created_at 排序、UUID 破平局；反向 UUID 顺序升级负例及真实 PG 回归 |
| P1 | 复合唯一约束允许多个 workspace=NULL 的同位置记录，未覆盖服务支持的无 workspace 事件 | 增量迁移增加部分唯一索引，保留既有约束；真实 PG/SQLite 升级与模型约束回归 |
| P1 | 新增竞争去重仅查 event_id 就返回成功，异体/跨 workspace 同 ID 可被竞争输入当作已落库并继续折叠投影 | 比较 workspace 和除平台到达位置/接收时间外的事实，异体抛显式冲突并映射 managed 协议冲突；保证调用方事务仍可继续 |
| P2 | 原并发测试没有制造首次分配冲突；支持矩阵声称 PG 条件测试接线但实际只有 SQLite fixture | 强制首次分配碰撞；真实 PostgreSQL 双事务同步 max+1 竞争验证收敛；新增测试接入现有 PG CI job；纠正文档证据范围 |

审批 finder 同时修正最新但不匹配的记录遮蔽较早有效记录、同逻辑动作消费关联恢复问题；最终消费仍由权威 gate 校验，不把 locator 当作授权。

## #251 与 #252 核对

- #251 原复核修正保持：输出校验先于成功投影、停止关联、共享流式信封与摘要、workflow 输入/workspace。原保留的独立 workflow Run 身份缺口不改变；相应 HTTP、调度与 S12 回归纳入验证。
- #252 的 ADDED requirements 均已同步到主规格，区别仅为空白和中文标点；归档引用存在且指向正确。定位文档对独立/受管授权边界仍适用，无需为归档强行改写。

## 验证

- 使用新 worktree 的 Python 3.12，uv frozen lockfile 安装；不使用主目录 Python 3.14 环境。
- CLI 装配使用生产治理 service/profile/本地 SQL approval ledger/gate，仅替换最终业务 HTTP dispatch；不启动网络监听，验证完整授权链后的业务调用计数。
- Runner/governance 整包回归：328 passed。追加最终分支、通道和期限回归：62 passed；套件有交集，不累计。
- PostgreSQL 与 SQLite 新增位置/迁移定向回归：13 passed。最终完整迁移与位置、宿主授权组合：95 passed，另一个发布回填测试因两个 fixture 数据库名不同且误共用 URL 失败，已用正确独立 URL 重跑：1 passed。完整 upgrade head 与模型列漂移检查实际通过。
- 初次平台宽回归：611 passed、34 skipped、1 failed。唯一失败为旧测试只推进墙钟而不推进单调钟；已让两时钟同步推进以模拟真正经过授权期限，单独复验通过。
- 最终平台宽回归：612 passed、34 skipped、0 failed。范围：tests/test_execution、tests/test_migrations、HTTP G3 和 scheduling executors。条件跳过包含 JS 试点未安装、多 schema 专用样例及未设置各 PG 环境变量的条件项；本轮 PG 迁移/位置与原生续跑已另外显式执行，不把条件跳过算作通过。保留 7 条收集/测试桩 coroutine 警告。
- S12、S03、SC04、SC05 reconnect/wake chain、SC06 相关场景：50 passed、0 failed，2 条 websockets 弃用警告；进程场景此轮为 SQLite 配置。
- 既有 PostgreSQL 原生续跑验收：5 passed、0 failed，使用本轮专用容器，补验平台事件存储修正对原生恢复的兼容性。
- 最终 ruff check、ruff format --check（应用、tests、runner/governance 与变更迁移）：通过；mypy 应用和独立包：661 source files，零错误。OpenSpec change 和所审查的入口/调度/受管宿主/任务主规格 strict validate 均通过。git diff --check 通过。

复现主要命令（在 worktree 中）：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_execution/ tests/test_migrations/ tests/test_channel/test_chat_engine_g3_entry.py tests/test_services/test_scheduling/test_executors.py -q --timeout=90 -p no:cacheprovider
.venv/Scripts/python.exe -m pytest packages/hecate-runner/tests/ packages/hecate-governance/tests/ -q --timeout=90 -p no:cacheprovider
.venv/Scripts/python.exe -m pytest tests/scenarios/test_s12_autonomous_multiturn.py tests/scenarios/test_s03_approval_wait.py tests/scenarios/test_sc04_lease_renewal_policy.py tests/scenarios/test_sc05_managed_reconnect.py tests/scenarios/test_sc05_managed_wake_chain.py tests/scenarios/test_sc06_evidence_failure.py -q --timeout=120 -p no:cacheprovider
```

PG 专项：设置 `HECATE_REVIEW_POSTGRES_URL`（asyncpg）运行 `test_managed_lease_postgres_review.py`，设置 `HECATE_STEP6_POSTGRES_URL`（psycopg）运行 `test_builtin_continuation.py`。完整升级漂移与发布回填测试需各自对应的 `DRIFT_ADMIN_URL`/`DRIFT_DATABASE_URL`，不要将两个固定临时数据库名误映射为同一 URL。

## 边界与运行说明

- 新指纹算法包含授权配置，已装备 expectation 的操作员需重算并显式刷新；漂移期间历史证据上传不因此阻塞。
- 新索引迁移 `7a6e5d4c3b20` 跟随原 `daebf1a2b3c4`；已升级原迁移的数据库仍需执行新增迁移。原迁移修正只影响尚未执行的历史重复修复分支；不重写 alembic 版本。
- 本次 PostgreSQL 证据为隔离测试库的真实多事务/迁移测试，不冒充独立 worker 进程级并发或生产部署认证；真实时钟硬件漂移、完整断连故障集和旁路封闭仍属 step16。
- 高风险动作的完整在线判定与工具级风险策略认证仍开放，本轮只接通明确观察到的 enrollment/trust-root 失效；不能把持有有限租约等同于对所有高风险动作完成在线判定。平台事件位置唯一性也不等于对所有旧 owner 的迟到事实都已完成 ownership fencing，G2 不因此关闭。
- 新测试库运行于专用容器 `hecate-lease-review-pg`，不使用机器上既有业务数据库。校验结束后已移除本次临时容器及其测试数据卷。
- 修正通过分支 `feat/managed-lease-review-hardening` 交付；OpenSpec change 尚未归档。
