# Step6 主线追加复核

## 基线与结论

基线：`main` / `origin/main` 的 `b73af85`（PR #230）。本轮修正位于 `fix/step6-followup-review`，OpenSpec change 为 `step6-run-evidence-consistency`，尚未提交或推送。本报告补充前轮复核，不把尚未交付的能力视为已完成。

**Step6 继续保持部分完成。** 持久任务、动作账本、独立宿主、平台投影和共享执行的分层可保留。本次修复已交付查询面和取消回执的一致性；不改变组件可替换、语言中立协议或平台不拥有后端 checkpoint 的原则。

## 发现与修正

| 缺陷 | 修正 | 回归证据 |
|---|---|---|
| 持久事件最多返回一页，正式 HTTP 却总是 `has_more=false`，客户端可能只取得首批证据 | SQL 多读一条，按实际 cursor 判断后续内容；缺口同样占分页容量；正式与兼容接口传递真实分页信息 | `test_contract.py` 完整读取超过两页事件，无重复、无截断；`test_event_queries.py` SQLite/PostgreSQL 缺口分页 |
| 兼容事件接口把 queued、等待、待对账一律当成 terminal，完成任务的首个未耗尽页也会提前关闭 | 仅成功/失败/取消且本页已耗尽时声明终态 | 等待事件查询及多页终态查询 |
| 重启后旧 Run 直接继承 Task 当前状态，原等待尝试可能变成后续尝试的成功状态 | 当前 Run 使用 Task 状态；历史 Run 只读取同完整 Run/source 的状态事实，缺证据为 unknown | `test_waiting_wake.py` 原等待与新成功尝试重启后分别查询；SC03 安装制品真实进程 |
| 原等待 Run 可显示已消费 token，甚至可能取得新尝试的等待 token | 等待视图要求当前 Run 引用匹配且 token 未消费 | 唤醒前后及重启后的旧 Run 不返回可消费 wait |
| 成功/失败 Run 重启后丢失事件产物引用和原错误 | 由成功事实恢复已持久事件的产物引用，由自身终态事件恢复错误 | 正式契约成功/失败重启前后状态详情和产物一致 |
| 幂等重放在重启后改变 received_at 并丢失 idempotency_key | 原时间取 SubmissionRow，key 取原提交关联，不借用 Task 最新更新时间 | 成功/失败后重启，再提交同请求返回逐字段相同回执 |
| 重复取消生成多个命令，却只保存最后一个 command ID，旧 requested 回执永不收敛 | 在执行 loop 内串行选择和登记命令，重复运行中取消共用一个待处理命令；取消终态与 applied 回执原子提交 | 实际阻塞业务写期间重复取消；禁用事务后的单独 applied 更新仍可正确收敛 |
| 重启后取消可查询但无内存执行的已完成 Run，request_cancel 抛 KeyError；后续取消还可能修改已有 applied 回执 | 无活动执行时返回拒绝；结束后的新请求使用新拒绝回执，保留旧 applied | 正式契约重启后取消；实际取消完成后再次请求 |

初始三个负例失败于事件截断、缺少分页标记和等待终态错误；追加重放/取消负例失败于回执时间/key 变化和不同取消命令。修复没有放宽身份、重做业务写入或用忽略失败替代验收。

## 与 Step6 各切片的核对

| 范围 | 本轮核对与证据 | 仍需关闭 |
|---|---|---|
| Task/Run、SQL worker、fencing、outbox | 平台 execution 回归；durable 的 SQLite/PostgreSQL 故障与领取/晚到结果矩阵；新 Run/source 查询隔离 | 完整 PostgreSQL 宿主故障组合 |
| step6a 受管接收/投影 | 现有受管环路、重投、历史流补传和投影回归通过；接收仍仅表示 queued | Runner wheel 对接真实平台 HTTP，确认丢失、断连和重启组合 |
| step6b 动作时授权 | 现有真实派发拒绝、Lease 身份/作用域/期限及未知动作回归通过 | 与 step7 接通工具级范围、合法企业审批、撤权/断连窗口、多写续租 |
| step6c 等待/命令 | owner/token、并发唤醒、前序动作回填、取消回执及 SC03 本地进程验证 | 受管命令下发、新 attempt 平台关联；owner/token 技术唤醒不能代表企业审批 |
| step6d 恢复 | Runner attempt checkpoint、定义漂移、未知结果回归通过；平台仍拒绝把聊天历史当 native continuation | 平台共享执行的真实原生续跑与冻结 Action 关联 |
| step6e 工作流回调 | 真实 REST 回调 workspace/子引用/终态/一次性 token 负例通过 | 确定性 workflow 节点或具名 adapter 的真实父子执行与各自重启 |
| step6f 验收 | wheel SC03 终态写、审批等待和历史 Run 重启视图通过；SQL 两方言另行验证 | 完整受管/PostgreSQL 宿主矩阵、未知外部写、中心缓冲/保留策略 |

