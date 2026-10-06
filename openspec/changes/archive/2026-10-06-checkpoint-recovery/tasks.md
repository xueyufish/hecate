# Tasks

## 1. 宿主 checkpoint 持久化

- [x] 1.1 `hecate-durable`:新增 `SqlCheckpointStore`(CheckpointStore ABC 的 SQL 实现,`checkpoint` 表,坏行/缺失返回 None),注册进 `create_schema`。验证:单测覆盖 save/load/list、坏行回退、并发写取最新。
- [x] 1.2 `engine.py`:durable 下装配 `SqlCheckpointStore`(替代 InMemory);session id 稳定化为任务级(`runner-task:<task_id>`);恢复入口(`resume`/`resume_managed`)在同一 attempt 上以 `resume_value` 恢复,checkpoint 缺失回退 replay;WAITING 唤醒新 attempt 不复用旧 checkpoint。验证:engine 单测覆盖会话稳定性与恢复选择。

## 2. 平台恢复分支

- [x] 2.1 `task_dispatcher.py`:中断尝试含受保护动作时先查 engine 会话 checkpoint——可用则以 `resume_value` 恢复原 Run 执行(台账 hook 续仲裁);不可用保持 `reconciliation_required`。验证:dispatcher 单测覆盖两分支。

## 3. 闭环测试

- [x] 3.1 宿主恢复链:保护动作 succeeded 落账后崩溃(持久状态模拟)→ 重启恢复从 checkpoint 继续 → 已决动作零重复业务调用、任务收敛终态。验证:集成测试通过(业务 API 计数断言)。
- [x] 3.2 回退链:无 checkpoint 的中断任务按 replay 恢复,台账仲裁不变;claimed 写类仍停待对账。验证:集成测试通过。
- [x] 3.3 平台恢复链:有 checkpoint 恢复原 Run;无 checkpoint 保持待对账。验证:平台侧测试通过。
- [x] 3.4 既有回归:runner/durable/execution/受管三件套全绿。验证:scoped pytest。

## 4. 验证与文档

- [x] 4.1 更新 runner README(恢复语义)与演进方案 step6d 状态标注;能力声明 `long_task_recovery` 从 unsupported 更新为 durable supported。验证:文档与代码一致。
- [x] 4.2 运行 ruff check、ruff format --check、mypy src/ packages/;受影响 pytest 全绿。验证:本地门禁零错误。
