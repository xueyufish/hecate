# Step6 持久执行复核报告

## 结论与范围

本轮以 main 的 `7c22a2bb2fd111ab64c6e4052f27bf4a9735a042` 为基线，检查演进方案 Step6、对应实现和测试。修复位于独立 `codex/step6-review` 分支，未修改主检出目录，未推送。

**Step6 应记为部分完成。** 存储、worker、平台控制 API、本地 Runner 恢复和受管通道原语已有实现，但原文把部分组件测试等同于完整宿主交付。修复可确认的问题后仍不能声称已完成受管执行、独立审批等待或平台完整 checkpoint 恢复。演进方案现已列出 step6a～step6f 的实现位置、前置条件和验收方式。

## 已发现并修复

| 问题 | 原有行为／影响 | 修正与验证落点 |
|---|---|---|
| 租约代次可重用 | 同一 holder 过期／释放后复用 token，旧回调可能仍被认为合法 | SQL／内存租约保留代次，过期再领取递增；续租和释放验证 token 与期限；`test_worker_dispatch.py` 覆盖同名 holder 及旧代次 |
| 真实平台 dispatcher 没有 fencing | worker 传入 lease，但平台完成／等待路径读取最新 revision 并写状态，旧执行可能覆盖接管方 | 捕获本次 dispatch revision，状态事务验证 live lease；SQL 内联执行也经 DurableWorker 领取；真实 PlatformTaskDispatcher 迟到成功回归 |
| 重启／失败调度无效与饥饿 | 延迟任务先占满批次；活跃任务挡住后面的失联任务；崩溃不计重试上限 | 先选到期任务，再限制批次；扫描失联 running；持久记录 dispatch starts；异常回写仅影响原代次／revision；补回归 |
| Outbox 最大 ID 并非提交顺序 | PostgreSQL 较低 ID 事务晚提交时，最大 ID 游标会永久跳过事件 | 新增逐事件 `durable_outbox_receipt`，以确认集合查待投递；中继租约、持久重试／跳过；真实双事务 PostgreSQL 提交乱序与重启测试 |
| 终态／平台事件分开提交 | 崩溃可留下“状态完成但无终态证据”；平台读模型故障时无法补齐 | SQL 状态和终态 envelope 同事务；投影补齐 Run 结果；只吞明确 event_id 去重冲突，其余 IntegrityError 继续重试 |
| 动作钩子未接实际平台工具链 | Runner 有 SQL hook，平台共享装配仍没有注入，平台工具执行不受持久台账保护 | 提取可选 `hecate-durable[runtime]::SqlActionLedgerHook`，Entry／Workflow／WorkerDependencies 注入，kernel 不依赖平台存储；真实共享装配 SQL 回填测试 |
| 双台账把自己的领取当作崩溃 | SQL 领取成功后 EventStore 恢复又读取同一 CLAIMED，首次受保护执行会被错误拦截 | 当前 executor 获得 durable claim 后，事件锁内只仲裁事件日志，不将自己的领取当作未知历史；真实 SQL hook + EventStore 回归，既有工具／G2 测试复验 |
| 恢复后新模型尝试可能重做写入 | 新 Run ID 产生新 execution_id，无法保证命中旧结果 | 平台中断尝试已有受保护 Action 时停在待对账；完整 checkpoint 恢复留在 step6d，当前不承诺自动续跑 |
| 提交关联崩溃窗口 | durable 提交后平台 Task/Run 登记失败，幂等重放查不到原 Run | 提交前固化可信身份链和全部原始 ID；独立事务登记可按冻结 payload 幂等恢复；提交中断测试确保不另建 Run |
| 提交 key 只比较请求摘要 | 同名 key／同体跨主体或 workspace 可能回填他人关联 | 比较主体、workspace 和摘要；Action 重放同时比较工具名与副作用分类；跨域与工具名冲突回归。原始 key 全局唯一的局限已明确 |
| 过期／旧修订号命令先执行 | queued cancel 忽略期限，测试竟要求其 applied；唤醒成功与命令 applied 分开 | 执行前校验期限和 expected_revision；状态／输入／token 消费与 applied 原子提交；原测试改为过期无效果；命令重放忽略可变 receipt 状态，Runner cancel 重放保留原时间／回执 |
| 恢复替换业务身份 | Runner 用首个可用身份恢复，或请求元数据可替换身份范围 | 持久化服务端可信主体／数据域，恢复读取原输入并按当前配置核验；失效／撤销转待对账，拒绝伪造扩权；serial admission 防重复调度 |
| 等待任务被 startup 自动执行 | 所有非终态均加入恢复，等待和待对账可能重新调用业务 API | 只对 queued／running 调度，等待／待对账保持；恢复也执行证据准入；CLI 正确处理 EvidenceUnavailableError |
| 能力摘要与实际配置相反 | 默认 unsupported 覆盖 durable supported | 修正合并顺序；profile 能力回归和 README 同步，能力原语不等于生产认证 |
| 托管配置被 CLI 静默忽略 | 配置了 control_plane 也只启动本地宿主；通道接受竟标 running | 未接通的托管 CLI 显式失败；接受保持 queued；完整执行链见 step6a／6b，SC05 保留接收和投影切片，整体 planned |
| 接收确认丢失导致漏投／重复挤占 | 时间游标跳过未确认投递；已确认项长期重投挤占新批次 | 未确认项独立于 advisory cursor；已确认项不再占拉取批次；请求摘要包含内容，异体重投冲突；丢确认回归 |
| 多 Run 上传共用游标 | 第一个 Run 上传后，其它 Run 低序号事实被跳过；统计重复累计 | 来源／Run 独立游标，批次统计按实际数量；重启从本地日志重放，上游幂等；多 Run 上传和重复上传回归 |
| 受管事件可覆盖事实 | 仅按 event_id 跳过，异体重放未比较；旧序号可回滚终态 | 校验已接受 Task/Run 映射、治理 envelope 和完整原始事实摘要；保留源序号，按来源投影进度；终态不被旧事实重开；异体回归 |
| 留存清理越过期限／SQL readiness 漏判 | JSONL 按文件日期起点清理，删除尚未过期当天记录；SQL探测异常类别绕过证据拒绝 | 只删除整日均过期文件；SQLAlchemy 失败转显式不可写；边界日和缺表探测回归 |
| 扩大回归的 schema 注册遗漏 | 延迟导入 Event／Session 状态模型导致测试会话首次建表后再发现表，测试连续缺表 | 测试 conftest 对新注册的模型仅补建缺表，保持原会话 schema／按行清理机制 |