G2 和 SC03～SC06 的整体认证保持原门槛。尚未交付的功能继续拒绝或标注未认证，不能据本轮回归改成“全部完成”。

## 验证与限制

- 第一轮 Runner、durable、平台 execution 和工作流回调：`705 passed, 26 skipped`。
- 完成提交/取消读侧修正后的 Runner、durable、事件中继、分层、kernel purity 和安装制品 SC03：`228 passed, 1 skipped`。
- 独立 PostgreSQL 参考容器与 SQLite 的同一 durable 故障/查询测试：`79 passed, 1 skipped`。首次未装 postgres extra 时数据库用例在 setup 报缺少 psycopg；安装项目已声明的 extra 后全部通过，初次结果不算成功。
- 全范围 `mypy src/ packages/`：`892` 个源文件通过；ruff check、format、OpenSpec strict 和 diff check 通过。最终 Runner 回归和详细命令见 change 的 `verification.md`。

测试解释器为本地 Python 3.14.6，不替代 CI Python 3.12。本轮未重跑全应用套件、未验收完整受管 wheel+平台 HTTP/PG 宿主组合，也未认证真实供应商模型或企业审批。PostgreSQL 仅使用本轮专用数据库，验证后删除专用容器。

## 升级与回退

无新增表或 migration。EventPage 新字段有默认值；公开 HTTP Schema 已包含 has_more。历史 Run 保留原尝试事实，缺事实时不补造成功。升级前 drain 本地宿主并保留 Submission/事件/命令账本；回退不能恢复旧 Run 借用新 Task 状态、截断事件或改写已应用命令的行为。

## 2026-10-07 Step6 补充验收

本轮按 `complete-step6-recovery-gates` 的实施顺序完成了持久命令闭环、既有 continuation/replay 保证复核、真实工作流父子回调、受管 Action 派发授权检查，以及 clean-installed Runner 对接平台 HTTP 的 SC05 切片。SC05 全场景测试曾在本机 SQLite 配置下通过（`9 passed`），包含接受响应丢失后重投、真实业务 API 调用计数、平台 Run 投影、Runner 重启/重连和多个 Run 的事件补传。step6a 的 HTTP/wheel 门槛因此标记完成；这不等于 SC05 场景认证完成。

PostgreSQL 完整宿主故障矩阵仍未通过。本机 Windows 执行环境中，pytest 主进程可连接临时 PostgreSQL，但 clean-installed Runner 子进程连接 Docker 发布的 localhost PostgreSQL 时被系统拒绝（`Permission denied (10013)`）；后续 durable PostgreSQL 专项尝试未能取得稳定的测试报告，不能记为通过。用于验证的临时数据库和角色已删除。下一轮应在 Linux CI/开发环境配置独立 `HECATE_STEP6_POSTGRES_URL` 和 `DURABLE_TEST_POSTGRES_URL`，运行 PostgreSQL 参数化的 Runner 进程验收与 durable 故障注入，覆盖已接受未执行、进程 kill/restart、外部写成功但回执丢失、迟到终态、重复命令、平台重启和宿主失联，再据结果关闭 step6f。

