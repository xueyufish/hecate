# Tasks — step5e-durable-approval-continuation

## 1. 引擎侧等待信号

- [x] 1.1 在 hecate-runtime `errors` 模块定义 `ApprovalWaitSignal(BaseException)`(携带工具名、参数、风险级别、上下文标识),并审计工具循环/入口服务的既有 `except` 块确保信号不被改写或吞掉;验证:定向测试——信号穿过工具循环与入口服务后保持类型,`_stop_category` 映射为 `waiting_approval`,终态信封 state=failed、stop_category=waiting_approval,不落 `needs_review`
- [x] 1.2 实现 durable-wait 审批回调变体(无同步答复者时抛出 `ApprovalWaitSignal`;启用开关经任务派发装配注入,默认不装配);验证:未启用时行为与现状一致(同步询问/fail-closed 拒绝),启用时抛信号的装配级测试

## 2. 停车与 pending 绑定

- [x] 2.1 dispatcher 捕获 `ApprovalWaitSignal` 并转换为 `TaskWaitingSignalError(RESUME, contract_ref)`:先铸造 pending `ApprovalBinding`(`task_scope` 绑任务、参数摘要、主体指纹、期限),再 `_park` 写 `WAITING_APPROVAL`(等待契约携带 `approval_ref`/工具名/参数摘要);任一写入失败转任务失败终态;验证:停车后任务状态、等待契约、账本 pending 绑定三者齐备的断言测试 + 写入失败 fail-closed 反例
- [x] 2.2 崩溃窗口与唤醒续跑路由:Run 终态(waiting_approval 类别)+ 等待未消费 → 回填 fail-closed 失败;等待已消费(授权唤醒)→ 经原生续跑门在原 Run/引擎会话续跑(门为该类别放行终态拒绝,其余准入保留;门拒绝转 reconciliation);非 waiting_approval 终态回填行为不变;验证:三类确定性反例(无契约停车回填失败、授权唤醒续跑同会话、普通失败回填不变)

## 3. 唤醒授权门

- [x] 3.1 `task_control._wake_waiting` 的 RESUME 分支增加审批决定门:按等待契约 `approval_ref` 查账本——pending/缺失 → 拒绝且不消费 token;rejected → 消费 token 并转 failed(approval_rejected);approved → `check_binding`(参数摘要/主体/期限/撤销/可信时间)通过才消费 token、合并决定摘要入持久输入、重排队;载荷自报 approved 不授权;验证:六类唤醒(无决定/拒绝/通过/过期/撤销/参数变化)+ 重复 resume 与跨 workspace resume 的回执与 token 状态断言
- [x] 3.2 `provide_input` 分支与 chat 路径回归不变;验证:既有 task-control 与 chat 审批测试全绿(不改动断言)

## 4. 续跑消费

- [x] 4.1 续跑尝试的审批回调内联后端装配为持久绑定消费者:调用与绑定上下文匹配时按 `approval_ref` 走既有 `consume`(四态),成功才执行、不匹配的新受保护调用再抛等待信号、conflict/expired/revoked 按既有停止语义;唤醒不消费、消费绑定动作身份;验证:续跑后受保护动作恰执行一次(合成工具副作用计数)、重复唤醒/重试不二次消费、模型收到真实回执(确定性假模型断言续跑轮上下文含回执)
- [x] 4.2 续跑完成语义:内核从 checkpoint/事件尾部恢复通道状态(`resume_interrupted` 语义),停车前已完成动作凭会话键恢复不重复执行,续跑终态回答携带回执推导值;验证:确定性续跑断言(恢复上下文含真实回执、终态内容正确、副作用计数不变)

## 5. 真实进程重启证据(PG)

- [x] 5.1 worker 子进程重启 E2E:子进程 A(真实引擎+脚本化 provider)认领→策略审批停车→真实终止;账本 decide(approved)+ `resume` 后子进程 B(全新解释器/装配、同 PG、会话快照 PG 后端)经原生续跑门续跑至终态;断言副作用恰一次(跨进程计数文件)、授权唤醒续跑同 Run、绑定消费、终态回答携带真实回执值;验证:PG 专用模块经 `HECATE_STEP6_POSTGRES_URL` 实跑通过(19.3s),无 URL 显式 skip
- [x] 5.2 将新 PG 模块纳入 CI PG 作业(与既有 TCP/MCP/Workflow 证据模块并列):test_approval_wait_park.py + test_durable_approval_restart.py;验证:CI 配置 diff 审查 + 本地按 CI 命令复跑通过

## 6. 真实模型记录与文档

- [x] 6.1 真实模型 durable 变体:E2E 子进程显式 opt-in 并核对所选 provider 的凭据；断言终态成功、原 Run/会话、原批准/动作、恢复模型输入中的真实回执及正确结果引用，不规定自然语言措辞。保留 GLM 原观察，复核后补采独立记录，失败也保存已发生事实；不将本场景作为完整 rubric 重跑或生产认证，gate=false、成本未知。实际命令、结果和范围见 review.md。
- [x] 6.2 文档如实更新:演进方案 step5e 状态行(持久审批/重启证据闭合,余项保留)、执行机制矩阵、ADR-036 支持边界;验证:文档 diff 审查,链接可达

## 7. 验证收口

- [x] 7.1 静态门:`ruff check src/ tests/ packages/` 0 错误、`ruff format --check` 1592 文件已格式化、`mypy src/` 637 源文件零错误;`openspec validate --strict` 通过
- [x] 7.2 定向回归:新增/改动模块全绿(test_approval_wait_park 12 + E2E 确定性/真实双模式);受影响域 2198 passed 0 failed(419.99s);PG 临时容器(端口 55441)验收后删除
- [x] 7.3 宽回归:2553 passed, 46 skipped, 0 failed(555.21s;首跑一次导入期文件系统瞬时超时,单目录复跑无复现后完整复跑干净);verification.md 记录命令、结果、跳过项与实施修正

## 8. 复核修正

- [x] 8.1 复现并修正原审批引用被重铸、跨 Task 绑定唤醒、拒批 Run 投影缺失和等待写入失败被重排队；反例先失败、修正后通过。
- [x] 8.2 审批与业务派发使用同一服务端动作 ID，保留原审批；验证消费后、领取前真实进程崩溃的同动作重绑定，及相同参数新动作的再次审批。
- [x] 8.3 修正恢复时将事件版本误用为执行步数的缺陷；独立反例由实际三步恢复成五十八步，修正后保持实际步数，PG 停车前成功写入恢复零重复。
- [x] 8.4 复核跨进程在线权威时钟重锚、授权边界与真实模型失败留证；补齐受影响域和 PG 验证、文档及 review.md。
