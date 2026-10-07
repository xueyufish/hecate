# Design

## Context

SC 场景的进程级 harness 模式已确立(SC01/SC02):`uv build` 三个 wheel → 干净 venv 安装(非 editable、无仓库路径)→ 写 profile → 子进程启动 runner → HTTP 驱动;CI 保证运行,本地跳过不算完成。缺口:durable 组合的 profile(写/审批工具 + 持久库)、进程硬终止与重启控制、业务副作用计数,以及 SC03/SC06 的场景实现与清单登记。

## Goals

- SC03/SC06 的验收从组件测试升级为 wheel 进程级:真实进程硬终止/重启,业务副作用次数与持久 Task/Action/命令回执为断言对象。

## Non-Goals

- 受管组合(SC04/SC05)的 wheel 进程级:需要平台控制面服务进入场景(平台 wheel 化、控制面依赖矩阵),超出本切片——登记已有组件切片与缺口,整体保持 planned。
- PostgreSQL 参考存储的进程矩阵:存储层语义(乱序、租约、迟到回执)已由 durable 包的 CI PostgreSQL 用例覆盖;场景 harness 用文件库,进程级语义(进程死亡 + 持久文件)不受影响。
- SC07+(制品篡改/回滚)及其余场景。

## Decisions

1. **进程控制走 `RunnerInstance.process` 的 terminate/kill + 重启。**终止用 `terminate()`(SIGTERM 语义;授权关闭路径已验证)与 `kill()`(硬杀)双档:硬杀验证"无优雅收尾时持久状态仍可信"(durable 行 + 证据 + checkpoint);重启用同一 profile 与持久库新起子进程,启动 drain 恢复。
2. **SC03 场景断言以业务 API 计数与持久回执为准。**序列:批准写入执行一次(计数 1)→ 硬终止 → 重启 → 恢复语义(已决回填不重做、计数仍 1;等待可被合法唤醒;未知结果保持待对账不重写)。写工具的"批准"在独立模式即业务 App 侧约束(无平台审批服务,SC03 定义如此);`approval_required` 工具走等待+唤醒链验证。
3. **SC06 用不可写目录注入。**evidence 目录指向不可写路径(只读目录),新保护动作被拒(带 `unwritable` 类别的门拒绝,可经 health/evidence 查询),readonly 派发继续;恢复换可写目录后保护动作恢复。
4. **清单状态诚实分层。**SC03/SC06 → `status: implemented` 且 `implemented_slices` 列出进程级切片;SC04/SC05 → `implemented_slices` 增加已有组件切片(lease_gate、managed_loop、action_authorization、delivery_acceptance、event_projection),`status` 保持 `planned`,assertions 后附进程级缺口说明。清单一致性测试随之钉住。
5. **harness 扩展保持声明式。**新增参数(`durable=True`、`business_api` 计数端点、`runner.stop(kill=True)`、`runner.restart()`)集中在 harness,场景测试保持可读;不引入平台服务依赖。

## Risks

- Windows/CI 的进程终止语义差异(SIGTERM vs TerminateProcess):harness 统一走 `Popen.terminate()`/`kill()` 并轮询退出,场景断言不依赖优雅退出路径;授权关闭路径已有 SC01 覆盖。
- wheel 构建耗时在 CI 可接受(SC01/02 已同模式);新增场景复用同一构建产物目录,不重复构建。