最终提交前在沙箱外重跑后，`ruff check src/ tests/ packages/` 与 `ruff format --check src/ tests/ packages/` 通过；`mypy src/ packages/` 对 892 个源文件通过；OpenSpec strict 校验通过；受影响测试 `53 passed`，包括 SQLite SC05 安装制品/HTTP 场景。首次 mypy 发现的 nullable wait-record 测试错误已修复。之前在受限环境遇到的 uv 缓存写入和 OpenSpec realpath `EPERM` 属于执行环境限制，已通过在沙箱外执行验证解决。PostgreSQL Runner 子进程故障矩阵仍未通过，不能由这些检查替代；Step6 总体保持部分完成。

## 2026-10-08 状态记录修正

对 `main`（`9b9dae5`）的代码核对发现上文 2026-10-07 小节所引 `complete-step6-recovery-gates` 的两处完成声明超出代码与验收证据，另有一处方案门槛文本滞后。本节登记更正；不改写归档勾选，注记已追加于该 change 的 `tasks.md`。

| 记录 | 代码事实 | 更正 |
|---|---|---|
| Task2"完成内置 continuation" | `src/hecate/execution/task_dispatcher.py` 对含受保护动作的中断转 `reconciliation_required` 并以新 attempt 重放（注释明确：当前入口启动新模型执行，无法延续原动作身份）；`src/hecate/execution/builtin.py` 中断标记 `continuation controls are unsupported` | 实际交付为冻结动作关联 + 已决结果回放 + 保守待对账；平台原生 continuation 未实现，演进方案 step6d 门槛维持 |
| Task3"接入真实工作流父子等待" | 生产代码仅有消费侧：`src/hecate/execution/task_control.py` 核实子任务事实后唤醒、`src/hecate/channel/api/tasks.py` 回调端点；全局检索无提交子任务并持久等待的生产节点/adapter；`tests/test_execution/test_worker_integration.py::test_orchestration_child_wait_and_wake` 经 monkeypatch 替换 `PlatformTaskDispatcher._execute` 制造等待 | 生产侧确定性父子 adapter 未实现，演进方案 step6e 门槛维持；monkeypatch 编排只证明原语 |
| 方案 step6c 关闭门槛文本 | 受管命令下发、宿主命令处理、效果回执上传与新 attempt 平台关联代码已随 #232 交付（`src/hecate/execution/managed_channel.py` 的 `command_for_host`/命令回执校验、`packages/hecate-runner/src/hecate_runner/managed.py` 的命令 inbox `_apply_command` 与 effect 上传端点） | 方案 step6c 已追加修正行：剩余门槛为真实受管进程全链验收（等待→命令→唤醒→新 attempt→终态、两端重启、确认丢失、重复/过期命令）与 step7 合法审批判定 |

**后续收口拆分（实施顺序建议，均为 Step6 自有工作，不等待 step8—19）：**

| 后续 change | 内容 | 依赖 |
|---|---|---|
| `managed-command-acceptance` | 真实受管进程全链验收（6c 技术闭环）；场景参数化设计为 PG 复用 | 本 change |
| `builtin-native-continuation` | 内置后端真实 checkpoint continuation、冻结动作关联、恢复入口重验（6d） | 本 change |
| `workflow-child-adapter` | 生产侧确定性父子任务 adapter + 两端重启/重复/迟到回调（6e） | `builtin-native-continuation` |
| `lease-renewal-policy` | 多写动作续租或拒绝策略 + 失联/过期/nonce 重放零副作用验收（6b 技术闭环） | 本 change，可与前两者并行 |
| `step6-pg-process-matrix` | CI 配置 `HECATE_STEP6_POSTGRES_URL` 跑宿主矩阵（6f） | `managed-command-acceptance` 及其余实现 change |

各 change 随自身验收证据翻转对应方案条目；企业授权、审批判定、断连窗口与中心证据策略仍归 step7/10，Step6 完成状态最终收敛为"技术交付完成，剩余项逐条列 Step7/10 依赖"。本节未运行新测试，结论来自静态代码核对；后续 change 的运行证据以其各自 verification 记录为准。

## 2026-10-08 `managed-command-acceptance` 验收记录

`feat/managed-command-acceptance` 交付 step6c 关闭门槛的进程级部分。Runner wheel + 真实平台 HTTP（TCP）验收套件位于 `tests/scenarios/test_sc05_managed_wake_chain.py`（5 用例），覆盖：

