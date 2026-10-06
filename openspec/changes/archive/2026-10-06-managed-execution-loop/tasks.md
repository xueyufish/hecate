# Tasks

## 1. 宿主装配与身份打点

- [x] 1.1 `profile.py`:`ControlPlaneConfig` 增加 `data_domains`(非空字符串数组,默认空;重复值拒绝)与 `issuer_domain`(必填),并纳入受管身份派生;`resolve_secret_ref` 转公开供 CLI 解析通道密钥。验证:profile 校验单测见 4.1。
- [x] 1.2 `managed.py`:集中本地引用约定(issuer 常量与 task/run id 构造);`_accept_delivery` 在持久化输入中打点 `_host_identity`(principal=`managed:<trust_root>`,domains=`data_domains`);幂等摘要保持只覆盖投递内容;`register()` 容忍重复注册(409/422)后继续凭据交换,修复重连路径。验证:重复接受跨配置不冲突,打点入账(集成测试 3.2/3.3)。
- [x] 1.3 `durable.py`:`finish_run` 接受 `error` 并经 `apply_task_state(terminal_payload=...)` 同事务发 `run_terminal`(status/error)。验证:durable 终态迁移的事件流含 `task_state` + `run_terminal`(集成测试 3.1)。

## 2. 串行执行调度与恢复分流

- [x] 2.1 `engine.py`:新增 `resume_managed`(受管 issuer 校验、打点身份与当前配置一致性校验、QUEUED/RUNNING 门、串行槽与台账路径复用;配置漂移转待对账);standalone `resume()` 跳过受管 issuer;共享 `_schedule_durable_resume` 尾部。验证:集成测试 3.3/3.4 覆盖互斥与恢复。
- [x] 2.2 `managed.py`:新增 `ManagedExecutionScheduler` 串行 drain 循环(槽忙重试;`closing` 即停;证据不可用保留任务;不可恢复任务记忆化防饥饿)。验证:集成测试 3.1/3.4 覆盖串行与降级。
- [x] 2.3 `__main__.py`:受管装配(`control_plane`+durable;缺 durable 显式失败)、`_start_managed` 重连容忍启动循环、启动 drain 按 issuer 分流、关闭顺序(`_managed_drain`:停拉取/调度→诚实收敛→最终上传→释放)。验证:装配失败路径与独立模式零变化(runner 包既有 106 测试全绿)。

## 3. 闭环测试(平台侧集成)

- [x] 3.1 正常路径:投递→接受(仅 accepted,平台投影无执行状态)→串行执行→`task_state`/`run_terminal` 上传→平台投影终态与结果载荷。验证:`tests/test_execution/test_managed_execution_loop.py` 6 个集成测试通过(含串行槽、按 Run 游标、身份打点)。
- [x] 3.2 重试幂等:接受响应丢失→重投→原 Task/Run 幂等返回,无第二次执行、无重复副作用;重复确认幂等、异引用 409。验证:集成测试通过。
- [x] 3.3 重启恢复:接受后宕机(模拟持久状态)→重启恢复同一 Task/Run 并执行一次;未上传事件重启后补传,平台按 event_id 去重。验证:集成测试通过。
- [x] 3.4 断线与多 Run:断线期间降级重试、重连补传;两个 Run 事件按各自游标完整补传互不跳过;迟到/旧序号事件不回滚终态投影。验证:集成测试通过。

## 4. 验证与文档

- [x] 4.1 runner 包内测试:profile 校验(data_domains)、CLI 受管装配失败路径;既有 runner/durable/execution 回归。验证:scoped pytest 通过。
- [x] 4.2 更新 `packages/hecate-runner/README.md` 与演进方案 step6a 状态(受管闭环已装配、动作时租约/等待/恢复仍属 step6b+)。验证:文档与代码一致。
- [x] 4.3 运行 ruff check、ruff format --check、mypy;受影响 pytest 全绿。验证:本地门禁零错误。