## 验证证据与边界

- `packages/hecate-durable/tests` 在隔离 PostgreSQL 与 SQLite 上通过；包括事务提交乱序、旧代次、过期领取、迟到失败和崩溃预算。SQLite 不支持 PostgreSQL 序列／并发提交语义的用例明确跳过，真实 PostgreSQL 已执行该用例。
- 平台 `tests/test_execution`、Runner 包测试、共享装配／动作钩子、工作流服务、SC05 通道切片及场景清单一致性进行回归；实际执行入口测试未用仅模拟 CAS 的 dispatcher 替代。
- Runtime 工具／回执／G2 恢复、kernel purity 与自包含依赖检查覆盖修改后的双台账仲裁行为。
- `ruff check`、`ruff format --check` 检查修改的 Python 文件，`mypy src/` 检查平台源码；均通过。
- 使用专用 PostgreSQL 容器及独立测试数据库执行从空库到 Alembic head；新增回执表迁移成功。未连接或修改现有业务数据库。
- 构建 runtime／durable／runner 非 editable wheels，装入独立环境，从仓库导入路径之外运行 kernel smoke、SQL 存储和 ledger bridge 导入、实际 ToolWorker 首次派发／持久结果回填、Runner CLI；确认没有完整 `hecate` 主应用安装。这只证明发行依赖闭包与基础启动，**不是**独立审批／受管执行的进程故障认证。

本轮使用本机 Python 与依赖环境，未代替 CI 的 Python 版本矩阵、全仓测试或部署负载认证。部分 Runner 故障用例模拟崩溃后数据库状态；需要真实 kill/restart 的场景继续列在 step6f，不能以模拟状态恢复冒充进程验收。

## 尚未完成的 Step6 要求

| 剩余能力 | 当前事实 | 完成方式 |
|---|---|---|
| 正式 Runner 后端与托管执行 | 固定图预览 HTTP、通道 library；CLI 尚无受管派发循环 | step5c + step6a：共享服务、正式 binding、持久接收—执行—投影及关闭链 |
| 动作时的受管授权 | LeaseGate 可单测，未接实际工具派发；CLI 已拒绝半接通配置 | step6b + step7：每个 Action 强制验证授权，真实工具计数测试到期／撤权 |
| 独立审批／输入等待 | 通用 TaskStore 有状态与命令原语，Runner 无完整产品路径 | step6c + step7：持久等待绑定、一次性合法唤醒、进程重启验收 |
| 平台完整 G2 自动恢复 | 持久 Action 钩子已接通；新模型尝试不能保持全部旧执行上下文 | step6d：checkpoint、原逻辑 Action 和真实结果恢复；现阶段保守待对账 |
| 确定性父子工作流与回调 | 手写 orchestrator 验证了父子关联／等待原语，未证明真实工作流入口或认证回调 | step6e：具名 adapter／节点、认证回调、跨域／迟到／重启测试 |
| 独立／受管进程故障矩阵 | SQL 核心及源码测试已有，wheel smoke 未运行全组合故障 | step6f：安装制品 + PostgreSQL + 真实进程控制，分别登记 SC 场景完成状态 |

这些剩余要求保留在 Step6，不能都推给 Step7／Step16。Step7 提供身份／策略／审批规则，Step6 必须让相应状态和执行事实可靠保存、恢复及投影。先实现共享执行消费者再冻结接缝，不为本轮补通用调度框架、第二套治理引擎或固定厂商依赖。

## 合入与升级

1. 按现有流程审阅并合入 `codex/step6-review`，本轮未推送／合并。
2. 平台先执行 Alembic 升级至包含 `durable_outbox_receipt` 的 head，再启动新 relay／worker；旧 outbox 可幂等重放一次，不删除原事件。现有 poison 事件的重放会重新经过有界失败判定。
3. 独立宿主先 drain、备份本地状态，再创建新增表；`create_schema()` 只补缺表，不处理任意旧列变更。宿主在自己的 schema 中使用新回执表，不依赖平台 Alembic 管理表。
4. 中断且含受保护 Action 的平台任务继续待对账，不能直接手改 queued 以“恢复”；待 step6d 有原逻辑动作恢复证据后再改变该默认。
5. 按 step6a～step6f 补剩余交付，分别更新场景清单／能力证据；整体验收后才恢复 Step6 完成标记。