- 全链 happy path——等待→平台 resume 命令→runner 一次性 token 消费→successor attempt→终态；原等待 Run 的平台投影保持 `waiting_approval`，不继承 successor 状态。
- 等待期宿主 `kill`+`restart` 后唤醒——前序受保护业务写入全链恰好一次。
- 平台 `uvicorn` 重启后命令仍投递且幂等回放——同 `command_id` 重发返回原 applied 回执，无第二次业务效果。
- effect 上传首请求 503 重试——业务计数证明无双重应用。
- 过期命令被拒绝——零 post-wake 读，本地等待 Run 保留 `waiting_approval`。

实现层发现并修补两处缺口（step6c + step6b 范围，未越界 step7）：runner 侧 `is_decided_action` 允许已决动作回填跳过 Lease gate；引擎 `_lease_refusal` 在 `CredentialError` 上加入有界等待（5 s 等待新拉取的 lease，超时显式拒绝）。

平台栈与 SC05 共享（`tests/scenarios/tools/managed_platform.py`），SC05 原断言零修改；文件型 SQLite 解决 ASGI 与测试侧并发提交撞车。PG 参数化由 `HECATE_STEP6_POSTGRES_URL` 门控复用，套件结构供 `step6-pg-process-matrix` 在 Linux CI 接入。SC05 manifest 同步追加两个 slices；场景整体保持 `planned`。Step6 整体仍为"部分完成"，本 change 不翻转总体状态。

## 2026-10-10 `builtin-native-continuation` 验收记录

PR #237（squash `8ef5be1`，经 merge queue 全绿合入）交付上表 `builtin-native-continuation` 行（6d）并关闭其门槛：平台共享执行的中断 attempt 按首派发冻结的定义摘要（工具顺序/Schema、模型、guardrail/资源引用）在原 engine session 原生续跑（Pregel `resume_value` + checkpoint 缓存/日志尾折叠），续跑前重验准入与摘要；已决动作按账本回填零业务重复，回执丢失保守停止在 `reconciliation_required`，摘要漂移与 Principal 撤销先于续跑门拒绝，等待/唤醒与续跑互斥。上方 2026-10-08 修正表的"Task2 完成内置 continuation"更正在此被取代；演进方案 step6d 条目已翻转。

**CI 首跑暴露并修复的三个缺陷**（4 个 PG 用例首次真实执行——本地 `_PG_SKIP` 此前从未运行它们）：

| 缺陷 | 修正 |
|---|---|
| dispatcher 续跑分支 fall-through：闸门通过分支置 `run = latest` 后控制流落到无条件的 `_new_attempt`，原生续跑被"换壳"成新 attempt，干净账本会重做受保护动作；摘要漂移用例此前通过正因其走拒绝分支、不触达该行 | `_new_attempt` 加 `if not resume_interrupted` 守卫 |
| SessionStateMaterializer 两层形状缺陷：`load` 把 `{"value": ...}` 存储信封原样交给 `ChannelManager.restore`，缓存恢复后 live 状态与全量日志折叠分歧，PROJECTION.EQUIVALENT fail-closed（等价守卫无 EventStore 时短路，故从未暴露）；save 按 `_` 前缀跳过通道，丢弃 logpolicy 显式可记录的 `_route`/`_dispatch` | `load` 对称解包信封（`_omitted` 占位符穿透）；save 过滤改以 `should_log_channel` 为准 |
| conftest teardown `DROP SCHEMA IF NOT EXISTS` 非法 PostgreSQL 语法，4 用例清理全部报错 | 改为 `IF EXISTS` |

另修正挂死恢复用例的编排：parked 进程在恢复窗口必须保持冻结，否则其向共享会话日志的迟到追加使 log-as-truth 比对落在移动尾部上（续跑的模型调用是新调用序号，不受 gate 影响）。顺带修复既有测试隔离地雷：`test_resume_endpoint`/`test_time_travel_endpoints` 在全局 app 上安装 stub auth/get_db 覆盖且不清理，同 xdist worker 后续测试继承 stub 身份（有效凭证 403、无效凭证 200）；本 PR 新增测试文件改变 loadfile 打包后暴露于 `test_audit_identity`，两个文件已加模块级 autouse 还原 fixture（`test_backup_api`/`test_replay_api` 一并加固）。

