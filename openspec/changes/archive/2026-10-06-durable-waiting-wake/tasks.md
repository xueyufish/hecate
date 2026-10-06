# Tasks

## 1. 等待进入

- [x] 1.1 `profile.py`:durable profile 允许 manifest 声明的 `approval_required` 工具(preview 仍拒绝);能力摘要与文档同步。验证:profile 单测覆盖两档行为。
- [x] 1.2 `durable.py`:`DurableRuntime` 增加等待进入封装 `park_wait(task_ref, run_ref, wake_kind, contract_ref, expires_at)`——生成一次性 token、状态迁移到 WAITING_* 并与等待记录同事务提交;增加读取等待记录的授权视图。验证:单测覆盖记录形状与 token 一次性字段。
- [x] 1.3 `engine.py`:派发边界的两条等待路径——`approval_required` 工具在台账 claim 后转为等待信号;业务结果 `input_required` 转为等待信号;`_execute` 捕获信号,持久化等待、状态置 waiting、串行槽释放,运行内存态如实标注。验证:engine 单测覆盖两条路径与零业务调用。

## 2. 唤醒命令面

- [x] 2.1 `durable.py`:`DurableRuntime.apply_wake(command)` 原子应用封装——命令幂等记录、期限/token/消费态校验、token 消费 + 输入合并(`apply_task_state(input_payload=..., event_run_ref=新 run, applied_command_id=...)`)、新 attempt run 重绑;校验失败置 `rejected` 并留原因。验证:单测覆盖合法应用、过期命令、错 token、重复唤醒幂等。
- [x] 2.2 `server.py`:授权唤醒路由(`POST /runs/{ref}/provide-input`、`POST /runs/{ref}/resume`),复用 server-verified 身份与 run 属主检查;回执含命令状态与原因。验证:server 测试覆盖认证、幂等重放、拒绝路径。
- [x] 2.3 调度收敛确认:唤醒后 QUEUED 任务被既有调度器拾取,以重绑后的新 attempt Run 执行;等待任务不被自动执行(既有过滤)。验证:集成断言。

## 3. 闭环测试(宿主集成)

- [x] 3.1 审批等待全链:任务执行 → 审批工具等待(零副作用)→ `resume` 唤醒 → 新 attempt Run 执行一次 → 终态与命令回执 `applied`。验证:集成测试通过。
- [x] 3.2 输入等待全链:业务返回 `input_required` → 等待 → `provide_input` 载荷合并 → 新 attempt Run 以合并输入执行一次。验证:集成测试通过。
- [x] 3.3 拒绝与重启:过期命令/错 token/重复唤醒零派发且回执显式;强制重启(持久状态模拟)后仍等待并被合法唤醒。验证:集成测试通过。
- [x] 3.4 既有回归:runner/durable/execution 受影响面全绿(waiting 过滤、串行槽、租约门不变)。验证:scoped pytest。

## 4. 验证与文档

- [x] 4.1 更新 runner README(等待/唤醒语义)与演进方案 step6c 状态标注。验证:文档与代码一致。
- [x] 4.2 运行 ruff check、ruff format --check、mypy src/ packages/;受影响 pytest 全绿。验证:本地门禁零错误。
