# Proposal

## Why

演进方案 step6f(独立/受管组合的进程级验收)未交付:SC01/SC02 已有 wheel 干净安装 + 子进程 + HTTP 驱动的进程级 harness 且 CI 权威,但 durable 组合(SC03 本地批准写入与重启、SC06 本地审计不可写)与受管组合(SC04 断连授权过期、SC05 完整重连矩阵)没有进程级验收——SC05 仅登记了组件切片(delivery_acceptance/event_projection)。场景清单中 SC03—SC06 仍为 `planned`,部分已实现的切片没有 `implemented_slices` 登记。

## What Changes

- **harness 扩展**:`runner_harness` 支持 durable profile(wheel 场景带写工具与 `approval_required` 工具)、runner 子进程的硬终止与重启(同持久库新进程)、以及业务 API 副作用计数透出。
- **SC03 进程级**:wheel 安装的 durable runner 上,受保护写入经批准后执行一次;进程硬终止后重启,等待/结果可恢复,已决动作回填不重做(副作用计数断言),未知结果不重写。
- **SC06 进程级**:本地证据存储不可写时新保护动作停止(readonly 继续),证据门拒绝带类别且可查询。
- **SC04/SC05 受管切片登记**:受管组合的进程级需要平台服务进入 wheel 场景(超出本切片);已有组件切片(租约门、通道闭环、动作授权的集成测试)登记进 `implemented_slices`,整体保持 `planned` 并注明缺口。
- **清单更新**:按实际覆盖更新 `manifest.yaml` 的 SC03/SC06 状态与 SC04/SC05 切片登记;不一致测试同步钉住。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `platform-scenario-pack`:SC03/SC06 进程级验收交付(wheel + 硬重启 + 副作用计数),SC04/SC05 登记已实现切片与剩余缺口,场景清单与实现一致。

## Impact

- `tests/scenarios/tools/runner_harness.py`:durable profile 构造、进程终止/重启、业务 API 计数。
- `tests/scenarios/`:新增 SC03/SC06 进程级测试文件;`manifest.yaml` 状态与切片登记;清单一致性测试。
- 不扩展:受管组合的 wheel 进程级(需要平台服务入场景,另行切片)、PostgreSQL 参考存储的进程矩阵(存储层由 durable 包 CI PostgreSQL 用例覆盖,场景用文件库)。