**证据**：5 个 PostgreSQL 并发用例（`tests/test_execution/test_builtin_continuation.py`：同 Run 续跑+迟到写 fencing、回执丢失、摘要漂移、准入顺序、等待互斥）在本地专用 PG 容器连跑稳定全绿，CI `step6-continuation-pg`（postgres:16）绿；SQLite 层受影响面回归 2092 passed/30 skipped，`tests/test_api`+audit+workspace isolation 633 passed/8 skipped；mypy 892 文件零错。本地环境注记：Docker 端口代理的 IPv6 发布口（::1）为黑洞，psycopg 无 connect_timeout 时先试 `::1` 会永久挂起，本地运行统一 `127.0.0.1`（CI Linux 不受影响）。

**边界**：迟到写者的事件日志追加不受 fencing 保护（状态写、动作账本与业务副作用均已闸）；该事件存储级 fencing 涉及平台 EventStore 与租约的接线，属另一条信任边界，归 `step6-pg-process-matrix`（6f）与 step7 撤权窗口，不归 6b（见 2026-10-10 `lease-renewal-policy` 验收记录的范围说明）；G2 保持未关闭；真实 kill/restart 进程矩阵归 `step6-pg-process-matrix`（6f）；不建设平台通用 checkpoint 引擎。Step6 总体保持部分完成（6b/6e/6f 及 step7 依赖未关）。

## 2026-10-10 `lease-renewal-policy` 验收记录

`feat/lease-renewal-policy` 交付 step6b 的技术闭环（多写动作续租或拒绝策略 + 失联/过期/nonce 重放零副作用验收）：

- **续租策略声明化**：`control_plane.lease_refresh_wait_seconds`（默认 5 s）取代引擎内硬编码等待预算，作为宿主断连/续租窗口声明进入 runner README（建议不超过一个 pull 周期的小倍数）；等待只覆盖"新租约未到达"，范围越界与身份不匹配立即拒绝。
- **重放窗口收口**：`LeaseGate.update` 在安装前拒绝已消费 nonce 的重放租约（不得替换当前租约，计入 `lease_gate_refusals`）；其余无效租约保持既有语义——照常安装、由派发边界以精确原因拒绝（CI 首跑更正：初版宽验证改变了既有安全契约，已收窄，见该归档 change verification.md 的更正记录）；nonce 消费记录持久化到宿主本地状态（`lease-consumed-nonces.jsonl`，载入时丢弃过期条目、跳过坏行），写入失败保守拒绝授权——平台无状态签发下，宿主该记录是重启后唯一的重放记忆。
- **进程级验收（SC04 技术切片）**：`tests/scenarios/test_sc04_lease_renewal_policy.py`，安装 wheel + 真实平台 HTTP，4 场景全绿——同 Run 双受保护写逐一续租（业务调用计数逐一核对、nonce 记录 ≥2 条）；平台在首次 accept 后停发 pull，第二个受保护动作在声明窗口内显式拒绝（Run 失败、拒绝后零业务调用、前序动作事实不变、失败终态仍上传投影）；租约范围外数据域立即拒绝（runner 配置放行、租约 scope 拒绝，证明拒绝来自租约层）且零业务调用；宿主重启后 nonce 记录延续、新租约正常武装（持久化不阻断合法续租）。
- **范围说明**：6d 验收边界行曾把"平台事件日志追加的 fencing"指向 6b——本 change 明确不包含该工作（平台 EventStore 与租约的接线是另一条信任边界），该行已更正指向 6f 矩阵与 step7 撤权窗口。工具级动作范围、合法审批、撤权及断连窗口的策略认证仍归 step7；租约过期对真实时钟偏移的认证归 SC04/step7（平台签发 TTL 固定，过期拒绝路径由闸门级单测与断连变体覆盖）。场景清单 SC04 追加 `implemented_slices`，场景整体保持 planned。Step6 总体保持部分完成（6e/6f 及 step7 依赖未关）。
