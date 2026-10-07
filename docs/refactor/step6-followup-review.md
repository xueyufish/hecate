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
